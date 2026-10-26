"""Index private saved snapshots for library and saved-result retrieval.

Revision ID: a4_saved_concerts
Revises: a4_retrieval
"""
from alembic import op
import sqlalchemy as sa

revision = "a4_saved_concerts"
down_revision = "a4_retrieval"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    if inspector.has_table("encore_saved_concerts"):
        expected = set("id saved_search_id title description source source_url retrieved_at created_at external_id artist venue location tour event_date search_title search_artist search_venue search_tour search_location card_data".split())
        if {column["name"] for column in inspector.get_columns("encore_saved_concerts")} != expected:
            raise RuntimeError("Unexpected schema for encore_saved_concerts; review before migrating.")
        return
    op.create_table("encore_saved_concerts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("saved_search_id", sa.Integer, sa.ForeignKey("encore_saved_searches.id"), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text, nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("source_url", sa.String(2048), nullable=False),
        sa.Column("retrieved_at", sa.DateTime, nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        sa.Column("external_id", sa.String(255), nullable=False),
        sa.Column("artist", sa.String(200), nullable=False),
        sa.Column("venue", sa.String(200), nullable=False),
        sa.Column("location", sa.String(160), nullable=False),
        sa.Column("tour", sa.String(200), nullable=False),
        sa.Column("event_date", sa.Date),
        sa.Column("search_title", sa.String(600), nullable=False),
        sa.Column("search_artist", sa.String(600), nullable=False),
        sa.Column("search_venue", sa.String(600), nullable=False),
        sa.Column("search_tour", sa.String(600), nullable=False),
        sa.Column("search_location", sa.String(480), nullable=False),
        sa.Column("card_data", sa.JSON, nullable=False),
        sa.UniqueConstraint("saved_search_id", "source", "external_id", name="uq_encore_saved_concert"),
        mysql_charset="utf8mb4", mysql_collate="utf8mb4_bin")
    for field in ("saved_search_id", "event_date", "search_location"):
        op.create_index(f"ix_encore_saved_concerts_{field}", "encore_saved_concerts", [field])


def downgrade():
    op.drop_table("encore_saved_concerts")
