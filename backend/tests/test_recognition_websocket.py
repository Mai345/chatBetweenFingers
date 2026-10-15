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
