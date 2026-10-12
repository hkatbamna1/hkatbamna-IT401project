import os
from datetime import timedelta

from dotenv import load_dotenv
from sqlalchemy.engine import URL


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev")
    GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID")
    GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PERMANENT_SESSION_LIFETIME = timedelta(hours=8)
    DATA_DIR = os.path.join(BASE_DIR, "data")
    SQLALCHEMY_DATABASE_URI = URL.create(
        "mysql+pymysql",
        username=os.environ.get("DB_USER", "hardikka_IT401_user"),
        password=os.environ.get("DB_PASSWORD", ""),
        host=os.environ.get("DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("DB_PORT", "3307")),
        database=os.environ.get("DB_NAME", "hardikka_IT401_db"),
        query={"charset": "utf8mb4"},
    )
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True, "pool_recycle": 280,
        "connect_args": {"connect_timeout": 5, "read_timeout": 10, "write_timeout": 10},
    }
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    CACHE_HOURS = {"jambase": 6, "setlistfm": 24}

    # External API keys / service config
    JAMBASE_API_KEY = os.environ.get("JAMBASE_API_KEY")
    SETLISTFM_API_KEY = os.environ.get("SETLISTFM_API_KEY")
    AI_SERVICE_API_KEY = os.environ.get("AI_SERVICE_API_KEY")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
    SESSION_COOKIE_SECURE = True


class TestingConfig(Config):
    TESTING = True
    SECRET_KEY = "isolated-test-session-key-not-for-production"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}


config = {
    "development": DevelopmentConfig,
    "production": ProductionConfig,
    "testing": TestingConfig,
    "default": DevelopmentConfig,
}
