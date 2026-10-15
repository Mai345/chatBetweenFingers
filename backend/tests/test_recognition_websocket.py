from fastapi.testclient import TestClient
from app import main

client = TestClient(main.app)


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

        websocket.send_bytes(b"fake-jpeg-1")
        first_result = websocket.receive_json()

        websocket.send_bytes(b"fake-jpeg-2")
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
