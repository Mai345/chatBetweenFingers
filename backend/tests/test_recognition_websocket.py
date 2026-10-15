import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app import main

client = TestClient(main.app)


def make_test_jpeg(width=10, height=10):
    image = np.zeros((height, width, 3), dtype=np.uint8)
    success, encoded_image = cv2.imencode(".jpg", image)

    assert success
    return encoded_image.tobytes()


def test_recognition_websocket_sends_ready():
    with client.websocket_connect("/ws/v0/recognition") as websocket:
        message = websocket.receive_json()

        assert message == {
            "type": "ready",
            "protocol_version": "0.1",
            "max_frame_size_bytes": 307200,
            "recommended_width": 640,
            "recommended_height": 480,
        }


def test_recognition_websocket_returns_incrementing_frame_ids():
    with client.websocket_connect("/ws/v0/recognition") as websocket:
        websocket.receive_json()

        jpeg_bytes = make_test_jpeg()

        websocket.send_bytes(jpeg_bytes)
        first_result = websocket.receive_json()

        websocket.send_bytes(jpeg_bytes)
        second_result = websocket.receive_json()

        assert first_result == {
            "type": "frame_result",
            "frame_id": 1,
            "state": "no_hand",
            "processing_time_ms": 0,
            "hands": [],
            "candidate": None,
        }
        assert second_result == {
            "type": "frame_result",
            "frame_id": 2,
            "state": "no_hand",
            "processing_time_ms": 0,
            "hands": [],
            "candidate": None,
        }


def test_recognition_websocket_rejects_invalid_jpeg():
    with client.websocket_connect("/ws/v0/recognition") as websocket:
        websocket.receive_json()

        websocket.send_bytes(b"not-a-jpeg")
        message = websocket.receive_json()

        assert message == {
            "type": "error",
            "code": "INVALID_IMAGE_FRAME",
            "message": "无法解析JPEG图像",
            "recoverable": True,
        }


def test_recognition_websocket_closes_when_frame_too_large():
    with client.websocket_connect("/ws/v0/recognition") as websocket:
        websocket.receive_json()

        oversized_frame = b"x" * (main.MAX_FRAME_SIZE_BYTES + 1)

        websocket.send_bytes(oversized_frame)
        error_message = websocket.receive_json()

        assert error_message == {
            "type": "error",
            "code": "FRAME_TOO_LARGE",
            "message": "图像帧超过300 KB限制",
            "recoverable": False,
        }

        with pytest.raises(WebSocketDisconnect) as disconnect_info:
            websocket.receive_json()

        assert disconnect_info.value.code == 1009


def test_recognition_websocket_closes_when_resolution_too_large():
    with client.websocket_connect("/ws/v0/recognition") as websocket:
        websocket.receive_json()

        width = main.MAX_FRAME_WIDTH + 1
        height = main.MAX_FRAME_HEIGHT

        jpeg_bytes = make_test_jpeg(width, height)

        assert len(jpeg_bytes) <= main.MAX_FRAME_SIZE_BYTES

        websocket.send_bytes(jpeg_bytes)
        error_message = websocket.receive_json()

        assert error_message == {
            "type": "error",
            "code": "FRAME_TOO_LARGE",
            "message": "图像分辨率超过1280×720限制",
            "recoverable": False,
        }

        with pytest.raises(WebSocketDisconnect) as disconnect_info:
            websocket.receive_json()

        assert disconnect_info.value.code == 1009
