from datetime import datetime, timedelta, timezone
from hashlib import pbkdf2_hmac
from hmac import compare_digest
from secrets import token_hex

import jwt
from fastapi import Cookie, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from .config import get_settings
from .database import get_db
from .models import User


def hash_password(password: str, salt: str | None = None) -> str:
    actual_salt = salt or token_hex(16)
    digest = pbkdf2_hmac("sha256", password.encode(), actual_salt.encode(), 120_000)
    return f"pbkdf2_sha256${actual_salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    _, salt, expected = stored.split("$", 2)
    actual = hash_password(password, salt).split("$", 2)[2]
    return compare_digest(actual, expected)


def create_session_token(user_id: int) -> str:
    settings = get_settings()
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(days=7),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def set_session_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        get_settings().cookie_name,
        token,
        httponly=True,
        samesite="lax",
        secure=False,
        max_age=60 * 60 * 24 * 7,
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(get_settings().cookie_name, path="/")


def decode_session_token(token: str) -> int:
    try:
        payload = jwt.decode(token, get_settings().jwt_secret, algorithms=["HS256"])
        return int(payload["sub"])
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ログインが必要です") from exc


def get_current_user(
    sns_session: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if sns_session is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ログインが必要です")
    user = db.get(User, decode_session_token(sns_session))
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ログインが必要です")
    return user
