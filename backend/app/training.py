"""自定义手势、关键点采集、数据导入导出及个人模型训练。"""

import hashlib
import json
import threading
import uuid
from pathlib import Path

import cv2
import joblib
import numpy as np
from fastapi import APIRouter, Request, Cookie
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from starlette.concurrency import run_in_threadpool

from app.api import (
    get_database_connection,
    require_user,
    fail,
    success,
    get_current_time,
    MAX_FRAME_SIZE_BYTES,
)
from app.recognition.knn import (
    FEATURE_VERSION,
    extract_features,
    train_model,
    predict_candidate,
)

router = APIRouter(prefix="/api/v1/training")
training_lock = threading.Lock()


def initialize_training_database():
    with get_database_connection() as connection:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS custom_gestures (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                label TEXT NOT NULL, description TEXT NOT NULL,
                UNIQUE(user_id, label)
            );
            CREATE TABLE IF NOT EXISTS training_samples (
                id TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                label TEXT NOT NULL, batch_id TEXT NOT NULL, features TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)


class GestureCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=32)
    description: str = Field(default="", max_length=500)

    @field_validator("label")
    @classmethod
    def validate_label(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("手势名称不能包含首尾空格")
        return value


class Sample(BaseModel):
    model_config = ConfigDict(extra="forbid")
    label: str = Field(min_length=1, max_length=32)
    batch_id: str = Field(min_length=1, max_length=100)
    features: list[float] = Field(min_length=63, max_length=63)

    @field_validator("label", "batch_id")
    @classmethod
    def validate_text(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("名称和批次不能包含首尾空格")
        return value

    @field_validator("features")
    @classmethod
    def validate_features(cls, value):
        if not np.isfinite(value).all() or np.max(np.abs(value)) > 100:
            raise ValueError("特征必须为合理范围内的有限数字")
        return value


class Dataset(BaseModel):
    model_config = ConfigDict(extra="forbid")
    feature_version: str
    samples: list[Sample] = Field(min_length=1, max_length=10000)


def model_path(user_id):
    with get_database_connection() as connection:
        database_path = connection.execute("PRAGMA database_list").fetchone()[
            2
        ]
    return (
        Path(database_path).parent / "personal_models" / (user_id + ".joblib")
    )


def get_extractor(app):
    if app.state.training_extractor is None:
        from app.recognition.hand_landmark_extractor import (
            HandLandmarkExtractor,
        )

        app.state.training_extractor = HandLandmarkExtractor()
    return app.state.training_extractor


class PersonalRecognizer:
    def __init__(self, app, bundle):
        self.app = app
        self.bundle = bundle

    def process(self, image_bgr):
        with self.app.state.extractor_lock:
            hands = get_extractor(self.app).extract(image_bgr)
        candidate = None
        if len(hands) == 1:
            candidate = predict_candidate(
                self.bundle, extract_features(hands[0]["landmarks"])
            )
        return hands, candidate


def load_user_recognizer(app, user_id):
    if user_id not in app.state.personal_recognizers:
        path = model_path(user_id)
        if not path.exists():
            return None
        try:
            bundle = joblib.load(path)
            if bundle["feature_version"] != FEATURE_VERSION:
                return None
            with app.state.extractor_lock:
                get_extractor(app)
            app.state.personal_recognizers[user_id] = PersonalRecognizer(
                app, bundle
            )
        except Exception:
            return None
    return app.state.personal_recognizers[user_id]


def list_custom_gestures(user_id):
    with get_database_connection() as connection:
        rows = connection.execute(
            "SELECT g.*, (SELECT count(*) FROM training_samples s WHERE s.user_id=g.user_id AND s.label=g.label) AS sample_count FROM custom_gestures g WHERE g.user_id=?",
            (user_id,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "label": row["label"],
            "display_name": row["label"],
            "description": row["description"],
            "sample_count": row["sample_count"],
            "category": "custom",
            "enabled": True,
        }
        for row in rows
    ]


def save_sample(connection, user_id, sample):
    features_json = json.dumps(sample.features)
    fingerprint_source = json.dumps(
        [user_id, sample.label, sample.batch_id, sample.features],
        ensure_ascii=False,
    )
    fingerprint = hashlib.sha256(fingerprint_source.encode()).hexdigest()
    connection.execute(
        "INSERT OR IGNORE INTO custom_gestures VALUES(?,?,?,?)",
        (str(uuid.uuid4()), user_id, sample.label, "导入的数据"),
    )
    return connection.execute(
        "INSERT OR IGNORE INTO training_samples VALUES(?,?,?,?,?,?)",
        (
            fingerprint,
            user_id,
            sample.label,
            sample.batch_id,
            features_json,
            get_current_time(),
        ),
    ).rowcount


@router.post("/gestures")
def create_gesture(
    body: GestureCreate, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    with get_database_connection() as connection:
        existing = connection.execute(
            "SELECT id FROM custom_gestures WHERE user_id=? AND label=?",
            (user["id"], body.label),
        ).fetchone()
        if existing:
            fail(409, "GESTURE_EXISTS", "该手势已经存在")
        connection.execute(
            "INSERT INTO custom_gestures VALUES(?,?,?,?)",
            (str(uuid.uuid4()), user["id"], body.label, body.description),
        )
    return success(list_custom_gestures(user["id"]), status=201)


async def detect_training_hands(request: Request):
    """只检测关键点，不保存图片和训练样本。"""
    frame = bytearray()
    async for chunk in request.stream():
        frame.extend(chunk)
        if len(frame) > MAX_FRAME_SIZE_BYTES:
            fail(400, "FRAME_TOO_LARGE", "图片超过300 KB")
    frame = bytes(frame)
    if len(frame) > MAX_FRAME_SIZE_BYTES:
        fail(400, "FRAME_TOO_LARGE", "图片超过300 KB")
    image = None
    if frame.startswith(b"\xff\xd8\xff"):
        try:
            image = cv2.imdecode(
                np.frombuffer(frame, dtype=np.uint8), cv2.IMREAD_COLOR
            )
        except cv2.error:
            pass
    if image is None or image.shape[1] > 1280 or image.shape[0] > 720:
        fail(400, "INVALID_IMAGE_FRAME", "请发送有效的JPEG，最大1280×720")
    try:

        def extract_hand():
            with request.app.state.extractor_lock:
                return get_extractor(request.app).extract(image)

        return await run_in_threadpool(extract_hand)
    except (ValueError, RuntimeError, OSError) as error:
        fail(503, "CAPTURE_FAILED", str(error))


@router.post("/preview")
async def preview(
    request: Request,
    local_session: str | None = Cookie(default=None),
):
    require_user(local_session)
    hands = await detect_training_hands(request)
    return success({"hands": hands})


@router.post("/capture")
async def capture(
    request: Request,
    label: str,
    batch_id: str,
    local_session: str | None = Cookie(default=None),
):
    user = require_user(local_session)
    with get_database_connection() as connection:
        if not connection.execute(
            "SELECT id FROM custom_gestures WHERE user_id=? AND label=?",
            (user["id"], label),
        ).fetchone():
            fail(404, "NOT_FOUND", "请先创建自定义手势")
    if not batch_id.strip() or len(batch_id) > 100:
        fail(400, "VALIDATION_ERROR", "采集批次不合法")

    hands = await detect_training_hands(request)
    if len(hands) != 1:
        fail(400, "INVALID_HAND_COUNT", "请只让一只手完整出现在画面中")
    try:
        sample = Sample(
            label=label,
            batch_id=batch_id,
            features=extract_features(hands[0]["landmarks"]),
        )
    except ValueError as error:
        fail(400, "INVALID_HAND_FEATURES", str(error))
    with get_database_connection() as connection:
        inserted = save_sample(connection, user["id"], sample)
    return success(
        {
            "saved": bool(inserted),
            "hands": hands,
            "gestures": list_custom_gestures(user["id"]),
        }
    )


@router.get("/dataset")
def export_dataset(local_session: str | None = Cookie(default=None)):
    user = require_user(local_session)
    with get_database_connection() as connection:
        rows = connection.execute(
            "SELECT label,batch_id,features FROM training_samples WHERE user_id=? ORDER BY created_at,id",
            (user["id"],),
        ).fetchall()
    return JSONResponse(
        {
            "feature_version": FEATURE_VERSION,
            "samples": [
                {
                    "label": row["label"],
                    "batch_id": row["batch_id"],
                    "features": json.loads(row["features"]),
                }
                for row in rows
            ],
        },
        headers={
            "Content-Disposition": 'attachment; filename="gesture-dataset.json"'
        },
    )


@router.post("/dataset/import")
def import_dataset(
    body: Dataset, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    if body.feature_version != FEATURE_VERSION:
        fail(400, "FEATURE_VERSION_MISMATCH", "数据的特征处理版本不一致")
    inserted = 0
    with get_database_connection() as connection:
        for sample in body.samples:
            inserted += save_sample(connection, user["id"], sample)
    return success(
        {"imported": inserted, "duplicates": len(body.samples) - inserted}
    )


@router.get("/status")
def training_status(
    request: Request, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    recognizer = load_user_recognizer(request.app, user["id"])
    return success(
        {
            "gestures": list_custom_gestures(user["id"]),
            "model": (
                {
                    key: value
                    for key, value in recognizer.bundle.items()
                    if key not in ("model", "training_labels")
                }
                if recognizer
                else None
            ),
        }
    )


@router.post("/train")
def train(request: Request, local_session: str | None = Cookie(default=None)):
    user = require_user(local_session)
    if request.app.state.connections.get(user["id"]):
        fail(409, "ACTIVE_SESSION_EXISTS", "请先结束实时识别会话，再训练模型")
    if not training_lock.acquire(blocking=False):
        fail(409, "TRAINING_BUSY", "已有训练正在进行")
    try:
        gestures = list_custom_gestures(user["id"])
        insufficient_gestures = [
            gesture for gesture in gestures if gesture["sample_count"] < 30
        ]
        if insufficient_gestures:
            details = "、".join(
                f"{gesture['label']}（{gesture['sample_count']}/30张）"
                for gesture in insufficient_gestures
            )
            fail(400, "INSUFFICIENT_DATA", "每种手势至少30张，还需补充：" + details)
        with get_database_connection() as connection:
            rows = connection.execute(
                "SELECT label,batch_id,features FROM training_samples WHERE user_id=?",
                (user["id"],),
            ).fetchall()
        samples = [
            {
                "label": row["label"],
                "batch_id": row["batch_id"],
                "features": json.loads(row["features"]),
            }
            for row in rows
        ]
        if len(samples) > 10000:
            fail(400, "TOO_MANY_SAMPLES", "最多训练10000个样本")
        try:
            bundle = train_model(samples)
        except ValueError as error:
            fail(400, "INSUFFICIENT_DATA", str(error))
        bundle["version"] = "knn-v1-" + uuid.uuid4().hex[:12]
        bundle["trained_at"] = get_current_time()
        # 加载关键点模型成功后才替换现有分类器。
        try:
            with request.app.state.extractor_lock:
                get_extractor(request.app)
        except (ValueError, RuntimeError, OSError) as error:
            fail(503, "MODEL_NOT_READY", "关键点模型无法加载：" + str(error))
        path = model_path(user["id"])
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = path.with_suffix(".tmp")
        joblib.dump(bundle, temporary_path)
        temporary_path.replace(path)
        request.app.state.personal_recognizers[user["id"]] = (
            PersonalRecognizer(request.app, bundle)
        )
        return success(
            {
                key: value
                for key, value in bundle.items()
                if key not in ("model", "training_labels")
            },
            "training_complete",
        )
    finally:
        training_lock.release()
