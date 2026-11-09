from contextlib import asynccontextmanager
from pathlib import Path
import threading

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import APIError, allowed_origin, init_db, router
from app.training import (
    initialize_training_database,
    router as training_router,
)

FRONTEND_DIRECTORY = Path(__file__).resolve().parents[2] / "frontend" / "v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    initialize_training_database()
    app.state.connections = {}
    app.state.recognizer = None
    app.state.personal_recognizers = {}
    app.state.training_extractor = None
    app.state.extractor_lock = threading.Lock()

    try:
        yield
    finally:
        if app.state.training_extractor is not None:
            app.state.training_extractor.close()
        app.state.personal_recognizers.clear()
        app.state.recognizer = None
        app.state.connections.clear()


app = FastAPI(
    title="谈指间 API",
    version="1.0",
    lifespan=lifespan,
)
app.include_router(router)
app.include_router(training_router)
app.mount(
    "/app",
    StaticFiles(
        directory=FRONTEND_DIRECTORY,
        html=True,
    ),
    name="frontend",
)


@app.middleware("http")
async def check_request_origin(request, call_next):
    is_api_request = request.url.path.startswith("/api/v1/")
    is_write_request = request.method not in ("GET", "HEAD", "OPTIONS")

    if is_api_request and is_write_request:
        if not allowed_origin(request.headers):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "code": "FORBIDDEN",
                        "message": "请求来源不允许",
                    }
                },
            )

    return await call_next(request)


@app.exception_handler(APIError)
async def handle_api_error(request, error):
    return JSONResponse(
        status_code=error.status,
        content={
            "error": {
                "code": error.code,
                "message": error.message,
            }
        },
    )


@app.exception_handler(RequestValidationError)
async def handle_validation_error(request, error):
    return JSONResponse(
        status_code=400,
        content={
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "请求参数不合法",
            }
        },
    )
