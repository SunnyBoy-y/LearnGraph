from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse


from sqlalchemy.exc import OperationalError, TimeoutError as SQLAlchemyTimeoutError


class AppError(Exception):
    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=jsonable_encoder(
                {
                    "error": {
                        "code": exc.code,
                        "message": exc.message,
                        "details": exc.details,
                    }
                },
                custom_encoder={Exception: str},
            ),
        )

    @app.exception_handler(SQLAlchemyTimeoutError)
    async def handle_db_pool_timeout(_: Request, exc: SQLAlchemyTimeoutError) -> JSONResponse:
        return JSONResponse(
            status_code=503,
            headers={"Retry-After": "1"},
            content={
                "error": {
                    "code": "database_busy",
                    "message": "数据库当前繁忙，请稍后重试。",
                    "details": {"reason": "pool_timeout"},
                }
            },
        )

    @app.exception_handler(OperationalError)
    async def handle_db_operational_error(_: Request, exc: OperationalError) -> JSONResponse:
        from app.core.database import _is_sqlite_locked_error

        if _is_sqlite_locked_error(exc):
            return JSONResponse(
                status_code=503,
                headers={"Retry-After": "1"},
                content={
                    "error": {
                        "code": "database_busy",
                        "message": "数据库正在处理其他写入，请稍后重试。",
                        "details": {"reason": "database_locked"},
                    }
                },
            )
        raise exc
