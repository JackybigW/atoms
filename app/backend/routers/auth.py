import logging
import os
from typing import Optional
from urllib.parse import urlencode

import httpx
from core.auth import (
    IDTokenValidationError,
    build_authorization_url,
    build_logout_url,
    generate_code_challenge,
    generate_code_verifier,
    generate_nonce,
    generate_state,
    validate_id_token,
)
from core.config import settings
from core.database import get_db
from dependencies.auth import get_current_user
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from models.auth import User
from pydantic import BaseModel, EmailStr
from schemas.auth import (
    PlatformTokenExchangeRequest,
    TokenExchangeResponse,
    UserResponse,
)
from services.auth import AuthError, AuthService
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/api/v1/auth", tags=["authentication"])
logger = logging.getLogger(__name__)


def _local_patch(url: str) -> str:
    """Patch URL for local development."""
    if os.getenv("LOCAL_PATCH", "").lower() not in ("true", "1"):
        return url

    patched_url = url.replace("https://", "http://").replace(":8000", ":3000")
    logger.debug("[get_dynamic_backend_url] patching URL from %s to %s", url, patched_url)
    return patched_url


def get_dynamic_backend_url(request: Request) -> str:
    """Get backend URL dynamically from request headers.

    Priority: mgx-external-domain > x-forwarded-host > settings.backend_url
    """
    mgx_external_domain = request.headers.get("mgx-external-domain")
    x_forwarded_host = request.headers.get("x-forwarded-host")
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme

    effective_host = mgx_external_domain or x_forwarded_host
    if not effective_host:
        fallback_url = _local_patch(settings.backend_url)
        logger.debug("[get_dynamic_backend_url] No proxy host found, fallback to %s", fallback_url)
        return fallback_url

    dynamic_url = _local_patch(f"{scheme}://{effective_host}")
    logger.debug(
        "[get_dynamic_backend_url] mgx-external-domain=%s, x-forwarded-host=%s, scheme=%s, dynamic_url=%s",
        mgx_external_domain,
        x_forwarded_host,
        scheme,
        dynamic_url,
    )
    return dynamic_url


def get_frontend_app_url(request: Request) -> str:
    """Get frontend app URL for browser redirects after auth completes."""
    frontend_url = _local_patch(getattr(settings, "frontend_url", ""))
    if frontend_url:
        return frontend_url.rstrip("/")

    origin = request.headers.get("origin")
    if origin:
        return _local_patch(origin).rstrip("/")

    return get_dynamic_backend_url(request)


def derive_name_from_email(email: str) -> str:
    return email.split("@", 1)[0] if email else ""


@router.get("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    """Start OIDC login flow with PKCE."""
    state = generate_state()
    nonce = generate_nonce()
    code_verifier = generate_code_verifier()
    code_challenge = generate_code_challenge(code_verifier)

    # Store state, nonce, and code verifier in database
    auth_service = AuthService(db)
    await auth_service.store_oidc_state(state, nonce, code_verifier)

    # Build redirect_uri dynamically from request
    backend_url = get_dynamic_backend_url(request)
    redirect_uri = f"{backend_url}/api/v1/auth/callback"
    logger.info("[login] Starting OIDC flow with redirect_uri=%s", redirect_uri)

    auth_url = build_authorization_url(state, nonce, code_challenge, redirect_uri=redirect_uri)
    return RedirectResponse(
        url=auth_url,
        status_code=status.HTTP_302_FOUND,
        headers={"X-Request-ID": state},
    )


@router.get("/callback")
async def callback(
    request: Request,
    code: Optional[str] = None,
    state: Optional[str] = None,
    error: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """Handle OIDC callback."""
    backend_url = get_dynamic_backend_url(request)
    frontend_url = get_frontend_app_url(request)

    def redirect_with_error(message: str) -> RedirectResponse:
        fragment = urlencode({"msg": message})
        return RedirectResponse(
            url=f"{frontend_url}/auth/error?{fragment}",
            status_code=status.HTTP_302_FOUND,
        )

    if error:
        return redirect_with_error(f"OIDC error: {error}")

    if not code or not state:
        return redirect_with_error("Missing code or state parameter")

    # Validate state using database
    auth_service = AuthService(db)
    temp_data = await auth_service.get_and_delete_oidc_state(state)
    if not temp_data:
        return redirect_with_error("Invalid or expired state parameter")

    nonce = temp_data["nonce"]
    code_verifier = temp_data.get("code_verifier")

    try:
        # Build redirect_uri dynamically from request
        redirect_uri = f"{backend_url}/api/v1/auth/callback"
        logger.info("[callback] Exchanging code for tokens with redirect_uri=%s", redirect_uri)

        # Exchange authorization code for tokens with PKCE
        token_data = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": settings.oidc_client_id,
            "client_secret": settings.oidc_client_secret,
        }

        # Add PKCE code verifier if available
        if code_verifier:
            token_data["code_verifier"] = code_verifier

        token_url = getattr(settings, 'oidc_token_url', None) or f"{settings.oidc_issuer_url}/token"
        try:
            async with httpx.AsyncClient() as client:
                token_response = await client.post(
                    token_url,
                    data=token_data,
                    headers={"Content-Type": "application/x-www-form-urlencoded", "X-Request-ID": state},
                )
        except httpx.HTTPError as e:
            logger.error(
                "[callback] Token exchange HTTP error: url=%s, error=%s",
                token_url,
                repr(e),
                exc_info=True,
            )
            return redirect_with_error(f"Token exchange failed: {type(e).__name__} {e}")

        if token_response.status_code != 200:
            logger.error(
                "[callback] Token exchange failed: url=%s, status_code=%s, response=%s",
                token_url,
                token_response.status_code,
                token_response.text,
            )
            return redirect_with_error(f"Token exchange failed: {token_response.text}")

        tokens = token_response.json()

        # Validate ID token
        id_token = tokens.get("id_token")
        if not id_token:
            return redirect_with_error("No ID token received")

        id_claims = await validate_id_token(id_token, access_token=tokens.get("access_token"))

        # Validate nonce
        if id_claims.get("nonce") != nonce:
            return redirect_with_error("Invalid nonce")

        # Get or create user
        email = id_claims.get("email", "")
        name = id_claims.get("name") or derive_name_from_email(email)
        user = await auth_service.get_or_create_user(platform_sub=id_claims["sub"], email=email, name=name)

        # Issue application JWT token encapsulating user information
        app_token, expires_at, _ = await auth_service.issue_app_token(user=user)

        fragment = urlencode(
            {
                "token": app_token,
                "expires_at": int(expires_at.timestamp()),
                "token_type": "Bearer",
            }
        )

        redirect_url = f"{frontend_url}/auth/callback?{fragment}"
        logger.info("[callback] OIDC callback successful, redirecting to %s", redirect_url)
        redirect_response = RedirectResponse(
            url=redirect_url,
            status_code=status.HTTP_302_FOUND,
        )
        return redirect_response

    except IDTokenValidationError as e:
        # Redirect to error page with validation details
        return redirect_with_error(f"Authentication failed: {e.message}")
    except HTTPException as e:
        # Redirect to error page with the original detail message
        return redirect_with_error(str(e.detail))
    except Exception as e:
        logger.exception(f"Unexpected error in OIDC callback: {e}")
        return redirect_with_error(
            "Authentication processing failed. Please try again or contact support if the issue persists."
        )


@router.post("/token/exchange", response_model=TokenExchangeResponse)
async def exchange_platform_token(
    payload: PlatformTokenExchangeRequest,
    db: AsyncSession = Depends(get_db),
):
    """Exchange Platform token for app token. Admin gets admin role, team members get user role."""
    logger.info("[token/exchange] Received platform token exchange request")

    verify_url = f"{settings.oidc_issuer_url}/platform/tokens/verify"
    logger.debug(f"[token/exchange] Verifying token with issuer: {verify_url}")

    try:
        async with httpx.AsyncClient() as client:
            verify_response = await client.post(
                verify_url,
                json={"platform_token": payload.platform_token},
                headers={"Content-Type": "application/json"},
            )
        logger.debug(f"[token/exchange] Issuer response status: {verify_response.status_code}")
    except httpx.HTTPError as exc:
        logger.error(f"[token/exchange] HTTP error verifying platform token: {exc}", exc_info=True)
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Unable to verify platform token") from exc

    try:
        verify_body = verify_response.json()
        logger.debug(f"[token/exchange] Issuer response body: {verify_body}")
    except ValueError:
        logger.error(f"[token/exchange] Failed to parse issuer response as JSON: {verify_response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Invalid response from platform token verification service",
        )

    if not isinstance(verify_body, dict):
        logger.error(f"[token/exchange] Unexpected response type: {type(verify_body)}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Unexpected response from platform token verification service",
        )

    if verify_response.status_code != status.HTTP_200_OK or not verify_body.get("success"):
        message = verify_body.get("message", "") if isinstance(verify_body, dict) else ""
        logger.warning(
            f"[token/exchange] Token verification failed: status={verify_response.status_code}, message={message}"
        )
        raise HTTPException(
            status_code=verify_response.status_code,
            detail=message or "Platform token verification failed",
        )

    payload_data = verify_body.get("data") or {}
    raw_user_id = payload_data.get("user_id")
    logger.info(f"[token/exchange] Token verified, platform_user_id={raw_user_id}, email={payload_data.get('email')}")

    if not raw_user_id:
        logger.error("[token/exchange] Platform token payload missing user_id")
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Platform token payload missing user_id")

    platform_user_id = str(raw_user_id)
    is_admin = platform_user_id == str(settings.admin_user_id)
    role = "admin" if is_admin else "user"

    logger.info(f"[token/exchange] User verified: platform_user_id={platform_user_id}, role={role}")
    auth_service = AuthService(db)

    user_email = payload_data.get("email", "") or (getattr(settings, "admin_user_email", "") if is_admin else "")
    user_name = payload_data.get("name") or payload_data.get("username")
    if not user_name:
        user_name = derive_name_from_email(user_email)

    user = User(id=platform_user_id, email=user_email, name=user_name, role=role)
    logger.debug(
        f"[token/exchange] User object for token issuance: id={user.id}, email={user.email}, role={user.role}"
    )

    app_token, expires_at, _ = await auth_service.issue_app_token(user=user)
    logger.info(f"[token/exchange] Token issued successfully for user_id={user.id}, expires_at={expires_at}")

    return TokenExchangeResponse(
        token=app_token,
    )


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(current_user: UserResponse = Depends(get_current_user)):
    """Get current user info."""
    return current_user


@router.get("/logout")
async def logout():
    """Logout user."""
    logout_url = build_logout_url()
    return {"redirect_url": logout_url}


# =========================================================================== #
# Email / password auth endpoints                                              #
# =========================================================================== #


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str


class AuthProvidersResponse(BaseModel):
    google: bool


def oidc_is_configured() -> bool:
    issuer_url = (settings.oidc_issuer_url or "").strip()
    client_id = (settings.oidc_client_id or "").strip()
    client_secret = (settings.oidc_client_secret or "").strip()
    placeholders = ("your-oidc-provider.com", "your-client-id", "your-client-secret")

    if not issuer_url or not client_id or not client_secret:
        return False

    return not any(token in value for token in placeholders for value in (issuer_url, client_id, client_secret))


def _auth_error_response(exc: AuthError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.message})


@router.get("/providers", response_model=AuthProvidersResponse)
async def auth_providers():
    """Return which public auth providers are available in this environment."""
    return AuthProvidersResponse(google=oidc_is_configured())


@router.post("/register", status_code=201)
async def register(
    payload: RegisterRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user with email and password."""
    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    auth_service = AuthService(db)
    try:
        await auth_service.register_with_password(
            email=payload.email,
            password=payload.password,
            name=payload.name,
        )
    except AuthError as exc:
        return _auth_error_response(exc)
    return {"message": "Account created. Please check your email to verify your address."}


@router.post("/login/password")
async def login_with_password(
    payload: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Sign in with email and password, returns JWT."""
    auth_service = AuthService(db)
    try:
        token, expires_at, _ = await auth_service.login_with_password(payload.email, payload.password)
    except AuthError as exc:
        return _auth_error_response(exc)
    return {
        "token": token,
        "expires_at": int(expires_at.timestamp()),
        "token_type": "Bearer",
    }


class VerifyEmailRequest(BaseModel):
    token: str

@router.post("/verify-email", status_code=200)
async def verify_email(
    payload: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify email address via token."""
    auth_service = AuthService(db)
    try:
        await auth_service.verify_email(payload.token)
        return {"message": "Email verified successfully"}
    except AuthError as exc:
        return _auth_error_response(exc)

class ResendVerificationRequest(BaseModel):
    email: EmailStr

@router.post("/resend-verification", status_code=200)
async def resend_verification(
    payload: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend verification email."""
    auth_service = AuthService(db)
    await auth_service.resend_verification_email(payload.email)
    return {"message": "If the email is registered and unverified, a new link has been sent."}


@router.post("/forgot-password", status_code=200)
async def forgot_password(
    payload: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Send password reset email (always returns 200 to prevent email enumeration)."""
    auth_service = AuthService(db)
    await auth_service.forgot_password(payload.email)
    return {"message": "If that email is registered, a reset link has been sent."}


@router.post("/reset-password", status_code=200)
async def reset_password(
    payload: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Apply new password using a reset token."""
    if len(payload.new_password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")
    auth_service = AuthService(db)
    try:
        await auth_service.reset_password(payload.token, payload.new_password)
    except AuthError as exc:
        return _auth_error_response(exc)
    return {"message": "Password updated. You can now sign in with your new password."}
