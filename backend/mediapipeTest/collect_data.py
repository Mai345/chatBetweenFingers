from pathlib import Path
from datetime import datetime

import mediapipe as mp
import cv2
import time
import math
import csv


MODEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "models"
    / "hand_landmarker.task"
)

DATA_FILE=(
    Path(__file__).resolve().parent.parent
    / "data"
    / "gesture_samples.csv"
)
CURRENT_LABEL = "v_sign" #open_palm,fist,v_sign
PARTICIPANT_ID = "p01"
PARTICIPANT_NAME = "zfyh"
TARGET_HAND = "Right"
TARGET_SAMPLES = 30
SESSION_ID = datetime.now().strftime(
    "%Y%m%d_%H%M%S"
)

# 21个点转化成63个特征坐标
def extract_features(hand_landmarks):

    wrist=hand_landmarks[0]
    middle_mcp = hand_landmarks[9]
    palm_dx = middle_mcp.x - wrist.x
    palm_dy = middle_mcp.y - wrist.y
    palm_size = math.hypot(palm_dx, palm_dy)
    if palm_size < 1e-6:
        return None

    features = []
    # 相对手腕的位置
    for landmark in hand_landmarks:
        relative_x = (landmark.x - wrist.x) / palm_size
        relative_y = (landmark.y - wrist.y) / palm_size
        relative_z = (landmark.z - wrist.z) / palm_size
        features.extend([
            relative_x,
            relative_y,
            relative_z
        ])
    return features

# 保存测试样本
def save_sample(
    file_path,
    label,
    participant_id,
    participant_name,
    session_id,
    handedness,
    features
):
    file_path.parent.mkdir(
        parents=True,
        exist_ok=True
    )

    # 是否需要表头
    needs_header = (
        not file_path.exists()
        or file_path.stat().st_size == 0
    )

    with file_path.open("a",newline="",encoding="utf-8-sig") as csv_file:
        writer = csv.writer(csv_file)

        # 表头
        if needs_header:
            feature_names=[f"f{index}" for index in range(len(features))]
            writer.writerow([
                "label",
                "participant_id",
                "participant_name",
                "session_id",
                "handedness",
                *feature_names
            ])

        # 写入数据
        writer.writerow([
            label,
            participant_id,
            participant_name,
            session_id,
            handedness,
            *features
        ])

options = mp.tasks.vision.HandLandmarkerOptions(
    # 手部关键点配置
    base_options=mp.tasks.BaseOptions(
        model_asset_path=str(MODEL_PATH)
    ),
    running_mode=mp.tasks.vision.RunningMode.VIDEO,
    num_hands=1
)

with mp.tasks.vision.HandLandmarker.create_from_options(options) as landmarker:
    print("MediaPipe 手部模型加载成功")

    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("无法打开摄像头")
        raise RuntimeError("无法打开摄像头")

    start_time = time.perf_counter()
    saved_count = 0

    while True:
        success, frame = camera.read()

        if not success:
            print("没有读取到摄像头画面")
            break

        current_features = None
        current_hand_label = None

        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
        frame = cv2.flip(frame, 1)

        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

        mp_image = mp.Image(
            image_format=mp.ImageFormat.SRGB,
            data=rgb_frame
        )

        # 当前时间戳
        timestamp_ms = int(
            (time.perf_counter() - start_time) * 1000
        )

        result = landmarker.detect_for_video(
            mp_image,
            timestamp_ms
        )

        if result.hand_landmarks:
            frame_height, frame_width, _ = frame.shape

            for hand_index, hand_landmarks in enumerate(result.hand_landmarks):

                features = extract_features(hand_landmarks)
                if features is None:
                    continue

                handedness = result.handedness[hand_index][0]
                model_hand_label = handedness.category_name
                hand_score = handedness.score

                if model_hand_label == "Left":
                    hand_label = "Right"
                elif model_hand_label == "Right":
                    hand_label = "Left"
                else:
                    hand_label = model_hand_label

                current_features = features
                current_hand_label = hand_label

                # 连线
                for connection in(
                    mp.tasks.vision.HandLandmarksConnections.HAND_CONNECTIONS
                ):
                    start_landmark = hand_landmarks[connection.start]
                    end_landmark = hand_landmarks[connection.end]
                    start_x = int(start_landmark.x * frame_width)
                    start_y = int(start_landmark.y * frame_height)
                    start_point = (start_x, start_y)

                    end_x = int(end_landmark.x * frame_width)
                    end_y = int(end_landmark.y * frame_height)
                    end_point = (end_x, end_y)

                    cv2.line(
                        frame,
                        start_point,
                        end_point,
                        (255, 255, 255),
                        2
                    )

                # 21个关键点
                for landmark in hand_landmarks:
                    x = int(landmark.x * frame_width)
                    y = int(landmark.y * frame_height)
                    cv2.circle(
                        frame,
                        (x, y),
                        5,
                        (0, 255, 0),
                        -1
                    )

                # 判断左右手
                wrist = hand_landmarks[0]
                text_x = int(wrist.x * frame_width)
                text_y = int(wrist.y * frame_height) - 20
                display_text = f"{hand_label} {hand_score:.2f}"
                cv2.putText(
                    frame,
                    display_text,
                    (text_x, text_y),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (0, 255, 255),
                    2,
                    cv2.LINE_AA
                )

        status_text = (
            f"Label: {CURRENT_LABEL}  "
            f"Samples: {saved_count}/{TARGET_SAMPLES}"
        )
        cv2.putText(
            frame,
            status_text,
            (20, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.7,
            (0, 255, 0),
            2,
            cv2.LINE_AA
        )

        cv2.imshow('Camera Test', frame)
        key = cv2.waitKey(1) & 0xFF

        if key == ord("s"):
            if current_features is None:
                print("未检测到手，本次未保存")
            elif current_hand_label != TARGET_HAND:
                print(
                    f"请使用{TARGET_HAND}手，"
                    f"当前检测到：{current_hand_label}"
                )
            elif saved_count >= TARGET_SAMPLES:
                print("已经达到目标数量，本次未保存")
            else:
                save_sample(
                    DATA_FILE,
                    CURRENT_LABEL,
                    PARTICIPANT_ID,
                    PARTICIPANT_NAME,
                    SESSION_ID,
                    current_hand_label,
                    current_features
                )
                saved_count += 1
                print(
                    f"已保存 {CURRENT_LABEL}："
                    f"{saved_count}/{TARGET_SAMPLES}"
                )
        elif key == ord("q"):
            break

    camera.release()
    cv2.destroyAllWindows()
