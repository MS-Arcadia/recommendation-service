from datetime import UTC, datetime
from uuid import UUID

from arcadia_recommendation.domain.catalog.game_profile import GameProfile
from arcadia_recommendation.domain.policy.embedding import Embedding
from arcadia_recommendation.domain.preference.profile import UserPreference
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


def game_to_domain(row: GameProfileRow) -> GameProfile:
    return GameProfile(
        game_id=GameId(row.game_id),
        developer_id=UserId(row.developer_id),
        title=row.title,
        genres=tuple(row.genres),
        tags=tuple(row.tags),
        published_at=aware(row.published_at) if row.published_at is not None else None,
        is_published=row.is_published,
        purchase_count=row.purchase_count,
        embedding=Embedding({name: float(value) for name, value in row.embedding.items()}),
    )


def write_game(row: GameProfileRow, game: GameProfile) -> None:
    row.game_id = game.game_id.value
    row.developer_id = game.developer_id.value
    row.title = game.title
    row.genres = list(game.genres)
    row.tags = list(game.tags)
    row.embedding = dict(game.embedding.weights)
    row.is_published = game.is_published
    row.purchase_count = game.purchase_count
    row.published_at = game.published_at


def preference_to_domain(row: UserPreferenceRow) -> UserPreference:
    return UserPreference(
        user_id=UserId(row.user_id),
        taste=Embedding({name: float(value) for name, value in row.taste.items()}),
        owned=frozenset(GameId(UUID(raw)) for raw in row.owned),
        signal_count=row.signal_count,
        updated_at=aware(row.updated_at) if row.updated_at is not None else None,
    )


def write_preference(row: UserPreferenceRow, preference: UserPreference) -> None:
    row.user_id = preference.user_id.value
    row.taste = dict(preference.taste.weights)
    # Sorted so an unchanged set serialises to identical JSON. Without it, every upsert rewrites the column
    # with the same contents in a new order and every row looks changed to anything watching the table.
    row.owned = sorted(str(game_id) for game_id in preference.owned)
    row.signal_count = preference.signal_count
    row.updated_at = preference.updated_at


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
