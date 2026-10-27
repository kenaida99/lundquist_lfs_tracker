"""Username and password login in front of the whole web page and API.

One account, set with WEB_USERNAME / WEB_PASSWORD. The session is a signed cookie that stops
working when the password changes.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import logging
import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware

log = logging.getLogger(__name__)

SESSION_MAX_AGE_S = 7 * 24 * 3600
PUBLIC_PATHS = {"/login", "/logout"}
MIN_PASSWORD_LENGTH = 8
MAX_FAILURES = 5  # wrong passwords from one address ...
LOCKOUT_S = 300  # ... and it is locked out for this long


@dataclass(frozen=True)
class AuthConfig:
    username: str
    password: str
    session_secret: str
    secure_cookies: bool = False  # set when the page is served over https


def load_session_secret(path: Path) -> str:
    """SESSION_SECRET from the environment, else a random one kept in a file so logins survive restarts."""
    if secret := os.environ.get("SESSION_SECRET"):
        return secret
    try:
        if path.exists() and (secret := path.read_text(encoding="utf-8").strip()):
            return secret
    except OSError as error:
        log.warning("could not read %s (%s); making a new session secret", path, error)
    secret = secrets.token_urlsafe(48)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secret, encoding="utf-8")
    except OSError as error:
        log.warning("could not save %s (%s); logins will end when the tracker restarts", path, error)
    return secret


def same(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


class LoginLimiter:
    """Locks an address out after too many wrong passwords."""

    def __init__(self, clock=time.monotonic) -> None:
        self._clock = clock
        self._failures: dict[str, tuple[int, float]] = {}  # address -> (count, locked until)

    def locked(self, key: str) -> bool:
        count, until = self._failures.get(key, (0, 0.0))
        if count >= MAX_FAILURES and self._clock() < until:
            return True
        if count >= MAX_FAILURES:
            del self._failures[key]
        return False

    def failed(self, key: str) -> None:
        count, _ = self._failures.get(key, (0, 0.0))
        self._failures[key] = (count + 1, self._clock() + LOCKOUT_S)

    def succeeded(self, key: str) -> None:
        self._failures.pop(key, None)


def login_page(message: str = "", status: int = 200) -> HTMLResponse:
    note = f"<p style='color:#ff6b6b'>{html.escape(message)}</p>" if message else ""
    field = "font:inherit;padding:.6rem;border-radius:8px;border:1px solid #232b3b;background:#171d2b;color:inherit"
    body = (
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        "<title>Sign in - LFS Tracker</title>"
        "<body style='font:16px system-ui;background:#0a0d13;color:#e8ecf4;display:grid;place-items:center;height:100vh;margin:0'>"
        "<form method=post action=/login style='display:grid;gap:.7rem;width:min(320px,90vw)'>"
        f"<h2 style='margin:0 0 .5rem'>LFS Tracker</h2>{note}"
        f"<input name=username placeholder=Username autocomplete=username autofocus required style='{field}'>"
        f"<input name=password type=password placeholder=Password autocomplete=current-password required style='{field}'>"
        "<button style='font:inherit;font-weight:600;padding:.6rem;border-radius:8px;border:0;background:#3f6fe8;color:#fff;cursor:pointer'>"
        "Sign in</button></form>"
    )
    return HTMLResponse(body, status_code=status)


def install_auth(app: FastAPI, config: AuthConfig) -> None:
    limiter = LoginLimiter()
    # What a valid session holds: it changes when the username or password does.
    token = hmac.new(
        config.session_secret.encode(), f"{config.username}\0{config.password}".encode(), hashlib.sha256
    ).hexdigest()

    def signed_in(request: Request) -> bool:
        return same(str(request.session.get("auth", "")), token)

    async def guard(request: Request, call_next) -> Response:
        if request.url.path in PUBLIC_PATHS or signed_in(request):
            return await call_next(request)
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "not signed in"}, status_code=401)
        return RedirectResponse("/login", status_code=303)

    # Added last = outermost, so the session is loaded before the guard runs.
    app.add_middleware(BaseHTTPMiddleware, dispatch=guard)
    app.add_middleware(
        SessionMiddleware,
        secret_key=config.session_secret,
        max_age=SESSION_MAX_AGE_S,
        same_site="lax",  # the cookie is not sent on cross-site POSTs
        https_only=config.secure_cookies,
    )

    @app.get("/login")
    async def login_form(request: Request) -> Response:
        return RedirectResponse("/", status_code=303) if signed_in(request) else login_page()

    @app.post("/login")
    async def login(request: Request) -> Response:
        address = request.client.host if request.client else ""
        if limiter.locked(address):
            return login_page("Too many wrong passwords. Try again in a few minutes.", 429)
        form = {k: v[0] for k, v in parse_qs((await request.body()).decode(errors="replace")).items()}
        # Both are compared, so the reply does not show which one was wrong.
        user_ok = same(form.get("username", ""), config.username)
        password_ok = same(form.get("password", ""), config.password)
        if not (user_ok and password_ok):
            limiter.failed(address)
            log.warning("failed sign-in from %s", address)
            return login_page("Wrong username or password.", 401)
        limiter.succeeded(address)
        request.session.clear()
        request.session["auth"] = token
        request.session["user"] = config.username
        return RedirectResponse("/", status_code=303)

    @app.post("/logout")
    async def logout(request: Request) -> Response:
        request.session.clear()
        return JSONResponse({"signed_out": True})
