import numpy as np
import cv2
import pytest

from app.main import app
from app.recognition.knn import FEATURE_VERSION, train_model, predict_candidate


def login(client, username="trainer"):
    assert (
        client.post(
            "/api/v1/auth/register",
            json={
                "username": username,
                "password": "password123",
                "display_name": "采集员",
            },
        ).status_code
        == 201
    )


def samples():
    rows = []
    for label, offset in [("左", -1), ("右", 1)]:
        for batch in ("first", "second"):
            for index in range(15):
                rows.append(
                    {
                        "label": label,
                        "batch_id": batch,
                        "features": [offset + index * 0.001] * 63,
                    }
                )
    return rows


class FakeExtractor:
    def extract(self, image):
        return [
            {
                "landmarks": [
                    {"index": i, "x": i * 0.01, "y": i * 0.02, "z": 0}
                    for i in range(21)
                ]
            }
        ]

    def close(self):
        pass


def test_collect_export_import_and_deduplicate(client, monkeypatch):
    login(client)
    monkeypatch.setattr(app.state, "training_extractor", FakeExtractor())
    assert (
        client.post(
            "/api/v1/training/gestures", json={"label": "谢谢"}
        ).status_code
        == 201
    )
    ok, frame = cv2.imencode(".jpg", np.zeros((10, 10, 3), dtype=np.uint8))
    assert ok
    url = "/api/v1/training/capture?label=谢谢&batch_id=batch1"
    result = client.post(
        url, content=frame.tobytes(), headers={"Content-Type": "image/jpeg"}
    )
    assert result.status_code == 200
    assert result.json()["data"]["saved"] is True
    exported = client.get("/api/v1/training/dataset").json()
    assert len(exported["samples"][0]["features"]) == 63
    result = client.post("/api/v1/training/dataset/import", json=exported)
    assert result.json()["data"]["duplicates"] == 1
    assert (
        client.get("/api/v1/training/status").json()["data"]["gestures"][0][
            "sample_count"
        ]
        == 1
    )


def test_import_train_and_personal_recognition(client, monkeypatch):
    login(client)
    monkeypatch.setattr(app.state, "training_extractor", FakeExtractor())
    dataset = {"feature_version": FEATURE_VERSION, "samples": samples()}
    assert (
        client.post(
            "/api/v1/training/dataset/import", json=dataset
        ).status_code
        == 200
    )
    response = client.post("/api/v1/training/train")
    assert response.status_code == 200, response.text
    assert response.json()["data"]["evaluation"]["available"] is True
    assert client.get("/api/v1/health").status_code == 200
    app.state.personal_recognizers.clear()
    assert client.get("/api/v1/health").status_code == 200
    assert (
        client.get("/api/v1/training/status").json()["data"]["model"][
            "sample_count"
        ]
        == 60
    )
    client.post("/api/v1/auth/logout")
    login(client, "another_user")
    assert client.get("/api/v1/training/dataset").json()["samples"] == []
    assert client.get("/api/v1/health").status_code == 503


def test_knn_distance_rejection_and_no_batch_leakage():
    bundle = train_model(samples())
    bundle["version"] = "test"
    assert predict_candidate(bundle, [-1] * 63)["label"] == "左"
    assert predict_candidate(bundle, [50] * 63) is None
    ungrouped = [{**sample, "batch_id": "same"} for sample in samples()]
    assert train_model(ungrouped)["evaluation"]["available"] is False


def test_training_requires_30_samples_per_gesture(client, monkeypatch):
    with pytest.raises(ValueError, match="30"):
        train_model(samples()[1:])

    login(client)
    monkeypatch.setattr(app.state, "training_extractor", FakeExtractor())
    dataset = {"feature_version": FEATURE_VERSION, "samples": samples()[1:]}
    response = client.post("/api/v1/training/dataset/import", json=dataset)
    assert response.status_code == 200
    response = client.post("/api/v1/training/train")
    assert response.status_code == 400
    assert "29/30" in response.json()["error"]["message"]

    dataset["samples"] = samples()[:1]
    response = client.post("/api/v1/training/dataset/import", json=dataset)
    assert response.status_code == 200
    assert client.post("/api/v1/training/train").status_code == 200

    client.post("/api/v1/training/gestures", json={"label": "尚未采集"})
    response = client.post("/api/v1/training/train")
    assert response.status_code == 400
    assert "0/30" in response.json()["error"]["message"]


def test_invalid_data_and_capture_auth(client):
    assert client.get("/api/v1/training/dataset").status_code == 401
    login(client)
    assert client.post("/api/v1/training/train").status_code == 400
    dataset = {"feature_version": "wrong", "samples": samples()}
    assert (
        client.post(
            "/api/v1/training/dataset/import", json=dataset
        ).status_code
        == 400
    )


    dataset = {
        "feature_version": FEATURE_VERSION,
        "samples": [{"label": "a", "batch_id": "b", "features": [1]}],
    }
    assert (
        client.post(
            "/api/v1/training/dataset/import", json=dataset
        ).status_code
        == 400
    )


@pytest.mark.parametrize("hand_count", [0, 1, 2])
def test_preview_returns_landmarks_without_saving_samples(
    client, monkeypatch, hand_count
):
    login(client)
    extractor = FakeExtractor()
    hands = extractor.extract(None) * hand_count
    monkeypatch.setattr(extractor, "extract", lambda image: hands)
    monkeypatch.setattr(app.state, "training_extractor", extractor)
    success, encoded = cv2.imencode(
        ".jpg", np.zeros((10, 10, 3), dtype=np.uint8)
    )
    assert success

    response = client.post(
        "/api/v1/training/preview",
        content=encoded.tobytes(),
        headers={"Content-Type": "image/jpeg"},
    )

    assert response.status_code == 200
    assert len(response.json()["data"]["hands"]) == hand_count
    if hand_count:
        assert len(response.json()["data"]["hands"][0]["landmarks"]) == 21
    assert client.get("/api/v1/training/dataset").json()["samples"] == []


def test_preview_checks_login_and_image(client):
    assert client.post("/api/v1/training/preview", content=b"").status_code == 401
    login(client)
    response = client.post(
        "/api/v1/training/preview",
        content=b"not-jpeg",
        headers={"Content-Type": "image/jpeg"},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "INVALID_IMAGE_FRAME"
