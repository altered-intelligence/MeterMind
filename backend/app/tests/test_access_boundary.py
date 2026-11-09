from fastapi.testclient import TestClient
from app.main import app, settings


def test_remote_denied_without_token(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "")
    with TestClient(app, client=("192.0.2.5", 50000)) as client:
        assert client.get("/api/bills").status_code == 403


def test_configured_token_required_even_on_loopback(monkeypatch):
    monkeypatch.setattr(settings, "api_token", "a" * 32)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/health", headers={"Authorization": "Bearer " + "a" * 32}).status_code == 200
