from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from typing import Optional, Dict, Any
from loguru import logger

class AppException(Exception):
    def __init__(self, code: int, message: str, details: Optional[Dict] = None):
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)

class DatabaseException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_500_INTERNAL_SERVER_ERROR, message, details)

class LLMException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, message, details)

class RedisException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_503_SERVICE_UNAVAILABLE, message, details)

class ValidationException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_400_BAD_REQUEST, message, details)

class NotFoundException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_404_NOT_FOUND, message, details)

class AuthException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_401_UNAUTHORIZED, message, details)

class RateLimitException(AppException):
    def __init__(self, message: str, details: Optional[Dict] = None):
        super().__init__(status.HTTP_429_TOO_MANY_REQUESTS, message, details)

async def exception_handler(request, exc: AppException) -> JSONResponse:
    logger.error(f"AppException: {exc.code} - {exc.message} - {exc.details}")
    return JSONResponse(
        status_code=exc.code,
        content={
            "error": exc.message,
            "code": exc.code,
            "details": exc.details,
            "timestamp": request.state.timestamp if hasattr(request.state, 'timestamp') else None
        }
    )

async def generic_exception_handler(request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unexpected error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": "Internal server error",
            "code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            "details": {"message": str(exc)}
        }
    )