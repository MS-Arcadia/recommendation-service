from datetime import datetime
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.infrastructure.config.settings import get_settings
from arcadia_recommendation.infrastructure.persistence.sql.base import Base

_DIMENSIONS = get_settings().embedding_dimensions


class GameProfileRow(Base):
    """`GAME_EMBEDDING` of ER د-۱۲, plus the popularity counter the fallback ranks on.

    Two content vectors, matching the two the domain holds. `embedding` is the sparse genre/tag map and
    stays JSONB — a few dozen named dimensions that no index would help. `dense` is the `vector` column
    ER د-۱۲ specifies, and the type is doing real work: holding it as a JSONB array instead was measured at
    **87ms of parsing alone** to answer one `/similar` over 500 games at 1024 dimensions, against 32ms for
    the arithmetic it was feeding. pgvector stores the same vector in a compact binary form, computes the
    distance in the database, and returns ten rows instead of five hundred.

    Its width is read from settings at import because `vector(n)` needs a literal — the one setting a
    restart cannot change, since a new width means a migration and a re-embedding.
    """

    __tablename__ = "game_profiles"

    game_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    developer_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    title: Mapped[str] = mapped_column(String(limits.MAX_TITLE_CHARS), nullable=False)
    genres: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    tags: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    embedding: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False, default=dict)
    dense: Mapped[list[float] | None] = mapped_column(Vector(_DIMENSIONS), nullable=True)
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    purchase_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # The partial index is what makes the sweep's embedding pass cheap: it holds only the games still
    # waiting for a vector, which is nothing at steady state however large the catalogue grows.
    __table_args__ = (
        Index("ix_game_profiles_popular", "is_published", "purchase_count"),
        Index(
            "ix_game_profiles_unembedded",
            "purchase_count",
            postgresql_where=sa_text("dense IS NULL AND is_published"),
        ),
    )


class UserPreferenceRow(Base):
    """`USER_PREFERENCE` of ER د-۱۲. `owned` is denormalised alongside the ownership table because the
    exclusion rule is read on every generation and reading it as a set beats a join returning one column.

    `history` is the ER's `interaction_history`, and it is the only column here that is not derivable from
    the others: the two taste vectors are running sums, and a sum cannot say which game contributed what.
    Keeping the actions is what lets the dense vector be rebuilt when a game is embedded after the purchase
    that should have counted it.
    """

    __tablename__ = "user_preferences"

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    taste: Mapped[dict[str, float]] = mapped_column(JSONB, nullable=False, default=dict)
    taste_dense: Mapped[list[float] | None] = mapped_column(Vector(_DIMENSIONS), nullable=True)
    owned: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    signal_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    __table_args__ = (Index("ix_user_preferences_active", "signal_count", "updated_at"),)


class OwnershipRow(Base):
    """The collaborative signal, one row per (user, game).

    Its own table rather than a column on the preference row, because the item-item query joins it to itself:
    "everyone who owns what you own, and what else they own" is a self-join, and a JSONB array cannot be one
    side of it. The composite index is ordered (game_id, user_id) because that join starts from the games.
    """

    __tablename__ = "ownerships"

    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    game_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    acquired_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # The partial index is what makes the backfill cheap: it holds only the rows still waiting for their
    # game's description, which is a handful at steady state however large the table grows.
    __table_args__ = (
        Index("ix_ownerships_by_game", "game_id", "user_id"),
        Index(
            "ix_ownerships_uncounted",
            "game_id",
            postgresql_where=sa_text("counted = false"),
        ),
    )


class RecommendationRow(Base):
    """`RECOMMENDATION` of ER د-۱۲: what the batch decided, ready for one indexed read."""

    __tablename__ = "recommendations"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    user_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    game_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "game_id", name="user_game"),
        Index("ix_recommendations_for_user", "user_id", "rank"),
    )


class OutboxRow(Base):
    """Written in the same transaction as the aggregate it belongs to — that single fact is the entire
    reliability guarantee. `id` is the event's own id, so the row and the envelope agree on the value
    consumers deduplicate by, across every retry."""

    __tablename__ = "outbox"

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    topic: Mapped[str] = mapped_column(String(128), nullable=False)
    partition_key: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    headers: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    correlation_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dead_lettered: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (Index("ix_outbox_pending", "published_at", "dead_lettered", "occurred_at"),)


class ProcessedEventRow(Base):
    """Consumer-side deduplication. Marked inside the same transaction as the effect it guards, so a crash
    between the two is impossible rather than merely unlikely — which for a purchase counter that only ever
    increments is the difference between a popularity ranking and a fiction."""

    __tablename__ = "processed_events"

    event_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
