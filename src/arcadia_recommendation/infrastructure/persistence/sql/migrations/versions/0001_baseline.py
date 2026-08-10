"""baseline

Revision ID: 0001_baseline
Revises:
Create Date: 2026-08-10

The whole schema in one revision. A service whose first migration is a sequence of five is a service whose
history is noise; the interesting migrations are the ones that come after this.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "game_profiles",
        sa.Column("game_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("developer_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("genres", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("tags", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False),
        sa.Column("purchase_count", sa.Integer(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("game_id", name=op.f("pk_game_profiles")),
    )
    op.create_index("ix_game_profiles_popular", "game_profiles", ["is_published", "purchase_count"])

    op.create_table(
        "user_preferences",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("taste", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("owned", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("signal_count", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("user_id", name=op.f("pk_user_preferences")),
    )
    op.create_index("ix_user_preferences_active", "user_preferences", ["signal_count", "updated_at"])

    op.create_table(
        "ownerships",
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("game_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("acquired_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("user_id", "game_id", name=op.f("pk_ownerships")),
    )
    op.create_index("ix_ownerships_by_game", "ownerships", ["game_id", "user_id"])
    op.create_index(
        "ix_ownerships_uncounted", "ownerships", ["game_id"], postgresql_where=sa.text("counted = false")
    )

    op.create_table(
        "recommendations",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("user_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("game_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("rank", sa.Integer(), nullable=False),
        sa.Column("reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_recommendations")),
        sa.UniqueConstraint("user_id", "game_id", name=op.f("uq_recommendations_user_game")),
    )
    op.create_index("ix_recommendations_for_user", "recommendations", ["user_id", "rank"])

    op.create_table(
        "outbox",
        sa.Column("id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("aggregate_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("partition_key", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("headers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dead_lettered", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox")),
    )
    op.create_index("ix_outbox_pending", "outbox", ["published_at", "dead_lettered", "occurred_at"])

    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", name=op.f("pk_processed_events")),
    )


def downgrade() -> None:
    op.drop_table("processed_events")
    op.drop_index("ix_outbox_pending", table_name="outbox")
    op.drop_table("outbox")
    op.drop_index("ix_recommendations_for_user", table_name="recommendations")
    op.drop_table("recommendations")
    op.drop_index("ix_ownerships_uncounted", table_name="ownerships")
    op.drop_index("ix_ownerships_by_game", table_name="ownerships")
    op.drop_table("ownerships")
    op.drop_index("ix_user_preferences_active", table_name="user_preferences")
    op.drop_table("user_preferences")
    op.drop_index("ix_game_profiles_popular", table_name="game_profiles")
    op.drop_table("game_profiles")
