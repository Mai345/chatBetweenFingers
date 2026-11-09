from pathlib import Path

import joblib
import math

MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "static_knn.joblib"
)


class StaticGestureClassifier:
    def __init__(self):
        self._classifier = joblib.load(MODEL_PATH)

    def _extract_features(self, landmarks):
        wrist = landmarks[0]
        middle_mcp = landmarks[9]

        palm_dx = middle_mcp["x"] - wrist["x"]
        palm_dy = middle_mcp["y"] - wrist["y"]
        palm_size = math.hypot(palm_dx, palm_dy)
        if palm_size < 1e-6:
            return None

        features = []

        for landmark in landmarks:
            relative_x = (landmark["x"] - wrist["x"]) / palm_size
            relative_y = (landmark["y"] - wrist["y"]) / palm_size
            relative_z = (landmark["z"] - wrist["z"]) / palm_size
            features.extend(
                [
                    relative_x,
                    relative_y,
                    relative_z,
                ]
            )

        return features

    def predict(self, landmarks):
        features = self._extract_features(landmarks)
        if features is None:
            return None

        probabilities = self._classifier.predict_proba([features])
        scores = probabilities[0]
        best_index = int(scores.argmax())

        label = str(self._classifier.classes_[best_index])
        confidence = float(scores[best_index])

        return {
            "label": label,
            "confidence": confidence,
            "model_version": "static-v1",
        }
