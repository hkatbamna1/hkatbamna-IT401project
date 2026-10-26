"""Add the concert catalog and per-search telemetry; preserve A3 tables.

Revision ID: a4_retrieval
Revises: a3_baseline
"""
from alembic import op
import sqlalchemy as sa

revision = "a4_retrieval"
down_revision = "a3_baseline"
branch_labels = None
depends_on = None


def upgrade():
    metadata = sa.MetaData()
    # Foreign-key target already exists in A3; only the two A4 tables are created.
    sa.Table("encore_users", metadata, sa.Column("id", sa.Integer, primary_key=True))
    options = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
    concerts = sa.Table("encore_concerts", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
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
        sa.Column("event_date", sa.Date, index=True),
        sa.Column("search_title", sa.String(600), nullable=False),
        sa.Column("search_artist", sa.String(600), nullable=False),
        sa.Column("search_venue", sa.String(600), nullable=False),
        sa.Column("search_tour", sa.String(600), nullable=False),
        sa.Column("search_location", sa.String(480), nullable=False, index=True),
        sa.Column("card_data", sa.JSON, nullable=False),
        sa.UniqueConstraint("source", "external_id", name="uq_encore_concert_source_id"), **options)
    events = sa.Table("encore_search_events", metadata,
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("query", sa.String(160), nullable=False),
        sa.Column("normalized_query", sa.String(480), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False, index=True),
        sa.Column("result_count", sa.Integer, nullable=False),
        sa.Column("filters", sa.JSON, nullable=False),
        sa.Column("sort", sa.String(20), nullable=False),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("encore_users.id"), index=True), **options)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    for table in (concerts, events):
        if inspector.has_table(table.name):
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            if set(table.columns.keys()) != existing:
                raise RuntimeError(f"Unexpected schema for {table.name}; review before migrating.")
        table.create(bind, checkfirst=True)


def downgrade():
    op.drop_table("encore_search_events")
    op.drop_table("encore_concerts")
