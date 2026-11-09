from app.main import app


def test_health_returns_503_when_model_not_ready(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "MODEL_NOT_READY"
    assert response.json()["data"]["database_ready"] is True


def test_health_returns_200_when_model_ready(client, monkeypatch):
    monkeypatch.setattr(app.state, "recognizer", object())
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["data"]["static_model_ready"] is True


def test_openapi_only_exposes_v1_api_routes(client):
    document = client.get("/openapi.json").json()

    assert document["info"]["version"] == "1.0"
    for path in document["paths"]:
        assert path.startswith("/api/v1/")
