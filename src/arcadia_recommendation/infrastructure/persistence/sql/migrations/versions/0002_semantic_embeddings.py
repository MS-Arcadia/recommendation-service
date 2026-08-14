"""semantic embeddings

Revision ID: 0002_semantic_embeddings
Revises: 0001_baseline
Create Date: 2026-08-13

Adds the `vector` columns ER د-۱۲ specifies, alongside the sparse JSONB ones rather than replacing them:
both ranking spaces stay live so SCORING_BACKEND can be flipped without a migration in either direction.

The extension is created here as well as in the platform's Postgres init script, because that script only
runs on an empty data directory — a stack that has been up since before this revision would otherwise fail
on the first column. `recommendation_user` owns its database, which is enough for a trusted extension.

The vector width is read from the same setting the model reads, so the column and the ORM agree by
construction. It is baked in as a literal: changing EMBEDDING_DIMENSIONS afterwards needs a new revision and
a re-embedding of the catalogue, since the stored vectors were produced by the old model.
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql

from arcadia_recommendation.infrastructure.config.settings import get_settings

revision = "0002_semantic_embeddings"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None

_DIMENSIONS = get_settings().embedding_dimensions


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column("game_profiles", sa.Column("description", sa.Text(), nullable=False, server_default=""))
    op.add_column("game_profiles", sa.Column("dense", Vector(_DIMENSIONS), nullable=True))
    # Partial: it holds only the games still waiting for a vector, which is nothing at steady state
    # however large the catalogue grows.
    op.create_index(
        "ix_game_profiles_unembedded",
        "game_profiles",
        ["purchase_count"],
        postgresql_where=sa.text("dense IS NULL AND is_published"),
    )

    op.add_column("user_preferences", sa.Column("taste_dense", Vector(_DIMENSIONS), nullable=True))
    op.add_column(
        "user_preferences",
        sa.Column(
            "history",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
    )


def downgrade() -> None:
    op.drop_column("user_preferences", "history")
    op.drop_column("user_preferences", "taste_dense")
    op.drop_index("ix_game_profiles_unembedded", table_name="game_profiles")
    op.drop_column("game_profiles", "dense")
    op.drop_column("game_profiles", "description")
