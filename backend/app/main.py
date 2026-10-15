from fastapi import FastAPI
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
