from pathlib import Path

import cv2
import mediapipe as mp

MODEL_PATH = (
    Path(__file__).resolve().parents[2] / "models" / "hand_landmarker.task"
)


class HandLandmarkExtractor:
    def __init__(self):
        options = mp.tasks.vision.HandLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(
                model_asset_path=str(MODEL_PATH)
            ),
            running_mode=mp.tasks.vision.RunningMode.IMAGE,
            num_hands=2,
        )
        self._landmarker = mp.tasks.vision.HandLandmarker.create_from_options(
            options
        )

    def detect(self, image_bgr):
        image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)

        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=image_rgb)

        result = self._landmarker.detect(mp_image)

        hands = []

        for hand_index, hand_landmarks in enumerate(result.hand_landmarks):
            landmarks = []
            handedness_category = result.handedness[hand_index][0]
            for landmark_index, landmark in enumerate(hand_landmarks):
                landmarks.append(
                    {
                        "index": landmark_index,
                        "x": landmark.x,
                        "y": landmark.y,
                        "z": landmark.z,
                    }
                )

            hands.append(
                {
                    "handedness": handedness_category.category_name,
                    "detection_score": handedness_category.score,
                    "landmarks": landmarks,
                }
            )

        return hands

    def extract(self, image_bgr):
        return self.detect(image_bgr)

    def close(self):
        self._landmarker.close()
