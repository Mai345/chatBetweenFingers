from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

MODEL_READY = False

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
            "max_frame_size_bytes": 307200,
            "recommended_width": 640,
            "recommended_height": 480,
        }
    )

    try:
        while True:
            await websocket.receive_bytes()
    except WebSocketDisconnect:
        pass
