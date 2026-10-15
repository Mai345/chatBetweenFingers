from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
import cv2
import numpy as np

MODEL_READY = False
MAX_FRAME_SIZE_BYTES = 300 * 1024
MAX_FRAME_WIDTH = 1280
MAX_FRAME_HEIGHT = 720

app = FastAPI()


@app.get("/api/v0/health")
def health_check():
    if not MODEL_READY:
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "code": "MODEL_NOT_READY",
                    "message": "识别模型尚未准备好",
                }
            },
        )

    return {
        "status": "ok",
        "version": "0.1",
        "model_ready": True,
    }


@app.websocket("/ws/v0/recognition")
async def recognition_socket(websocket: WebSocket):
    await websocket.accept()
    await websocket.send_json(
        {
            "type": "ready",
            "protocol_version": "0.1",
            "max_frame_size_bytes": MAX_FRAME_SIZE_BYTES,
            "recommended_width": 640,
            "recommended_height": 480,
        }
    )

    frame_id = 0

    try:
        while True:
            frame_bytes = await websocket.receive_bytes()

            if len(frame_bytes) > MAX_FRAME_SIZE_BYTES:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "FRAME_TOO_LARGE",
                        "message": "图像帧超过300 KB限制",
                        "recoverable": False,
                    }
                )
                await websocket.close(code=1009)
                return

            encoded_image = np.frombuffer(frame_bytes, dtype=np.uint8)
            image_bgr = cv2.imdecode(encoded_image, cv2.IMREAD_COLOR)

            if image_bgr is None:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "INVALID_IMAGE_FRAME",
                        "message": "无法解析JPEG图像",
                        "recoverable": True,
                    }
                )
                continue

            height, width = image_bgr.shape[:2]

            if width > MAX_FRAME_WIDTH or height > MAX_FRAME_HEIGHT:
                await websocket.send_json(
                    {
                        "type": "error",
                        "code": "FRAME_TOO_LARGE",
                        "message": "图像分辨率超过1280×720限制",
                        "recoverable": False,
                    }
                )
                await websocket.close(code=1009)
                return

            frame_id += 1

            await websocket.send_json(
                {
                    "type": "frame_result",
                    "frame_id": frame_id,
                    "state": "no_hand",
                    "processing_time_ms": 0,
                    "hands": [],
                    "candidate": None,
                }
            )
    except WebSocketDisconnect:
        pass
