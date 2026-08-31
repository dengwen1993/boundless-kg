"""FastAPI middleware: CORS + structured logging + error handler."""

from __future__ import annotations

import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from src.config import get_api_cors_origins, get_settings


def configure_cors(app: FastAPI) -> None:
    origins = get_api_cors_origins()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )


def configure_logging(app: FastAPI) -> None:
    """挂载项目日志系统（logs/{debug,info,error}.log + 控制台）。幂等。"""
    from src.observability.logging_config import setup_logging

    setup_logging(console_level=get_settings().log_level)


async def access_log_middleware(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - start) * 1000
    logging.getLogger("api.access").info(
        "%s %s -> %d (%.1f ms)",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response


def install_middlewares(app: FastAPI) -> None:
    configure_cors(app)
    configure_logging(app)
    app.middleware("http")(access_log_middleware)


class DomainError(Exception):
    """Domain-layer exception raised through the API."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


async def domain_error_handler(request: Request, exc: DomainError):
    return JSONResponse(
        status_code=exc.status,
        content={"error": exc.code, "message": exc.message},
    )


__all__ = [
    "configure_cors",
    "configure_logging",
    "install_middlewares",
    "DomainError",
    "domain_error_handler",
]