import base64
import hashlib
import hmac
import json
import os
import time

from fastapi import HTTPException, Request, Response

COOKIE = "daily_health_admin"
SESSION_HOURS = 12


def admin_username() -> str:
    return os.environ.get("ADMIN_USERNAME", "admin")


def admin_password() -> str:
    return os.environ.get("ADMIN_PASSWORD", "")


def session_secret() -> str:
    return os.environ.get("SESSION_SECRET", "change-this-session-secret")


def secrets_equal(left: str, right: str) -> bool:
    left_bytes = left.encode()
    right_bytes = right.encode()
    if len(left_bytes) != len(right_bytes):
        hmac.compare_digest(left_bytes, left_bytes)
        return False
    return hmac.compare_digest(left_bytes, right_bytes)


def _encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode(raw: str) -> bytes:
    padding = "=" * (-len(raw) % 4)
    return base64.urlsafe_b64decode(raw + padding)


def issue_token(username: str) -> str:
    payload = json.dumps(
        {"u": username, "exp": int(time.time()) + SESSION_HOURS * 3600},
        separators=(",", ":"),
    ).encode()
    signature = hmac.new(session_secret().encode(), payload, hashlib.sha256).digest()
    return f"{_encode(payload)}.{_encode(signature)}"


def token_is_valid(token: str | None) -> bool:
    if not token or "." not in token:
        return False
    try:
        payload_raw, signature_raw = token.split(".", 1)
        payload = _decode(payload_raw)
        expected = hmac.new(session_secret().encode(), payload, hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(signature_raw)):
            return False
        data = json.loads(payload)
        if data.get("u") != admin_username():
            return False
        return int(data.get("exp", 0)) >= int(time.time())
    except Exception:
        return False


def is_admin(request: Request) -> bool:
    if not admin_password():
        return False
    return token_is_valid(request.cookies.get(COOKIE))


def require_admin(request: Request) -> None:
    if not admin_password():
        raise HTTPException(
            status_code=503,
            detail="Admin login is not configured. Set ADMIN_PASSWORD.",
        )
    if not token_is_valid(request.cookies.get(COOKIE)):
        raise HTTPException(status_code=401, detail="Admin login is required.")


def set_admin_cookie(response: Response, username: str) -> None:
    response.set_cookie(
        COOKIE,
        issue_token(username),
        httponly=True,
        samesite="lax",
        max_age=SESSION_HOURS * 3600,
        path="/",
    )


def clear_admin_cookie(response: Response) -> None:
    response.delete_cookie(COOKIE, path="/")
