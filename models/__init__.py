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
    concerts = db.relationship("SavedConcert", cascade="all, delete-orphan", back_populates="saved_search")
    generations = db.relationship("RecommendationGeneration", cascade="all, delete-orphan",
                                  back_populates="saved_search")


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


class ConcertFields(RecordMetadata):
    external_id = db.Column(db.String(255), nullable=False)
    artist = db.Column(db.String(200), nullable=False, default="")
    venue = db.Column(db.String(200), nullable=False, default="")
    location = db.Column(db.String(160), nullable=False, default="")
    tour = db.Column(db.String(200), nullable=False, default="")
    event_date = db.Column(db.Date, index=True)
    search_title = db.Column(db.String(600), nullable=False)
    search_artist = db.Column(db.String(600), nullable=False)
    search_venue = db.Column(db.String(600), nullable=False)
    search_tour = db.Column(db.String(600), nullable=False)
    search_location = db.Column(db.String(480), nullable=False, index=True)
    card_data = db.Column(db.JSON, nullable=False)


class Concert(ConcertFields, db.Model):
    """Shared provider facts, independent of private snapshots."""
    __tablename__ = "encore_concerts"
    __table_args__ = (
        db.UniqueConstraint("source", "external_id", name="uq_encore_concert_source_id"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )


class SavedConcert(ConcertFields, db.Model):
    """Searchable projection of one owner's exact saved concert snapshot."""
    __tablename__ = "encore_saved_concerts"
    __table_args__ = (
        db.UniqueConstraint("saved_search_id", "source", "external_id", name="uq_encore_saved_concert"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )
    saved_search_id = db.Column(db.Integer, db.ForeignKey("encore_saved_searches.id"), nullable=False, index=True)
    saved_search = db.relationship("SavedSearch", back_populates="concerts")


class SearchEvent(db.Model):
    __tablename__ = "encore_search_events"
    __table_args__ = {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"}
    id = db.Column(db.Integer, primary_key=True)
    query = db.Column(db.String(160), nullable=False)
    normalized_query = db.Column(db.String(480), nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    result_count = db.Column(db.Integer, nullable=False)
    filters = db.Column(db.JSON, nullable=False)
    sort = db.Column(db.String(20), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("encore_users.id"), index=True)


class RecommendationGeneration(db.Model):
    """Validated intelligence; ownership follows the parent private saved search."""
    __tablename__ = "encore_recommendation_generations"
    __table_args__ = (
        db.Index("ix_encore_generation_search_time", "saved_search_id", "generated_at"),
        {"mysql_charset": "utf8mb4", "mysql_collate": "utf8mb4_bin"},
    )
    id = db.Column(db.Integer, primary_key=True)
    saved_search_id = db.Column(db.Integer, db.ForeignKey("encore_saved_searches.id", ondelete="CASCADE"), nullable=False)
    user_request = db.Column(db.Text, nullable=False)
    retrieval_options = db.Column(db.JSON, nullable=False)
    source_context = db.Column(db.JSON, nullable=False)
    output = db.Column(db.JSON, nullable=False)
    prompt_version = db.Column(db.String(40), nullable=False)
    model = db.Column(db.String(120), nullable=False)
    generated_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    saved_search = db.relationship("SavedSearch", back_populates="generations")
