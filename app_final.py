import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import uuid4

from fastapi import FastAPI, File, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from checks import inspect_csv
from csv_core import CSVOptions, DoctorError

from config import (
    APP_VERSION, MAX_CHANGES, MAX_COLUMNS, MAX_FIELD_LENGTH,
    MAX_HEADER_LENGTH, MAX_ISSUE_DETAILS, Settings, load_settings,
)
from csv_profile import IMPLEMENTED_CHECK_IDS, ProfileError, load_profile

LOGGER = logging.getLogger("csv_import_doctor")
LIMIT_ERROR_CODES = frozenset({
    "FILE_TOO_LARGE", "ROW_LIMIT_EXCEEDED", "COLUMN_LIMIT_EXCEEDED",
    "FIELD_LIMIT_EXCEEDED", "CHANGE_LIMIT_EXCEEDED",
})


def error_response(
    request: Request, status: int, code: str, message: str,
    *, details: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Return safe metadata, never raw validation inputs or exception text."""
    request_id = request.state.request_id
    LOGGER.warning(
        "event=request_rejected request_id=%s code=%s status=%s",
        request_id, code, status,
    )
    return JSONResponse(
        status_code=status,
        content={"error": {
            "code": code, "message": message, "request_id": request_id,
            "report": None, "details": {} if details is None else details,
        }},
        headers=headers,
    )


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
            "Development version 0.5: inspect CSV files through POST /inspect "
            "or the local CLI. Inspection is read-only. "
            "Normalization and corrected downloads are not implemented yet."
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
        except Exception as exc:
            LOGGER.error(
                "event=unexpected_error request_id=%s exception_type=%s",
                request_id,
                type(exc).__name__,
            )
            response = error_response(
                request, 500, "INTERNAL_ERROR", "An unexpected server error occurred."
            )
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

        response.headers["X-Request-ID"] = request_id
        response.headers["Cache-Control"] = "no-store"
        return response

    @application.exception_handler(DoctorError)
    async def doctor_error(request: Request, exc: DoctorError) -> JSONResponse:
        status = 413 if exc.code in LIMIT_ERROR_CODES else 422
        code = "INVALID_REQUEST" if exc.code == "INVALID_OPTIONS" else exc.code
        return error_response(request, status, code, exc.message, details=exc.details)

    @application.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Do not serialize exc.errors(): it includes untrusted inputs and filenames.
        return error_response(
            request, 422, "INVALID_REQUEST",
            "Provide one CSV file in field 'file' and supported query parameters.",
        )

    @application.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        known = {
            400: ("INVALID_REQUEST", "The request body could not be parsed."),
            404: ("NOT_FOUND", "The requested route does not exist."),
            405: ("METHOD_NOT_ALLOWED", "The method is not allowed for this route."),
            413: ("REQUEST_TOO_LARGE", "The request exceeds a framework limit."),
        }
        code, message = known.get(exc.status_code, ("HTTP_ERROR", "The request was rejected."))
        return error_response(request, exc.status_code, code, message, headers=exc.headers)

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

    @application.get("/profile", summary="Profile, effective settings and limits")
    def get_profile(request: Request) -> dict[str, Any]:
        return {
            "profile": request.app.state.profile,
            "implemented_check_ids": list(IMPLEMENTED_CHECK_IDS),
            "effective_settings": {
                "trim_nbsp": request.app.state.settings.csv_trim_nbsp,
                "normalization_implemented": False,
            },
            "limits": {
                "max_bytes": request.app.state.settings.csv_max_bytes,
                "max_rows": request.app.state.settings.csv_max_rows,
                "max_columns": MAX_COLUMNS,
                "max_field_length": MAX_FIELD_LENGTH,
                "max_header_length": MAX_HEADER_LENGTH,
                "max_issue_details": MAX_ISSUE_DETAILS,
                "max_changes": MAX_CHANGES,
            },
        }

    @application.post(
        "/inspect", summary="Inspect a CSV without changing its contents",
        description=(
            "Upload a CSV in multipart field 'file'. Parameters belong in the query string. "
            "HTTP 200 means inspection completed; check 'valid' for data errors. "
            "Warnings do not make a report invalid. Defaults come from GET /profile. "
            "The source is never normalized or retained by the application."
        ),
        responses={
            400: {"description": "Malformed multipart request"},
            413: {"description": "File, record, column or field limit exceeded"},
            422: {"description": "Invalid request or unreadable CSV"},
            500: {"description": "Unexpected internal error; no private details returned"},
        },
    )
    def inspect_upload(
        request: Request,
        file: Annotated[UploadFile, File(description="CSV bytes; filename and MIME type are not trusted.")],
        encoding: Annotated[Literal["utf-8", "cp1251"] | None, Query(
            description="Omit to use the profile default; never guessed.",
        )] = None,
        delimiter: Annotated[Literal["comma", "semicolon", "tab"] | None, Query(
            description="Omit to use the profile default; never guessed.",
        )] = None,
        id_column: Annotated[str | None, Query(
            min_length=1, max_length=MAX_HEADER_LENGTH,
            description="Exact header after configured trim; omit to skip ID checks.",
        )] = None,
    ) -> dict[str, Any]:
        current = request.app.state.settings
        profile = request.app.state.profile
        try:
            # The multipart parser may already have spooled a temporary file.
            # This cap bounds the bytes passed to our core, not the HTTP body.
            raw = file.file.read(current.csv_max_bytes + 1)
            report = inspect_csv(
                raw,
                options=CSVOptions(
                    encoding=encoding if encoding is not None else profile["defaults"]["encoding"],
                    delimiter=delimiter if delimiter is not None else profile["defaults"]["delimiter"],
                ),
                id_column=id_column, settings=current, profile=profile,
            )
        finally:
            file.file.close()
        # Use one identity for response headers, logs and the core report.
        report["request_id"] = request.state.request_id
        LOGGER.info(
            "event=inspection_completed request_id=%s rows=%s columns=%s "
            "errors=%s warnings=%s valid=%s",
            request.state.request_id, report["table"]["row_count"],
            report["table"]["column_count"], report["summary"]["error_count"],
            report["summary"]["warning_count"], report["valid"],
        )
        return report

    return application

app = create_app()
