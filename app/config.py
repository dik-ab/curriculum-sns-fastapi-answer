from functools import lru_cache
from os import getenv


class Settings:
    database_url: str = getenv("DATABASE_URL", "sqlite:///./sns_fastapi.db")
    jwt_secret: str = getenv("JWT_SECRET", "dev-secret")
    frontend_url: str = getenv("FRONTEND_URL", "http://localhost:5173")
    cookie_name: str = "sns_session"


@lru_cache
def get_settings() -> Settings:
    return Settings()
