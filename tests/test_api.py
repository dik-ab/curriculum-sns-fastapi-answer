from fastapi.testclient import TestClient

from app.database import Base, engine
from app.main import app
from app.models import EmailVerificationToken
from app.database import SessionLocal


def reset_db() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def verify_latest_user() -> None:
    with SessionLocal() as db:
        token = db.query(EmailVerificationToken).order_by(EmailVerificationToken.id.desc()).first()
        assert token is not None
        res = client.get(f"/auth/verify-email?token={token.token}")
        assert res.status_code == 200


client = TestClient(app)


def register_login(email: str, username: str) -> TestClient:
    local_client = TestClient(app)
    res = local_client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "displayName": username.title(),
            "password": "password123",
        },
    )
    assert res.status_code == 201
    verify_latest_user()
    res = local_client.post("/auth/login", json={"email": email, "password": "password123"})
    assert res.status_code == 200
    assert "sns_session" in local_client.cookies
    return local_client


def test_post_like_follow_and_chat_flow():
    reset_db()
    alice = register_login("alice@example.com", "alice")
    bob = register_login("bob@example.com", "bob")

    post_res = bob.post("/posts", json={"content": "FastAPIからこんにちは"})
    assert post_res.status_code == 201
    post_id = post_res.json()["id"]

    timeline = alice.get("/posts").json()
    assert timeline[0]["content"] == "FastAPIからこんにちは"
    assert timeline[0]["likedByMe"] is False

    assert alice.post(f"/posts/{post_id}/likes").status_code == 204
    liked = alice.get("/posts").json()[0]
    assert liked["likeCount"] == 1
    assert liked["likedByMe"] is True

    assert alice.post("/users/bob/follow").status_code == 204
    profile = alice.get("/users/bob").json()
    assert profile["isFollowing"] is True
    assert profile["followersCount"] == 1

    conversation = alice.post("/conversations", json={"username": "bob"}).json()
    assert conversation["partner"]["username"] == "bob"
    message = bob.post("/conversations", json={"username": "alice"}).json()
    assert message["id"] == conversation["id"]

    messages = alice.get(f"/conversations/{conversation['id']}/messages").json()
    assert messages == []


def test_logout_clears_session_cookie():
    reset_db()
    alice = register_login("logout-alice@example.com", "logoutalice")

    res = alice.post("/auth/logout")

    assert res.status_code == 204
    set_cookie = res.headers.get("set-cookie", "")
    assert "sns_session=" in set_cookie
    assert "Max-Age=0" in set_cookie
