"""Nathavaram voter list application."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from urllib.parse import quote

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import __version__
from app.config import ROOT, load_settings
from app import database
from app.database import dispose_db, get_db, init_db
from app.deps import csrf_guard
from app.migrate import run_migrations
from app.routers import auth, reports, sync, users, voters
from app.services.bootstrap import seed_admin
from app.services.throttle import LoginThrottle
from app.web.routes import LoginRequired, PasswordChangeRequired
from app.web.routes import router as web_router
from app.web.templates import render

logger = logging.getLogger("nathavaram")


def create_app() -> FastAPI:
    settings = load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logging.basicConfig(level=logging.INFO)
        run_migrations(settings.database_url)
        init_db(settings.database_url)
        if database.SessionLocal is None:
            raise RuntimeError("Database session factory was not created")
        db = database.SessionLocal()
        try:
            seed_admin(db, settings)
        finally:
            db.close()
        yield
        dispose_db()

    app = FastAPI(
        title=settings.app_name,
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        dependencies=[Depends(csrf_guard)],
    )
    app.state.settings = settings
    # In-memory, per-process login limiter; see docs/SECURITY.md for the caveats.
    app.state.login_throttle = LoginThrottle()

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self'; style-src 'self'; "
            "font-src 'self'; script-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'self'"
        )
        return response

    @app.exception_handler(LoginRequired)
    async def need_login(request: Request, exc: LoginRequired):
        return RedirectResponse(f"/login?next={quote(exc.next_path)}", status_code=303)

    @app.exception_handler(PasswordChangeRequired)
    async def need_password_change(request: Request, exc: PasswordChangeRequired):
        return RedirectResponse("/change-password", status_code=303)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        # A dict detail {"code", "message"} becomes {"detail": message, "code": code}.
        body: dict = {"detail": exc.detail}
        if isinstance(exc.detail, dict) and "code" in exc.detail:
            body = {"detail": exc.detail.get("message", ""), "code": exc.detail["code"]}
        if request.url.path.startswith("/api") or request.url.path == "/healthz":
            return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)
        if exc.status_code == 404:
            return render(request, "404.html", {"user": None}, status_code=404)
        return JSONResponse(body, status_code=exc.status_code, headers=exc.headers)

    @app.get("/healthz")
    def healthz(db: Session = Depends(get_db)) -> dict:
        db.execute(text("SELECT 1"))
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(voters.router)
    app.include_router(reports.router)
    app.include_router(sync.router)
    app.include_router(web_router)
    app.mount("/static", StaticFiles(directory=str(ROOT / "app" / "static")), name="static")
    logger.info("Nathavaram voter list %s ready", __version__)
    return app
