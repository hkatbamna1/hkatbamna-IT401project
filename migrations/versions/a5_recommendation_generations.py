"""Store private generated recommendations without changing acquired records.

Revision ID: a5_recommendation_generations
Revises: a4_saved_concerts
"""
from alembic import op
import sqlalchemy as sa


revision = "a5_recommendation_generations"
down_revision = "a4_saved_concerts"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("encore_recommendation_generations",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("saved_search_id", sa.Integer,
                  sa.ForeignKey("encore_saved_searches.id", ondelete="CASCADE"), nullable=False),
        sa.Column("user_request", sa.Text, nullable=False),
        sa.Column("retrieval_options", sa.JSON, nullable=False),
        sa.Column("source_context", sa.JSON, nullable=False),
        sa.Column("output", sa.JSON, nullable=False),
        sa.Column("prompt_version", sa.String(40), nullable=False),
        sa.Column("model", sa.String(120), nullable=False),
        sa.Column("generated_at", sa.DateTime, nullable=False),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
    op.create_index("ix_encore_generation_search_time", "encore_recommendation_generations",
                    ["saved_search_id", "generated_at"])


def downgrade():
    op.drop_table("encore_recommendation_generations")
