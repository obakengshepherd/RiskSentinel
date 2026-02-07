"""
RiskSentinel — Security & Authentication Layer

Provides:
- JWT token generation and validation
- API key validation
- Password hashing (fallback for future user management)
- Claims extraction and validation
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any

from jose import JWTError, jwt
from passlib.context import CryptContext
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthCredentials, APIKeyCookie

from app.config import settings

logger = logging.getLogger("risksentinel.security")

# ---------------------------------------------------------------------------
# Password hashing context (for future user management)
# ---------------------------------------------------------------------------
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# ---------------------------------------------------------------------------
# Security schemes
# ---------------------------------------------------------------------------
http_bearer = HTTPBearer(
    scheme_name="Bearer",
    description="JWT Bearer token"
)

api_key_header = APIKeyCookie(name="X-API-Key", auto_error=False)


# ---------------------------------------------------------------------------
# JWT Operations
# ---------------------------------------------------------------------------
def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """
    Create a JWT access token.

    Parameters
    ----------
    data        : dict of claims to encode
    expires_delta : custom expiration delta (defaults to config)

    Returns
    -------
    token : str
    """
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + settings.jwt_expiration

    to_encode.update({"exp": expire, "iat": datetime.now(timezone.utc)})

    encoded_jwt = jwt.encode(
        to_encode,
        settings.JWT_SECRET_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    return encoded_jwt


def verify_token(token: str) -> Dict[str, Any]:
    """
    Verify and decode a JWT token.

    Parameters
    ----------
    token : str
        JWT token

    Returns
    -------
    claims : dict
        Decoded token claims

    Raises
    ------
    HTTPException
        If token is invalid or expired
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET_KEY,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return payload
    except JWTError as exc:
        logger.warning("JWT verification failed: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


# ---------------------------------------------------------------------------
# Password hashing (future user management)
# ---------------------------------------------------------------------------
def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return pwd_context.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a plain password against its hash."""
    return pwd_context.verify(plain_password, hashed_password)


# ---------------------------------------------------------------------------
# Dependency for FastAPI route protection
# ---------------------------------------------------------------------------
async def get_current_user(
    credentials: Optional[HTTPAuthCredentials] = Depends(http_bearer),
    api_key: Optional[str] = Depends(api_key_header),
) -> Dict[str, Any]:
    """
    Validate and extract claims from JWT token or API key.

    Supports:
    1. JWT Bearer token (Authorization: Bearer <token>)
    2. API Key (X-API-Key: <key> or cookie)

    Parameters
    ----------
    credentials : HTTPAuthCredentials
        From Authorization header
    api_key : str
        From X-API-Key header or cookie

    Returns
    -------
    claims : dict
        Token claims including sub (subject), scopes, etc.

    Raises
    ------
    HTTPException
        If authentication fails
    """
    if not settings.AUTH_ENABLED:
        # Development mode: allow unauthenticated access
        return {"sub": "system", "scopes": ["*"]}

    # Try JWT first
    if credentials:
        token = credentials.credentials
        claims = verify_token(token)
        return claims

    # Fallback to API key
    if settings.API_KEY_ENABLED and api_key:
        # Validate API key (in production, check against database)
        # For now, accept a predefined key
        if api_key == settings.SECRET_KEY:  # Placeholder validation
            return {
                "sub": "api_key_user",
                "scopes": ["transactions:read", "transactions:write"],
            }
        logger.warning("Invalid API key attempt")

    # No valid credentials found
    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


async def get_current_admin(
    current_user: Dict[str, Any] = Depends(get_current_user),
) -> Dict[str, Any]:
    """
    Verify that the current user has admin privileges.

    Parameters
    ----------
    current_user : dict
        Claims from get_current_user

    Returns
    -------
    claims : dict
        Verified admin claims

    Raises
    ------
    HTTPException
        If user is not an admin
    """
    scopes: list = current_user.get("scopes", [])
    if "*" not in scopes and "admin" not in scopes:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Insufficient permissions for this operation",
        )
    return current_user


# ---------------------------------------------------------------------------
# Token refresh (optional)
# ---------------------------------------------------------------------------
def create_token_pair(
    subject: str,
    scopes: list[str] = None,
) -> Dict[str, str]:
    """
    Create both access and refresh tokens.

    Parameters
    ----------
    subject : str
        Subject (user ID, email, etc.)
    scopes : list
        List of permission scopes

    Returns
    -------
    tokens : dict
        {"access_token": str, "refresh_token": str, "token_type": "bearer"}
    """
    if scopes is None:
        scopes = ["transactions:read", "transactions:write"]

    access_token_data = {
        "sub": subject,
        "scopes": scopes,
        "type": "access",
    }
    access_token = create_access_token(access_token_data)

    refresh_token_data = {
        "sub": subject,
        "type": "refresh",
    }
    refresh_token = create_access_token(
        refresh_token_data,
        expires_delta=settings.jwt_refresh_expiration,
    )

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
    }
