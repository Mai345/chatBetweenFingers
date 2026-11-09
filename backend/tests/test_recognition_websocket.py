import cv2
import numpy as np
import pytest
from starlette.websockets import WebSocketDisconnect

from app.main import app


def make_test_jpeg(width=10, height=10):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    success, encoded_image = cv2.imencode(".jpg", image)

    assert success
    return encoded_image.tobytes()


@pytest.fixture
def session_path(client):
    client.post(
        "/api/v1/auth/register",
        json={
            "username": "socket_user",
            "password": "password123",
            "display_name": "连接测试",
        },
    )
    response = client.post("/api/v1/sessions", json={})
    session_id = response.json()["data"]["id"]
    return f"/ws/v1/recognition?session_id={session_id}"


def test_recognition_requires_login(client):
    with client.websocket_connect("/ws/v1/recognition") as websocket:
        message = websocket.receive_json()
        assert message["code"] == "NOT_AUTHENTICATED"

        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_json()

        assert disconnected.value.code == 1008


def test_recognition_sends_connection_settings(client, session_path):
    with client.websocket_connect(session_path) as websocket:
        message = websocket.receive_json()

        assert message["type"] == "connected"
        assert message["max_frame_size_bytes"] == 307200
        assert message["recommended_width"] == 640
        assert message["recommended_height"] == 480
        assert message["model_ready"] is False


@pytest.mark.parametrize(
    "frame_bytes",
    [
        b"",
        b"not-a-jpeg",
        b"\xff\xd8\xffbroken",
    ],
    ids=["empty", "not-jpeg", "broken-jpeg"],
)
def test_invalid_image_does_not_break_connection(
    client, session_path, frame_bytes
):
    with client.websocket_connect(session_path) as websocket:
        websocket.receive_json()
        websocket.send_bytes(frame_bytes)
        message = websocket.receive_json()

        assert message["code"] == "INVALID_IMAGE_FRAME"
        assert message["recoverable"] is True

        websocket.send_bytes(make_test_jpeg())
        assert websocket.receive_json()["code"] == "MODEL_NOT_READY"


def test_non_jpeg_image_is_rejected(client, session_path):
    image = np.zeros((10, 10, 3), dtype=np.uint8)
    success, encoded_image = cv2.imencode(".png", image)
    assert success

    with client.websocket_connect(session_path) as websocket:
        websocket.receive_json()
        websocket.send_bytes(encoded_image.tobytes())
        assert websocket.receive_json()["code"] == "INVALID_IMAGE_FRAME"


@pytest.mark.parametrize(
    "frame_bytes",
    [
        b"x" * (307200 + 1),
        make_test_jpeg(width=1281, height=720),
    ],
    ids=["message-size", "resolution"],
)
def test_oversized_frame_closes_connection(client, session_path, frame_bytes):
    with client.websocket_connect(session_path) as websocket:
        websocket.receive_json()
        websocket.send_bytes(frame_bytes)

        assert websocket.receive_json()["code"] == "FRAME_TOO_LARGE"
        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_json()

        assert disconnected.value.code == 1009


def test_inference_failure_recovers_on_next_frame(
    client, session_path, monkeypatch
):
    class RecoveringRecognizer:
        def __init__(self):
            self.calls = 0

        def process(self, image_bgr):
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("模拟推理异常")
            return [], None

    monkeypatch.setattr(app.state, "recognizer", RecoveringRecognizer())

    with client.websocket_connect(session_path) as websocket:
        websocket.receive_json()
        websocket.send_bytes(make_test_jpeg())
        assert websocket.receive_json()["code"] == "INFERENCE_FAILED"

        websocket.send_bytes(make_test_jpeg())
        result = websocket.receive_json()
        assert result["state"] == "no_hand"
        assert result["frame_id"] == 1
        assert result["processing_time_ms"] >= 0


def test_invalid_control_message_is_recoverable(client, session_path):
    with client.websocket_connect(session_path) as websocket:
        websocket.receive_json()
        websocket.send_text("not-json")

        assert websocket.receive_json()["code"] == "VALIDATION_ERROR"

        websocket.send_json({"type": "cancel"})
        assert websocket.receive_json()["type"] == "session_finished"
