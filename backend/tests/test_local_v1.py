import time

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
import pytest

from app import main
from app.api import get_database_connection


def register(client, username="user_one"):
    return client.post(
        "/api/v1/auth/register",
        json={
            "username": username,
            "password": "password123",
            "display_name": "测试用户",
        },
    )


def test_registration_login_settings_and_password_storage(client):
    response = register(client)
    assert response.status_code == 201
    assert "HttpOnly" in response.headers["set-cookie"]
    with get_database_connection() as conn:
        row = conn.execute(
            "SELECT password_hash FROM users WHERE username=?", ("user_one",)
        ).fetchone()
        assert row["password_hash"].startswith("$argon2id$")
        assert conn.execute(
            "SELECT token_hash FROM login_sessions"
        ).fetchone()[0] != client.cookies.get("local_session")
    assert (
        client.get("/api/v1/auth/me").json()["data"]["username"] == "user_one"
    )
    assert (
        client.patch("/api/v1/settings", json={"audio_rate": 1.4}).json()[
            "data"
        ]["audio_rate"]
        == 1.4
    )
    assert (
        client.patch("/api/v1/settings", json={"audio_rate": 5}).status_code
        == 400
    )
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "user_one", "password": "wrongpass"},
        ).status_code
        == 401
    )
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "user_one", "password": "password123"},
        ).status_code
        == 200
    )


def test_session_edit_save_history_and_user_isolation(client):
    register(client)
    response = client.post("/api/v1/sessions", json={})
    assert response.status_code == 201
    sid = response.json()["data"]["id"]
    assert client.post("/api/v1/sessions", json={}).status_code == 409
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={sid}"
    ) as ws:
        assert ws.receive_json()["type"] == "connected"
        ws.send_json(
            {
                "type": "sentence_command",
                "action": "replace_text",
                "text": "你好",
            }
        )
        assert ws.receive_json()["draft_text"] == "你好"
        ws.send_json({"type": "stop"})
        assert ws.receive_json()["type"] == "session_finished"
    assert (
        client.get("/api/v1/sessions").json()["data"][0]["draft_text"]
        == "你好"
    )
    other = TestClient(main.app)
    assert register(other, "user_two").status_code == 201
    assert other.get(f"/api/v1/sessions/{sid}").status_code == 404
    assert other.delete(f"/api/v1/sessions/{sid}").status_code == 404
    assert client.delete(f"/api/v1/sessions/{sid}").status_code == 204


def test_disconnect_saves_draft_and_empty_session_disappears(client):
    register(client)
    sid = client.post("/api/v1/sessions", json={}).json()["data"]["id"]
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={sid}"
    ) as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "sentence_command",
                "action": "replace_text",
                "text": "保存这句话",
            }
        )
        ws.receive_json()
    assert (
        client.get(f"/api/v1/sessions/{sid}").json()["data"]["status"]
        == "finished"
    )
    empty = client.post("/api/v1/sessions", json={}).json()["data"]["id"]
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={empty}"
    ) as ws:
        ws.receive_json()
        ws.send_json({"type": "stop"})
        ws.receive_json()
    assert client.get(f"/api/v1/sessions/{empty}").status_code == 404


def test_model_is_explicitly_unavailable_and_frame_errors_recover(client):
    assert client.get("/api/v1/health").status_code == 503
    register(client)
    sid = client.post("/api/v1/sessions", json={}).json()["data"]["id"]
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={sid}"
    ) as ws:
        assert ws.receive_json()["model_ready"] is False
        ws.send_bytes(b"")
        assert ws.receive_json()["code"] == "INVALID_IMAGE_FRAME"
        import cv2
        import numpy as np

        ok, frame = cv2.imencode(".jpg", np.zeros((10, 10, 3), dtype=np.uint8))
        assert ok
        ws.send_bytes(frame.tobytes())
        assert ws.receive_json()["code"] == "MODEL_NOT_READY"
        ws.send_json({"type": "cancel"})
        assert ws.receive_json()["cancelled"] is True


def test_v1_security_and_local_ui(client):
    assert client.get("/app/").status_code == 200
    assert client.post("/api/v1/sessions", json={}).status_code == 401
    assert (
        client.post(
            "/api/v1/auth/register",
            json={},
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    assert register(client).status_code == 201
    assert register(client).status_code == 409
    assert (
        client.post(
            "/api/v1/auth/register",
            json={"username": "a", "password": "x", "display_name": ""},
        ).status_code
        == 400
    )
    assert len(client.get("/api/v1/gestures").json()["data"]) == 3


def test_future_model_adapter_stability_and_commit(client, monkeypatch):
    class FakeRecognizer:
        def process(self, image):
            return [{"landmarks": []}], {"label": "你好", "confidence": 0.95}

    monkeypatch.setattr(main.app.state, "recognizer", FakeRecognizer())
    assert client.get("/api/v1/health").status_code == 200
    register(client)
    assert (
        client.patch(
            "/api/v1/settings",
            json={"stable_duration_ms": 300, "cooldown_ms": 300},
        ).status_code
        == 200
    )
    sid = client.post("/api/v1/sessions", json={}).json()["data"]["id"]
    import cv2
    import numpy as np

    ok, frame = cv2.imencode(".jpg", np.zeros((10, 10, 3), dtype=np.uint8))
    assert ok
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={sid}"
    ) as ws:
        assert ws.receive_json()["model_ready"] is True
        ws.send_bytes(frame.tobytes())
        assert ws.receive_json()["state"] == "candidate"
        time.sleep(0.32)
        ws.send_bytes(frame.tobytes())
        assert ws.receive_json()["state"] == "committed"
        assert ws.receive_json()["event"]["label"] == "你好"
        ws.send_bytes(frame.tobytes())
        assert ws.receive_json()["state"] == "cooldown"
        ws.send_json({"type": "sentence_command", "action": "undo_last"})
        assert ws.receive_json()["draft_text"] == ""
        ws.send_json({"type": "cancel"})
        assert ws.receive_json()["cancelled"] is True


def test_websocket_rejects_wrong_user_and_oversized_frame(client):
    register(client)
    sid = client.post("/api/v1/sessions", json={}).json()["data"]["id"]
    other = TestClient(main.app)
    register(other, "user_two")
    with other.websocket_connect(f"/ws/v1/recognition?session_id={sid}") as ws:
        assert ws.receive_json()["code"] == "SESSION_NOT_FOUND"
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={sid}"
    ) as ws:
        ws.receive_json()
        ws.send_bytes(b"x" * (307200 + 1))
        assert ws.receive_json()["code"] == "FRAME_TOO_LARGE"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            ws.receive_json()
        assert disconnected.value.code == 1009


def test_tracker_resets_on_no_hand_and_low_confidence():
    from app.recognition.stability import StabilityTracker

    tracker = StabilityTracker(0.8, 300, 500)
    candidate = {"label": "你好", "confidence": 0.9}
    assert tracker.update(candidate, 100)[0] == "candidate"
    assert tracker.update(None, 200, False)[0] == "no_hand"
    assert tracker.update(candidate, 400)[0] == "candidate"
    assert (
        tracker.update({"label": "你好", "confidence": 0.2}, 500)[0]
        == "rejected"
    )
    assert tracker.update(candidate, 700)[0] == "candidate"
    assert tracker.update(candidate, 1000)[0] == "committed"
    assert tracker.update(candidate, 1100)[0] == "cooldown"


def test_logout_closes_active_websocket_and_saves_draft(client):
    register(client)
    sid = client.post("/api/v1/sessions", json={}).json()["data"]["id"]
    with client.websocket_connect(
        f"/ws/v1/recognition?session_id={sid}"
    ) as ws:
        ws.receive_json()
        ws.send_json(
            {
                "type": "sentence_command",
                "action": "replace_text",
                "text": "保存",
            }
        )
        ws.receive_json()
        assert client.post("/api/v1/auth/logout").status_code == 204
        with pytest.raises(WebSocketDisconnect):
            ws.receive_json()
    assert (
        client.post(
            "/api/v1/auth/login",
            json={"username": "user_one", "password": "password123"},
        ).status_code
        == 200
    )
    assert (
        client.get(f"/api/v1/sessions/{sid}").json()["data"]["draft_text"]
        == "保存"
    )
