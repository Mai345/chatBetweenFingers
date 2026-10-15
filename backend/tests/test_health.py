from fastapi.testclient import TestClient
from app import main

client = TestClient(main.app)


def test_health_returns_503_when_model_not_ready():
    response = client.get("/api/v0/health")
    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "MODEL_NOT_READY",
            "message": "识别模型尚未准备好",
        }
    }


def test_health_returns_200_when_model_ready(monkeypatch):
    monkeypatch.setattr(main, "MODEL_READY", True)
    response = client.get("/api/v0/health")
    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "version": "0.1",
        "model_ready": True,
    }
