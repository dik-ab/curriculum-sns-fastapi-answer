import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from subprocess import TimeoutExpired

import httpx
import socketio


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_health(base_url: str) -> None:
    deadline = time.time() + 10
    while time.time() < deadline:
        try:
            if httpx.get(f"{base_url}/health", timeout=0.5).status_code == 200:
                return
        except httpx.HTTPError:
            time.sleep(0.1)
    raise AssertionError("server did not become ready")


def latest_verification_token(db_path: Path) -> str:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "select token from email_verification_tokens order by id desc limit 1"
        ).fetchone()
    assert row is not None
    return str(row[0])


def register_verify_login(client: httpx.Client, db_path: Path, email: str, username: str) -> None:
    res = client.post(
        "/auth/register",
        json={
            "email": email,
            "username": username,
            "displayName": username.title(),
            "password": "password123",
        },
    )
    assert res.status_code == 201
    token = latest_verification_token(db_path)
    assert client.get(f"/auth/verify-email?token={token}").status_code == 200
    assert client.post("/auth/login", json={"email": email, "password": "password123"}).status_code == 200
    assert "sns_session" in client.cookies


def test_socketio_chat_delivers_new_message(tmp_path: Path):
    port = free_port()
    base_url = f"http://127.0.0.1:{port}"
    db_path = tmp_path / "sns_realtime.db"
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{db_path}",
        "JWT_SECRET": "realtime-test-secret",
        "FRONTEND_URL": "http://localhost:5173",
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]),
    }
    sio: socketio.Client | None = None
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:socket_app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_health(base_url)
        alice = httpx.Client(base_url=base_url)
        bob = httpx.Client(base_url=base_url)
        register_verify_login(alice, db_path, "alice@example.com", "alice")
        register_verify_login(bob, db_path, "bob@example.com", "bob")
        conversation = alice.post("/conversations", json={"username": "bob"}).json()

        received: list[dict] = []
        sio = socketio.Client()

        @sio.on("newMessage", namespace="/chat")
        def on_new_message(data):
            received.append(data)

        cookie_header = "; ".join(f"{key}={value}" for key, value in alice.cookies.items())
        sio.connect(base_url, namespaces=["/chat"], headers={"Cookie": cookie_header})
        sio.emit("joinConversation", {"conversationId": conversation["id"]}, namespace="/chat")
        sio.emit(
            "sendMessage",
            {"conversationId": conversation["id"], "content": "リアルタイムで届く"},
            namespace="/chat",
        )

        deadline = time.time() + 5
        while time.time() < deadline and not received:
            sio.sleep(0.1)

        assert received
        assert received[0]["conversationId"] == conversation["id"]
        assert received[0]["content"] == "リアルタイムで届く"
    finally:
        try:
            if sio is not None:
                sio.disconnect()
        except Exception:
            pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)
