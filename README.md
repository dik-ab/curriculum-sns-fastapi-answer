# curriculum-sns-fastapi-answer

FastAPI + SQLAlchemy implementation of the SNS curriculum backend.

## Local setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:socket_app --reload --port 8000
```

The default database is SQLite at `./sns_fastapi.db` so the answer app can run without extra services. Set `DATABASE_URL` if you want to use PostgreSQL.

## Test

```bash
pytest
```

## React frontend

Use the shared React SNS frontend with:

```bash
VITE_API_URL="http://localhost:8000"
VITE_SOCKET_URL="http://localhost:8000"
```
