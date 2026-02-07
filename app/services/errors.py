"""
RiskSentinel — Error Handling & Exception Classes

Centralised exception handling with proper HTTP status codes and
safe error messages (avoids information leakage).
"""

import logging
from typing import Any, Dict, Optional

from fastapi import HTTPException, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError

logger = logging.getLogger("risksentinel.errors")


# ===========================================================================
# Custom Exceptions (domain-specific)
# ===========================================================================
class RiskSentinelException(Exception):
    """Base exception for all RiskSentinel errors."""
    pass


class TransactionError(RiskSentinelException):
    """Transaction-related errors."""
    pass


class ScoringError(RiskSentinelException):
    """Scoring pipeline errors."""
    pass


class RuleEngineError(RiskSentinelException):
    """Rule evaluation errors."""
    pass


class DatabaseError(RiskSentinelException):
    """Database operation errors."""
    pass


class KafkaError(RiskSentinelException):
    """Kafka communication errors."""
    pass


class MLError(RiskSentinelException):
    """ML model inference errors."""
    pass


class AuthenticationError(RiskSentinelException):
    """Authentication/Authorization errors."""
    pass


class ValidationError(RiskSentinelException):
    """Input validation errors."""
    pass


# ===========================================================================
# HTTP Error Response Factory
# ===========================================================================
class ErrorResponse:
    """Standardised error response format."""

    def __init__(
        self,
        error_code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        details: Optional[Dict[str, Any]] = None,
        request_id: Optional[str] = None,
    ):
        self.error_code = error_code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        self.request_id = request_id

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return {
            "error": {
                "code": self.error_code,
                "message": self.message,
                "request_id": self.request_id,
            },
            **({'details': self.details} if self.details else {}),
        }


# ===========================================================================
# Exception to HTTP Response Mapping
# ===========================================================================
def exception_to_response(
    exc: Exception,
    request_id: Optional[str] = None,
) -> tuple[JSONResponse, str]:
    """
    Convert an exception to an HTTP response.

    Parameters
    ----------
    exc : Exception
        The exception to handle
    request_id : str
        Request ID for tracking

    Returns
    -------
    response : JSONResponse
    log_level : str
        Logging level (error, warning, info)
    """

    # Pydantic validation errors
    if isinstance(exc, ValidationError):
        error_resp = ErrorResponse(
            error_code="VALIDATION_ERROR",
            message="Input validation failed",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            details={"errors": str(exc)},
            request_id=request_id,
        )
        return JSONResponse(
            status_code=error_resp.status_code,
            content=error_resp.to_dict(),
        ), "warning"

    # Transaction errors
    if isinstance(exc, TransactionError):
        error_resp = ErrorResponse(
            error_code="TRANSACTION_ERROR",
            message="Failed to process transaction",
            status_code=status.HTTP_400_BAD_REQUEST,
            request_id=request_id,
        )
        logger.error("TransactionError: %s", exc)
        return JSONResponse(
            status_code=error_resp.status_code,
            content=error_resp.to_dict(),
        ), "error"

    # Scoring errors
    if isinstance(exc, ScoringError):
        error_resp = ErrorResponse(
            error_code="SCORING_ERROR",
            message="Risk scoring pipeline failed",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=request_id,
        )
        logger.error("ScoringError: %s", exc)
        return JSONResponse(
            status_code=error_resp.status_code,
            content=error_resp.to_dict(),
        ), "error"

    # Database errors
    if isinstance(exc, DatabaseError):
        error_resp = ErrorResponse(
            error_code="DATABASE_ERROR",
            message="Database operation failed",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            request_id=request_id,
        )
        logger.error("DatabaseError: %s", exc)
        return JSONResponse(
            status_code=error_resp.status_code,
            content=error_resp.to_dict(),
        ), "error"

    # Kafka errors (non-critical, degraded service)
    if isinstance(exc, KafkaError):
        error_resp = ErrorResponse(
            error_code="KAFKA_ERROR",
            message="Event stream publishing temporarily unavailable",
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            request_id=request_id,
        )
        logger.warning("KafkaError: %s", exc)
        return JSONResponse(
            status_code=error_resp.status_code,
            content=error_resp.to_dict(),
        ), "warning"

    # ML errors (fallback to non-ML scoring)
    if isinstance(exc, MLError):
        # This is typically non-fatal; log but don't fail
        logger.warning("MLError: %s (falling back to non-ML scoring)", exc)
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "warning": "ML model unavailable; using non-ML scoring",
            },
        ), "warning"

    # Authentication errors
    if isinstance(exc, AuthenticationError):
        error_resp = ErrorResponse(
            error_code="AUTHENTICATION_ERROR",
            message="Invalid or missing authentication",
            status_code=status.HTTP_401_UNAUTHORIZED,
            request_id=request_id,
        )
        logger.warning("AuthenticationError: unauthorized access attempt")
        return JSONResponse(
            status_code=error_resp.status_code,
            content=error_resp.to_dict(),
            headers={"WWW-Authenticate": "Bearer"},
        ), "warning"

    # Generic error (never expose full traceback to client)
    error_resp = ErrorResponse(
        error_code="INTERNAL_ERROR",
        message="An internal error occurred. Please try again later.",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        request_id=request_id,
    )
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=error_resp.status_code,
        content=error_resp.to_dict(),
    ), "error"
