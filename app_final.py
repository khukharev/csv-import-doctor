import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request

from config import APP_VERSION, Settings, load_settings
from csv_profile import IMPLEMENTED_CHECK_IDS, ProfileError, load_profile

LOGGER = logging.getLogger("csv_import_doctor")


class UTCFormatter(logging.Formatter):
    """Format log timestamps in UTC, whatever the time zone of the machine."""

    converter = time.gmtime


def configure_logging(level: str) -> None:
    if not any(getattr(handler, "csv_doctor_handler", False) for handler in LOGGER.handlers):
        handler = logging.StreamHandler()
        handler.csv_doctor_handler = True
        handler.setFormatter(
            UTCFormatter(
                "%(asctime)sZ level=%(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
        )
        LOGGER.addHandler(handler)
    LOGGER.setLevel(level)


def create_app(settings: Settings | None = None, profile_path: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        actual_settings = settings if settings is not None else load_settings()
        configure_logging(actual_settings.log_level)
        try:
            profile = load_profile(profile_path)
        except ProfileError as exc:
            # The message names the file and the problem, never any user data.
            LOGGER.error("event=profile_load_failed reason=%s", exc)
            raise
        application.state.settings = actual_settings
        application.state.profile = profile
        application.title = actual_settings.app_name
        # Rebuild the OpenAPI schema if it was requested before startup.
        application.openapi_schema = None
        LOGGER.info(
            "event=application_started version=%s profile_id=%s profile_version=%s",
            APP_VERSION,
            profile["id"],
            profile["version"],
        )
        try:
            yield
        finally:
            LOGGER.info("event=application_stopped version=%s", APP_VERSION)

    application = FastAPI(
        title="CSV Import Doctor",
        version=APP_VERSION,
        description=(
            "Development version 0.4: all seven checks are available "
            "in the local checks module and CLI. HTTP inspection and "
            "normalization are not implemented yet."
        ),
        lifespan=lifespan,
        redoc_url=None,
    )

    @application.middleware("http")
    async def log_request(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        started = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = request_id
            response.headers["Cache-Control"] = "no-store"
            return response
        except Exception as exc:
            LOGGER.error(
                "event=unexpected_error request_id=%s exception_type=%s",
                request_id,
                type(exc).__name__,
            )
            raise
        finally:
            route = request.scope.get("route")
            path = getattr(route, "path", "<unmatched>")
            LOGGER.info(
                "event=request_completed request_id=%s method=%s path=%s "
                "status=%s duration_ms=%.2f",
                request_id,
                request.method,
                path,
                status_code,
                (time.perf_counter() - started) * 1000,
            )

    @application.get("/health", summary="Check that the process is running")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/version", summary="Application version, public settings and profile")
    def version(request: Request) -> dict[str, str]:
        current = request.app.state.settings
        profile = request.app.state.profile
        return {
            "name": current.app_name,
            "author": current.app_author,
            "version": APP_VERSION,
            "profile_id": profile["id"],
            "profile_version": profile["version"],
        }

    @application.get("/profile", summary="Profile and checks implemented in the core (not HTTP inspection)")
    def get_profile(request: Request) -> dict[str, Any]:
        return {
            "profile": request.app.state.profile,
            "implemented_check_ids": list(IMPLEMENTED_CHECK_IDS),
        }

    return application

app = create_app()
