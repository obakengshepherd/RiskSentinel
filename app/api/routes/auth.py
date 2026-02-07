"""
RiskSentinel — Authentication API

POST /api/v1/auth/token         → issue JWT token
POST /api/v1/auth/refresh       → refresh JWT token
GET  /api/v1/auth/me            → get current user info
"""

import logging
from typing import Dict, Any

from fastapi import APIRouter, Depends, HTTPException, status
from datetime import datetime, timezone

from app.models.schemas import TokenResponse, LoginRequest
from app.services.security import (
    create_token_pair,
    verify_token,
    get_current_user,
)
from app.config import settings

logger = logging.getLogger("risksentinel.api.auth")
router = APIRouter()


# ===========================================================================
# POST /api/v1/auth/token
# ===========================================================================
@router.post(
    "/token",
    response_model=TokenResponse,
    summary="Get Access Token",
    description="Issue a JWT access token and refresh token. For testing in development mode.",
)
async def get_token(
    credentials: LoginRequest,
):
    """
    Issue JWT tokens for use in authenticated requests.

    **Development Mode Notes:**
    - In production, integrate with your actual user database or SSO provider
    - Current implementation accepts any username/password for testing
    - Always use HTTPS in production
    """
    username = credentials.username
    password = credentials.password

    # TODO: Validate credentials against actual user database
    # For now, accept any non-empty credentials in development
    if not username or not password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username and password required",
        )

    if settings.is_production():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Token endpoint disabled in production. Use SSO provider.",
        )

    # Create token pair
    tokens = create_token_pair(
        subject=username,
        scopes=["transactions:read", "transactions:write", "alerts:read", "alerts:write"],
    )

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=int(settings.jwt_expiration.total_seconds()),
    )


# ===========================================================================
# POST /api/v1/auth/refresh
# ===========================================================================
@router.post(
    "/refresh",
    response_model=TokenResponse,
    summary="Refresh Access Token",
    description="Use a refresh token to issue a new access token.",
)
async def refresh_token(
    request: Dict[str, Any],
):
    """
    Refresh an expired access token using a refresh token.

    Request body:
    ```json
    {
      "refresh_token": "your_refresh_token_here"
    }
    ```
    """
    refresh_token_str = request.get("refresh_token")

    if not refresh_token_str:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="refresh_token required",
        )

    # Verify refresh token
    try:
        claims = verify_token(refresh_token_str)
    except Exception as exc:
        logger.warning("Invalid refresh token: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
        ) from exc

    if claims.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token is not a refresh token",
        )

    # Issue new access token
    subject = claims.get("sub")
    tokens = create_token_pair(subject=subject)

    return TokenResponse(
        access_token=tokens["access_token"],
        refresh_token=tokens["refresh_token"],
        token_type=tokens["token_type"],
        expires_in=int(settings.jwt_expiration.total_seconds()),
    )


# ===========================================================================
# GET /api/v1/auth/me
# ===========================================================================
@router.get(
    "/me",
    summary="Get Current User",
    description="Retrieve information about the currently authenticated user.",
)
async def get_current_user_info(
    current_user: Dict[str, Any] = Depends(get_current_user),
):
    """Get current authenticated user information."""
    return {
        "subject": current_user.get("sub"),
        "scopes": current_user.get("scopes", []),
        "token_issued_at": datetime.now(timezone.utc).isoformat(),
        "expires_in": settings.JWT_EXPIRATION_HOURS * 3600,  # seconds
    }
