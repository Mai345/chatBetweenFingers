from pathlib import Path
import mediapipe as mp
import cv2
import time
import math

MODEL_PATH = (
    Path(__file__).resolve().parent.parent
    / "models"
    / "hand_landmarker.task"
)

# print("模型路径：", MODEL_PATH)
# print("模型是否存在：", MODEL_PATH.exists())

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

options = mp.tasks.vision.HandLandmarkerOptions(
    # 手部关键点配置
    base_options=mp.tasks.BaseOptions(
        model_asset_path=str(MODEL_PATH)
    ),
    running_mode=mp.tasks.vision.RunningMode.VIDEO,
    num_hands=2
)

with mp.tasks.vision.HandLandmarker.create_from_options(options) as landmarker:
    print("MediaPipe 手部模型加载成功")

    camera = cv2.VideoCapture(0)

    if not camera.isOpened():
        print("无法打开摄像头")
        raise RuntimeError("无法打开摄像头")

    start_time = time.perf_counter()

    has_printed_features = False

    while True:
        success, frame = camera.read()

        if not success:
            print("没有读取到摄像头画面")
            break

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

                if not has_printed_features:
                    print("特征数量：", len(features))
                    print(
                        "前9个特征：",
                        [round(value, 3) for value in features[:9]]
                    )
                    has_printed_features = True

                handedness = result.handedness[hand_index][0]
                model_hand_label = handedness.category_name
                hand_score = handedness.score

                if model_hand_label == "Left":
                    hand_label = "Right"
                elif model_hand_label == "Right":
                    hand_label = "Left"
                else:
                    hand_label = model_hand_label

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


        cv2.imshow('Camera Test', frame)
        key = cv2.waitKey(1)
        if key & 0xFF == ord('q'):
            break

    camera.release()
    cv2.destroyAllWindows()
