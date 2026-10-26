"""Adopt the existing A3 schema, or create it for a fresh installation.

Revision ID: a3_baseline
Revises:
"""
from alembic import op
import sqlalchemy as sa

revision = "a3_baseline"
down_revision = None
branch_labels = None
depends_on = None


def metadata_columns():
    return [sa.Column("id", sa.Integer, primary_key=True),
            sa.Column("title", sa.String(200), nullable=False),
            sa.Column("description", sa.Text, nullable=False),
            sa.Column("source", sa.String(40), nullable=False),
            sa.Column("source_url", sa.String(2048), nullable=False),
            sa.Column("retrieved_at", sa.DateTime, nullable=False),
            sa.Column("created_at", sa.DateTime, nullable=False)]


def upgrade():
    metadata = sa.MetaData()
    options = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
    sa.Table("encore_users", metadata, *metadata_columns(),
             sa.Column("google_sub", sa.String(255), nullable=False, unique=True),
             sa.Column("email", sa.String(320)), **options)
    sa.Table("encore_saved_searches", metadata, *metadata_columns(),
             sa.Column("user_id", sa.Integer, sa.ForeignKey("encore_users.id"), nullable=False, index=True),
             sa.Column("query_key", sa.String(64), nullable=False),
             sa.Column("filters", sa.JSON, nullable=False),
             sa.Column("snapshots", sa.JSON, nullable=False),
             sa.Column("last_viewed_at", sa.DateTime, nullable=False, index=True),
             sa.UniqueConstraint("user_id", "query_key", name="uq_encore_user_query"), **options)
    sa.Table("encore_cached_results", metadata, *metadata_columns(),
             sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
             sa.Column("filters", sa.JSON, nullable=False),
             sa.Column("page", sa.Integer, nullable=False),
             sa.Column("events", sa.JSON, nullable=False),
             sa.Column("total", sa.Integer, nullable=False),
             sa.Column("has_more", sa.Boolean, nullable=False),
             sa.Column("expires_at", sa.DateTime, nullable=False, index=True), **options)
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    # Do not stamp an unrelated or incomplete schema as the A3 baseline.
    for table in metadata.sorted_tables:
        if inspector.has_table(table.name):
            existing = {column["name"] for column in inspector.get_columns(table.name)}
            if set(table.columns.keys()) != existing:
                raise RuntimeError(f"Unexpected schema for {table.name}; review before migrating.")
    metadata.create_all(bind, checkfirst=True)


def downgrade():
    raise RuntimeError("The adopted A3 data is preserved. Restore a backup to remove the baseline.")
