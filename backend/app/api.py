"""v1 基础接口、登录会话与实时识别调度。"""

import hashlib
import json
import logging
import os
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

import cv2
import numpy as np
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import (
    APIRouter,
    Cookie,
    Query,
    Request,
    Response,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.recognition.stability import StabilityTracker

router = APIRouter()
password_hasher = PasswordHasher()
DEFAULT_SETTINGS = {
    "confidence_threshold": 0.8,
    "stable_duration_ms": 1000,
    "cooldown_ms": 1200,
    "mirror_preview": True,
    "show_landmarks": True,
    "show_confidence": True,
    "audio_enabled": True,
    "audio_rate": 1.0,
    "audio_volume": 1.0,
}
SESSION_COOKIE_NAME = "local_session"
DEFAULT_DATABASE_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "local_v1.sqlite3"
)
MAX_FRAME_SIZE_BYTES = 300 * 1024
MAX_FRAME_WIDTH = 1280
MAX_FRAME_HEIGHT = 720


def get_current_time():
    return datetime.now(timezone.utc).isoformat()


class Connection(sqlite3.Connection):
    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def get_database_connection():
    database_path = Path(
        os.environ.get("CBF_DB_PATH", str(DEFAULT_DATABASE_PATH))
    )
    database_path.parent.mkdir(parents=True, exist_ok=True)

    connection = sqlite3.connect(
        database_path,
        timeout=10,
        factory=Connection,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def init_db():
    with get_database_connection() as connection:
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY,
            username TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            display_name TEXT NOT NULL,
            settings TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS login_sessions (
            token_hash TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            expires_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            settings TEXT NOT NULL,
            draft_text TEXT NOT NULL DEFAULT '',
            tokens TEXT NOT NULL DEFAULT '[]',
            events TEXT NOT NULL DEFAULT '[]',
            started_at TEXT NOT NULL,
            finished_at TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS one_active_session_per_user
        ON sessions(user_id)
        WHERE status IN ('created', 'active');
        """)
        connection.execute(
            "UPDATE sessions SET status='finished', finished_at=? "
            "WHERE status IN ('created','active') "
            "AND length(trim(draft_text))>0",
            (get_current_time(),),
        )
        connection.execute(
            "DELETE FROM sessions WHERE status IN ('created','active') "
            "AND length(trim(draft_text))=0"
        )


class APIError(Exception):
    def __init__(self, status, code, message):
        self.status = status
        self.code = code
        self.message = message


def fail(status, code, message):
    raise APIError(status, code, message)


def success(data, message="ok", status=200):
    return JSONResponse(
        status_code=status,
        content={
            "data": data,
            "message": message,
        },
    )


def hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


def get_user_from_token(token):
    if not token:
        return None
    with get_database_connection() as connection:
        return connection.execute(
            "SELECT users.* FROM users "
            "JOIN login_sessions ON login_sessions.user_id=users.id "
            "WHERE login_sessions.token_hash=? "
            "AND login_sessions.expires_at>?",
            (hash_token(token), get_current_time()),
        ).fetchone()


def require_user(token):
    user = get_user_from_token(token)
    if not user:
        fail(401, "NOT_AUTHENTICATED", "请先登录")
    return user


def serialize_user(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "display_name": user["display_name"],
    }


def create_login_response(user_id, message, status=200):
    token = secrets.token_urlsafe(32)
    with get_database_connection() as connection:
        connection.execute(
            "INSERT INTO login_sessions VALUES(?,?,?)",
            (
                hash_token(token),
                user_id,
                (datetime.now(timezone.utc) + timedelta(days=7)).isoformat(),
            ),
        )
        user = connection.execute(
            "SELECT * FROM users WHERE id=?", (user_id,)
        ).fetchone()
    response = success(serialize_user(user), message, status)
    response.set_cookie(
        SESSION_COOKIE_NAME,
        token,
        httponly=True,
        samesite="lax",
        max_age=7 * 24 * 3600,
        path="/",
    )
    return response


class Register(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=4, max_length=32)
    password: str = Field(min_length=8, max_length=64)
    display_name: str = Field(min_length=1, max_length=32)

    @field_validator("username", "display_name")
    @classmethod
    def nonblank(cls, value):
        if not value.strip() or value != value.strip():
            raise ValueError("不能只包含空格或以空格开头、结尾")
        return value


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=4, max_length=32)
    password: str = Field(min_length=8, max_length=64)


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confidence_threshold: float | None = Field(default=None, ge=0.5, le=0.99)
    stable_duration_ms: int | None = Field(default=None, ge=300, le=3000)
    cooldown_ms: int | None = Field(default=None, ge=300, le=5000)
    mirror_preview: bool | None = None
    show_landmarks: bool | None = None
    show_confidence: bool | None = None
    audio_enabled: bool | None = None
    audio_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    audio_volume: float | None = Field(default=None, ge=0, le=1.0)


@router.post("/api/v1/auth/register")
def register(body: Register):
    user_id = str(uuid.uuid4())
    try:
        with get_database_connection() as connection:
            connection.execute(
                "INSERT INTO users VALUES(?,?,?,?,?)",
                (
                    user_id,
                    body.username,
                    password_hasher.hash(body.password),
                    body.display_name,
                    json.dumps(DEFAULT_SETTINGS),
                ),
            )
    except sqlite3.IntegrityError:
        fail(409, "USERNAME_EXISTS", "用户名已存在")
    return create_login_response(user_id, "register_success", 201)


@router.post("/api/v1/auth/login")
def login(body: Login):
    with get_database_connection() as connection:
        user = connection.execute(
            "SELECT * FROM users WHERE username=?", (body.username,)
        ).fetchone()
    try:
        valid = user and password_hasher.verify(
            user["password_hash"], body.password
        )
    except (VerifyMismatchError, InvalidHashError):
        valid = False
    if not valid:
        fail(401, "INVALID_CREDENTIALS", "用户名或密码错误")
    return create_login_response(user["id"], "login_success")


@router.get("/api/v1/auth/me")
def me(local_session: str | None = Cookie(default=None)):
    return success(serialize_user(require_user(local_session)))


@router.post("/api/v1/auth/logout")
async def logout(
    request: Request, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    with get_database_connection() as connection:
        connection.execute(
            "DELETE FROM login_sessions WHERE token_hash=?",
            (hash_token(local_session),),
        )
    for websocket in list(
        request.app.state.connections.get(user["id"], set())
    ):
        try:
            await websocket.close(code=1000)
        except RuntimeError:
            pass
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE_NAME, path="/", samesite="lax", httponly=True
    )
    return response


@router.get("/api/v1/health")
def health(request: Request):
    try:
        with get_database_connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except sqlite3.Error:
        fail(503, "DATABASE_NOT_READY", "数据库不可用")
    user = get_user_from_token(request.cookies.get(SESSION_COOKIE_NAME))
    ready = (
        get_recognizer(request.app, user["id"] if user else None) is not None
    )
    status = {
        "status": "ok" if ready else "degraded",
        "database_ready": True,
        "landmark_model_ready": ready,
        "static_model_ready": ready,
    }
    if ready:
        return success(status)
    return JSONResponse(
        {
            "error": {
                "code": "MODEL_NOT_READY",
                "message": "手势识别模型尚未接入",
            },
            "data": status,
        },
        status_code=503,
    )


# 示例词条不代表模型已经支持识别。
GESTURE_EXAMPLES = [
    ("open_palm", "张开手掌", "五指自然张开，面向摄像头。"),
    ("fist", "握拳", "五指收拢形成拳头。"),
    ("v_sign", "V 手势", "伸出食指和中指，其余手指收拢。"),
]

GESTURES = []
for label, display_name, description in GESTURE_EXAMPLES:
    GESTURES.append(
        {
            "id": str(uuid.uuid5(uuid.NAMESPACE_URL, "cbf:" + label)),
            "label": label,
            "display_name": display_name,
            "category": "demo",
            "description": description,
            "example_image_url": None,
            "difficulty": "easy",
            "enabled": True,
        }
    )


@router.get("/api/v1/gestures")
def gestures(
    keyword: str = "", local_session: str | None = Cookie(default=None)
):
    from app.training import list_custom_gestures

    user = get_user_from_token(local_session)
    available_gestures = list(GESTURES)
    if user:
        available_gestures.extend(list_custom_gestures(user["id"]))
    matching_gestures = []
    for gesture in available_gestures:
        searchable_text = gesture["label"] + gesture["display_name"]
        if keyword.lower() in searchable_text.lower():
            matching_gestures.append(gesture)

    return success(matching_gestures)


@router.get("/api/v1/gestures/{gesture_id}")
def gesture(gesture_id: str, local_session: str | None = Cookie(default=None)):
    from app.training import list_custom_gestures

    user = get_user_from_token(local_session)
    available_gestures = list(GESTURES)
    if user:
        available_gestures.extend(list_custom_gestures(user["id"]))
    for gesture in available_gestures:
        if gesture["id"] == gesture_id:
            return success(gesture)

    fail(404, "NOT_FOUND", "词条不存在")


@router.get("/api/v1/settings")
def get_settings(local_session: str | None = Cookie(default=None)):
    user = require_user(local_session)
    settings = json.loads(user["settings"])
    return success(settings)


@router.patch("/api/v1/settings")
def update_settings(
    body: Settings, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    changes = body.model_dump(exclude_unset=True)
    if any(value is None for value in changes.values()):
        fail(400, "VALIDATION_ERROR", "设置不能为 null")
    settings = json.loads(user["settings"])
    settings.update(changes)
    with get_database_connection() as connection:
        connection.execute(
            "UPDATE users SET settings=? WHERE id=?",
            (json.dumps(settings), user["id"]),
        )
    return success(settings)


def serialize_session(row):
    data = dict(row)
    for key in ("settings", "tokens", "events"):
        data[key] = json.loads(data[key])
    data["recognized_tokens"] = data.pop("tokens")
    data["recognized_events"] = data.pop("events")
    data["gesture_count"] = len(data["recognized_events"])
    scores = [
        e["confidence"] for e in data["recognized_events"] if "confidence" in e
    ]
    data["average_confidence"] = sum(scores) / len(scores) if scores else None
    data.pop("user_id", None)
    return data


def get_owned_session(connection, session_id, user_id):
    row = connection.execute(
        "SELECT * FROM sessions WHERE id=? AND user_id=?",
        (session_id, user_id),
    ).fetchone()
    if not row:
        fail(404, "SESSION_NOT_FOUND", "会话不存在")
    return row


@router.post("/api/v1/sessions")
def create_session(local_session: str | None = Cookie(default=None)):
    user = require_user(local_session)
    session_id = str(uuid.uuid4())
    settings = json.loads(user["settings"])
    try:
        with get_database_connection() as connection:
            connection.execute(
                "INSERT INTO sessions(id,user_id,status,settings,started_at) VALUES(?,?,?,?,?)",
                (
                    session_id,
                    user["id"],
                    "created",
                    json.dumps(settings),
                    get_current_time(),
                ),
            )
    except sqlite3.IntegrityError:
        fail(409, "ACTIVE_SESSION_EXISTS", "已有未结束的会话")
    return success(
        {"id": session_id, "status": "created", "settings": settings},
        "session_created",
        201,
    )


@router.get("/api/v1/sessions")
def list_sessions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    local_session: str | None = Cookie(default=None),
):
    user = require_user(local_session)
    with get_database_connection() as connection:
        rows = connection.execute(
            "SELECT * FROM sessions WHERE user_id=? AND status='finished' "
            "ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (user["id"], page_size, (page - 1) * page_size),
        ).fetchall()
    return success([serialize_session(r) for r in rows])


@router.get("/api/v1/sessions/{session_id}")
def get_session(
    session_id: str, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    with get_database_connection() as connection:
        return success(
            serialize_session(
                get_owned_session(connection, session_id, user["id"])
            )
        )


@router.delete("/api/v1/sessions/{session_id}")
def delete_session(
    session_id: str, local_session: str | None = Cookie(default=None)
):
    user = require_user(local_session)
    with get_database_connection() as connection:
        row = get_owned_session(connection, session_id, user["id"])
        if row["status"] == "active":
            fail(409, "ACTIVE_SESSION_EXISTS", "请先结束识别会话")
        connection.execute(
            "DELETE FROM sessions WHERE id=? AND user_id=?",
            (session_id, user["id"]),
        )
    return Response(status_code=204)


def finish_session(session_id, user_id, cancel=False):
    with get_database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not row or row["status"] not in ("created", "active"):
            return
        if cancel or not row["draft_text"].strip():
            connection.execute(
                "DELETE FROM sessions WHERE id=?", (session_id,)
            )
        else:
            connection.execute(
                "UPDATE sessions SET status='finished', finished_at=? WHERE id=?",
                (get_current_time(), session_id),
            )


async def send_websocket_error(websocket, code, message, recoverable):
    await websocket.send_json(
        {
            "type": "error",
            "code": code,
            "message": message,
            "recoverable": recoverable,
        }
    )


def get_recognizer(app, user_id):
    if app.state.recognizer is not None:
        return app.state.recognizer
    if user_id is None:
        return None
    from app.training import load_user_recognizer

    return load_user_recognizer(app, user_id)


def allowed_origin(headers):
    origin = headers.get("origin")
    if not origin:
        return True
    parsed = urlsplit(origin)
    return (
        parsed.scheme in ("http", "https")
        and parsed.hostname in ("localhost", "127.0.0.1", "::1")
        and parsed.netloc == headers.get("host")
    )


@router.websocket("/ws/v1/recognition")
async def recognition(websocket: WebSocket, session_id: str = ""):
    await websocket.accept()
    if not allowed_origin(websocket.headers):
        await send_websocket_error(
            websocket, "NOT_AUTHENTICATED", "请求来源不允许", False
        )
        await websocket.close(code=1008)
        return
    user = get_user_from_token(websocket.cookies.get(SESSION_COOKIE_NAME))
    if not user:
        await send_websocket_error(
            websocket, "NOT_AUTHENTICATED", "请先登录", False
        )
        await websocket.close(code=1008)
        return
    user_id = user["id"]
    with get_database_connection() as connection:
        row = connection.execute(
            "SELECT * FROM sessions WHERE id=? AND user_id=?",
            (session_id, user_id),
        ).fetchone()
        if not row:
            error = ("SESSION_NOT_FOUND", "会话不存在")
        elif row["status"] != "created":
            error = ("SESSION_ALREADY_FINISHED", "会话已结束或正在使用")
        else:
            claimed = connection.execute(
                "UPDATE sessions SET status='active' WHERE id=? AND status='created'",
                (session_id,),
            ).rowcount
            error = (
                None
                if claimed
                else ("SESSION_ALREADY_FINISHED", "会话已结束或正在使用")
            )
    if error:
        await send_websocket_error(websocket, *error, False)
        await websocket.close(code=1008)
        return
    connections = websocket.app.state.connections.setdefault(user_id, set())
    connections.add(websocket)
    settings = json.loads(row["settings"])
    tracker = StabilityTracker(
        settings["confidence_threshold"],
        settings["stable_duration_ms"],
        settings["cooldown_ms"],
    )
    frame_id = 0
    cancel = False
    try:
        await websocket.send_json(
            {
                "type": "connected",
                "session_id": session_id,
                "max_frame_size_bytes": MAX_FRAME_SIZE_BYTES,
                "recommended_width": 640,
                "recommended_height": 480,
                "model_ready": get_recognizer(websocket.app, user_id)
                is not None,
            }
        )
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                break
            frame = message.get("bytes")
            if frame is not None:
                started = time.perf_counter()
                if len(frame) > MAX_FRAME_SIZE_BYTES:
                    await send_websocket_error(
                        websocket,
                        "FRAME_TOO_LARGE",
                        "图像帧超过300 KB限制",
                        False,
                    )
                    await websocket.close(code=1009)
                    break
                image = None
                if frame.startswith(b"\xff\xd8\xff"):
                    try:
                        image = cv2.imdecode(
                            np.frombuffer(frame, dtype=np.uint8),
                            cv2.IMREAD_COLOR,
                        )
                    except cv2.error:
                        pass
                if image is None:
                    await send_websocket_error(
                        websocket,
                        "INVALID_IMAGE_FRAME",
                        "无法解析JPEG图像",
                        True,
                    )
                    continue
                height, width = image.shape[:2]
                if width > MAX_FRAME_WIDTH or height > MAX_FRAME_HEIGHT:
                    await send_websocket_error(
                        websocket,
                        "FRAME_TOO_LARGE",
                        "图像分辨率超过1280×720限制",
                        False,
                    )
                    await websocket.close(code=1009)
                    break
                recognizer = get_recognizer(websocket.app, user_id)
                if recognizer is None:
                    await send_websocket_error(
                        websocket,
                        "MODEL_NOT_READY",
                        "手势识别模型尚未接入",
                        True,
                    )
                    continue
                try:
                    hands, candidate = recognizer.process(image)
                    result_state, stable_ms, committed = tracker.update(
                        candidate, int(time.monotonic() * 1000), bool(hands)
                    )
                except Exception:
                    await send_websocket_error(
                        websocket,
                        "INFERENCE_FAILED",
                        "当前图像帧识别失败",
                        True,
                    )
                    continue
                event = None
                with get_database_connection() as connection:
                    current = get_owned_session(
                        connection, session_id, user_id
                    )
                    tokens = json.loads(current["tokens"])
                    events = json.loads(current["events"])
                    draft = current["draft_text"]
                    if committed:
                        event = {
                            "id": str(uuid.uuid4()),
                            "label": committed["label"],
                            "confidence": committed["confidence"],
                            "committed_at": get_current_time(),
                        }
                        tokens.append(event["label"])
                        events.append(event)
                        draft += event["label"]
                        connection.execute(
                            "UPDATE sessions SET draft_text=?, tokens=?, events=? WHERE id=?",
                            (
                                draft,
                                json.dumps(tokens, ensure_ascii=False),
                                json.dumps(events, ensure_ascii=False),
                                session_id,
                            ),
                        )
                frame_id += 1
                await websocket.send_json(
                    {
                        "type": "frame_result",
                        "frame_id": frame_id,
                        "state": result_state,
                        "processing_time_ms": round(
                            (time.perf_counter() - started) * 1000, 2
                        ),
                        "hands": hands,
                        "candidate": candidate,
                        "stable_ms": stable_ms,
                        "draft_text": draft,
                    }
                )
                if event:
                    await websocket.send_json(
                        {
                            "type": "gesture_committed",
                            "event": event,
                            "recognized_tokens": tokens,
                            "draft_text": draft,
                        }
                    )
                continue
            try:
                command = json.loads(message.get("text") or "")
                if not isinstance(command, dict):
                    raise ValueError("invalid command")
            except (json.JSONDecodeError, ValueError):
                await send_websocket_error(
                    websocket, "VALIDATION_ERROR", "消息格式不正确", True
                )
                continue
            kind = command.get("type")
            if kind in ("stop", "cancel"):
                cancel = kind == "cancel"
                finish_session(session_id, user_id, cancel)
                await websocket.send_json(
                    {
                        "type": "session_finished",
                        "session_id": session_id,
                        "cancelled": cancel,
                    }
                )
                await websocket.close(code=1000)
                break
            if kind != "sentence_command":
                await send_websocket_error(
                    websocket, "VALIDATION_ERROR", "不支持的消息类型", True
                )
                continue
            action = command.get("action")
            with get_database_connection() as connection:
                current = get_owned_session(connection, session_id, user_id)
                tokens = json.loads(current["tokens"])
                draft = current["draft_text"]
                if (
                    action == "replace_text"
                    and isinstance(command.get("text"), str)
                    and len(command["text"]) <= 10000
                ):
                    draft = command["text"]
                elif action == "clear":
                    draft, tokens = "", []
                elif action == "undo_last":
                    if tokens:
                        removed = tokens.pop()
                        if draft.endswith(removed):
                            draft = draft[: -len(removed)]
                else:
                    await send_websocket_error(
                        websocket, "VALIDATION_ERROR", "句子操作不合法", True
                    )
                    continue
                connection.execute(
                    "UPDATE sessions SET draft_text=?, tokens=? WHERE id=?",
                    (
                        draft,
                        json.dumps(tokens, ensure_ascii=False),
                        session_id,
                    ),
                )
            await websocket.send_json(
                {
                    "type": "sentence_updated",
                    "action": action,
                    "recognized_tokens": tokens,
                    "draft_text": draft,
                }
            )
    except WebSocketDisconnect:
        pass
    except Exception:
        logging.exception("v1 识别连接处理失败")
        try:
            await send_websocket_error(
                websocket, "INTERNAL_ERROR", "服务器内部错误", False
            )
            await websocket.close(code=1011)
        except (WebSocketDisconnect, RuntimeError):
            pass
    finally:
        connections.discard(websocket)
        if not connections:
            websocket.app.state.connections.pop(user_id, None)
        finish_session(session_id, user_id, cancel)
