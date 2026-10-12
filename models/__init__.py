from datetime import datetime, timezone

from flask_sqlalchemy import SQLAlchemy


db = SQLAlchemy()


def utcnow():
    """Store UTC without timezone offsets consistently in MariaDB and SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RecordMetadata:
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text, nullable=False, default="")
    source = db.Column(db.String(40), nullable=False)
    source_url = db.Column(db.String(2048), nullable=False)
    retrieved_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)


class User(RecordMetadata, db.Model):
    __tablename__ = "encore_users"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
    google_sub = db.Column(db.String(255), unique=True, nullable=False)
    email = db.Column(db.String(320))
    searches = db.relationship("SavedSearch", back_populates="user", cascade="all, delete-orphan")


class SavedSearch(RecordMetadata, db.Model):
    __tablename__ = "encore_saved_searches"
    __table_args__ = (
        db.UniqueConstraint("user_id", "query_key", name="uq_encore_user_query"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )
    user_id = db.Column(db.Integer, db.ForeignKey("encore_users.id"), nullable=False, index=True)
    query_key = db.Column(db.String(64), nullable=False)
    filters = db.Column(db.JSON, nullable=False)
    snapshots = db.Column(db.JSON, nullable=False, default=dict)
    last_viewed_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user = db.relationship("User", back_populates="searches")


class CachedResult(RecordMetadata, db.Model):
    __tablename__ = "encore_cached_results"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
    cache_key = db.Column(db.String(64), unique=True, nullable=False)
    filters = db.Column(db.JSON, nullable=False)
    page = db.Column(db.Integer, nullable=False)
    events = db.Column(db.JSON, nullable=False)
    total = db.Column(db.Integer, nullable=False)
    has_more = db.Column(db.Boolean, nullable=False)
    expires_at = db.Column(db.DateTime, nullable=False, index=True)
