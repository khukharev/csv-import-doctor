import logging
import time
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request

from config import APP_VERSION, Settings, load_settings

LOGGER = logging.getLogger("csv_import_doctor")


class UTCFormatter(logging.Formatter):
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

def create_app(settings: Settings | None = None) -> FastAPI:

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        actual_settings = settings if settings is not None else load_settings()
        application.state.settings = actual_settings
        application.title = actual_settings.app_name
        application.openapi_schema = None
        configure_logging(actual_settings.log_level)
        LOGGER.info("event=application_started version=%s", APP_VERSION)
        try:
            yield
        finally:
            LOGGER.info("event=application_stopped version=%s", APP_VERSION)

    application = FastAPI(
        title="CSV Import Doctor",
        version=APP_VERSION,
        description=(
            "Version 0.1: Project infrastructure. "
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

    @application.get("/health", summary="Process is healthy")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @application.get("/version", summary="App version and public settings")
    def version(request: Request) -> dict[str, str]:
        current = request.app.state.settings
        return {
            "name": current.app_name,
            "author": current.app_author,
            "version": APP_VERSION,
        }

    return application

app = create_app()
