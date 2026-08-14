from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.policy.embedding import DenseEmbedding, Embedding
from arcadia_recommendation.domain.preference.profile import UserPreference
from arcadia_recommendation.domain.preference.signal import SignalRecord
from arcadia_recommendation.domain.recommendation.recommendation import (
    Recommendation,
    RecommendationSource,
)
from arcadia_recommendation.domain.shared.ids import GameId, RecommendationId, UserId
from arcadia_recommendation.infrastructure.persistence.sql.models import (
    GameProfileRow,
    RecommendationRow,
    UserPreferenceRow,
)


def aware(moment: datetime) -> datetime:
    """Postgres hands back what it was given, and a driver round-trip can drop the offset. A naive datetime
    crossing back into the domain would compare wrongly against every aware one, so UTC is assumed here rather
    than discovered later in a sort."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)


def dense_to_domain(values: list[float] | None) -> DenseEmbedding:
    """NULL and the empty vector are the same fact — no embedding yet — but only one of them is storable:
    pgvector rejects a zero-dimension value, so an unembedded game is a NULL column rather than an empty
    one."""
    return DenseEmbedding.empty() if values is None else DenseEmbedding.of(values)


def dense_to_column(dense: DenseEmbedding) -> list[float] | None:
    return None if dense.is_empty else list(dense.values)


def game_to_domain(row: GameProfileRow) -> GameProfile:
    return GameProfile(
        game_id=GameId(row.game_id),
        developer_id=UserId(row.developer_id),
        title=row.title,
        genres=tuple(row.genres),
        tags=tuple(row.tags),
        description=row.description,
        published_at=aware(row.published_at) if row.published_at is not None else None,
        is_published=row.is_published,
        purchase_count=row.purchase_count,
        embedding=Embedding({name: float(value) for name, value in row.embedding.items()}),
        dense=dense_to_domain(row.dense),
    )


def write_game(row: GameProfileRow, game: GameProfile) -> None:
    row.game_id = game.game_id.value
    row.developer_id = game.developer_id.value
    row.title = game.title
    row.genres = list(game.genres)
    row.tags = list(game.tags)
    row.description = game.description
    row.embedding = dict(game.embedding.weights)
    row.dense = dense_to_column(game.dense)
    row.is_published = game.is_published
    row.purchase_count = game.purchase_count
    row.published_at = game.published_at


def preference_to_domain(row: UserPreferenceRow) -> UserPreference:
    return UserPreference(
        user_id=UserId(row.user_id),
        taste=Embedding({name: float(value) for name, value in row.taste.items()}),
        taste_dense=dense_to_domain(row.taste_dense),
        owned=frozenset(GameId(UUID(raw)) for raw in row.owned),
        history=tuple(_record(entry) for entry in row.history if _is_record(entry)),
        signal_count=row.signal_count,
        updated_at=aware(row.updated_at) if row.updated_at is not None else None,
    )


def write_preference(row: UserPreferenceRow, preference: UserPreference) -> None:
    row.user_id = preference.user_id.value
    row.taste = dict(preference.taste.weights)
    row.taste_dense = dense_to_column(preference.taste_dense)
    # Sorted so an unchanged set serialises to identical JSON. Without it, every upsert rewrites the column
    # with the same contents in a new order and every row looks changed to anything watching the table.
    row.owned = sorted(str(game_id) for game_id in preference.owned)
    # History is ordered, not sorted: it is a sequence of actions, and the cap keeps the newest.
    row.history = [{"game_id": str(record.game_id), "weight": record.weight} for record in preference.history]
    row.signal_count = preference.signal_count
    row.updated_at = preference.updated_at


def _is_record(entry: object) -> bool:
    return isinstance(entry, dict) and "game_id" in entry and "weight" in entry


def _record(entry: dict[str, Any]) -> SignalRecord:
    return SignalRecord(game_id=GameId(UUID(str(entry["game_id"]))), weight=float(entry["weight"]))


def recommendation_to_domain(row: RecommendationRow) -> Recommendation:
    return Recommendation(
        id=RecommendationId(row.id),
        user_id=UserId(row.user_id),
        game_id=GameId(row.game_id),
        score=row.score,
        source=RecommendationSource(row.source),
        rank=row.rank,
        generated_at=aware(row.generated_at),
        reasons=tuple(row.reasons),
    )


def recommendation_row(recommendation: Recommendation) -> RecommendationRow:
    return RecommendationRow(
        id=recommendation.id.value,
        user_id=recommendation.user_id.value,
        game_id=recommendation.game_id.value,
        score=recommendation.score,
        source=str(recommendation.source),
        rank=recommendation.rank,
        reasons=list(recommendation.reasons),
        generated_at=recommendation.generated_at,
    )
