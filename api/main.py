"""Optional local HTTP API for the canonical Omni-Grid triage pipeline."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
import time
import uuid
from collections import deque
from pathlib import Path, PurePath, PureWindowsPath
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from omnigrid.ingest import InputError, load_complaints
from omnigrid.pipeline import run_pipeline
from omnigrid.schemas import Report

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BYTES = MAX_UPLOAD_BYTES + 64 * 1024
MAX_FEATURES = 1000
MAX_PIPELINE_CONCURRENCY = 2
RATE_LIMIT_REQUESTS = 30
RATE_LIMIT_WINDOW_SECONDS = 60
TRIAGE_PATH = "/triage"
DEMO_PATH = "/demo"
REPOSITORY_ROOT = Path(__file__).resolve().parent.parent
REAL_DEMO_INPUT_PATH = REPOSITORY_ROOT / "data" / "real_sample_500.geojson"
SYNTHETIC_DEMO_INPUT_PATH = (
    REPOSITORY_ROOT / "data" / "synthetic" / "complaints.geojson"
)
DEMO_INPUT_PATH = REAL_DEMO_INPUT_PATH
WEB_DIRECTORY = REPOSITORY_ROOT / "web"
SUPPORTED_CONTENT_TYPES = {
    "application/geo+json",
    "application/json",
    "application/octet-stream",
}


class HealthResponse(BaseModel):
    """Process health only; optional model providers are not probed."""

    status: Literal["ok"]
    service: Literal["omnigrid-api"]


class RequestSizeLimitMiddleware:
    """Buffer and cap the small, bounded request body before multipart parsing."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("path", "").rstrip("/") != TRIAGE_PATH:
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                value
                for name, value in scope.get("headers", [])
                if name.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BYTES:
                    await self._too_large(scope, send)
                    return
            except ValueError:
                response = JSONResponse(
                    status_code=400,
                    content={"detail": "Invalid Content-Length header."},
                )
                await response(scope, receive, send)
                return

        body = bytearray()
        more_body = True
        while more_body:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            if message["type"] != "http.request":
                continue
            chunk = message.get("body", b"")
            if len(body) + len(chunk) > MAX_REQUEST_BYTES:
                await self._too_large(scope, send)
                return
            body.extend(chunk)
            more_body = message.get("more_body", False)

        body_bytes = bytes(body)
        replayed = False

        async def replay_receive() -> Message:
            nonlocal replayed
            if replayed:
                return {"type": "http.disconnect"}
            replayed = True
            return {"type": "http.request", "body": body_bytes, "more_body": False}

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _too_large(scope: Scope, send: Send) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": "Request body exceeds the maximum allowed size."},
        )

        async def empty_receive() -> Message:
            return {"type": "http.disconnect"}

        await response(scope, empty_receive, send)


app = FastAPI(
    title="Omni-Grid Triage API",
    description=(
        "Same-origin synthetic demo dashboard and HTTP access to the canonical "
        "Omni-Grid complaint triage pipeline."
    ),
    version="0.1.0",
)
app.add_middleware(RequestSizeLimitMiddleware)
_pipeline_semaphore = asyncio.Semaphore(MAX_PIPELINE_CONCURRENCY)
_rate_limit_buckets: dict[str, deque[float]] = {}


def _check_rate_limit(request: Request) -> None:
    now = time.monotonic()
    client_host = request.client.host if request.client else "unknown"
    bucket = _rate_limit_buckets.get(client_host)
    if bucket is None:
        if len(_rate_limit_buckets) >= 256:
            _rate_limit_buckets.pop(next(iter(_rate_limit_buckets)))
        bucket = deque()
        _rate_limit_buckets[client_host] = bucket
    while bucket and now - bucket[0] >= RATE_LIMIT_WINDOW_SECONDS:
        bucket.popleft()
    if len(bucket) >= RATE_LIMIT_REQUESTS:
        raise HTTPException(
            status_code=429,
            detail="Too many demo requests. Please wait before trying again.",
            headers={"Retry-After": str(RATE_LIMIT_WINDOW_SECONDS)},
        )
    bucket.append(now)


async def _run_bounded_pipeline(source: object) -> Report:
    if _pipeline_semaphore.locked():
        raise HTTPException(
            status_code=503,
            detail="The triage service is busy. Please try again shortly.",
            headers={"Retry-After": "5"},
        )
    await _pipeline_semaphore.acquire()
    worker = asyncio.create_task(run_in_threadpool(_run_pipeline, source))
    release_deferred = False
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        if worker.done():
            if not worker.cancelled():
                worker.exception()
        else:
            def release_after_worker(task: asyncio.Task[Report]) -> None:
                if not task.cancelled():
                    task.exception()
                _pipeline_semaphore.release()

            worker.add_done_callback(release_after_worker)
            release_deferred = True
        raise
    finally:
        if not release_deferred:
            _pipeline_semaphore.release()


@app.get(
    "/health",
    response_model=HealthResponse,
    summary="Check API process health",
    description=(
        "Reports only that the API process is responding. It does not check "
        "optional LLM or vision provider availability."
    ),
)
async def health() -> HealthResponse:
    """Return process health without probing downstream providers."""
    return HealthResponse(status="ok", service="omnigrid-api")


async def _execute_demo_pipeline(request: Request, input_path: Path) -> Report:
    _check_rate_limit(request)
    try:
        report = await _run_bounded_pipeline(input_path)
        report.stats["api_demo_media"] = True
        return report
    except HTTPException:
        raise
    except Exception as exc:
        request_id = uuid.uuid4().hex
        logger.error(
            "Demo request %s failed with %s.", request_id, type(exc).__name__
        )
        raise HTTPException(
            status_code=500,
            detail="The demo failed unexpectedly. Try again later.",
        ) from exc


@app.get(
    DEMO_PATH,
    response_model=Report,
    summary="Run demo pipeline",
    responses={
        429: {"description": "Demo request rate limit exceeded."},
        503: {"description": "Pipeline capacity is temporarily exhausted."},
    },
)
@app.post(
    DEMO_PATH,
    response_model=Report,
    summary="Run demo pipeline",
    responses={
        429: {"description": "Demo request rate limit exceeded."},
        503: {"description": "Pipeline capacity is temporarily exhausted."},
    },
)
async def run_demo(request: Request, dataset: str = "real") -> Report:
    """Run the canonical pipeline on the real or synthetic dataset."""
    target = (
        SYNTHETIC_DEMO_INPUT_PATH
        if dataset.strip().lower() == "synthetic"
        else REAL_DEMO_INPUT_PATH
    )
    return await _execute_demo_pipeline(request, target)


@app.get(
    "/demo/real",
    response_model=Report,
    summary="Run real Bengaluru demo pipeline",
    responses={
        429: {"description": "Demo request rate limit exceeded."},
        503: {"description": "Pipeline capacity is temporarily exhausted."},
    },
)
@app.post(
    "/demo/real",
    response_model=Report,
    summary="Run real Bengaluru demo pipeline",
    responses={
        429: {"description": "Demo request rate limit exceeded."},
        503: {"description": "Pipeline capacity is temporarily exhausted."},
    },
)
async def run_real_demo(request: Request) -> Report:
    """Run the canonical pipeline on the real Bengaluru sample fixture."""
    return await _execute_demo_pipeline(request, REAL_DEMO_INPUT_PATH)


@app.get(
    "/demo/synthetic",
    response_model=Report,
    summary="Run synthetic demo pipeline",
    responses={
        429: {"description": "Demo request rate limit exceeded."},
        503: {"description": "Pipeline capacity is temporarily exhausted."},
    },
)
@app.post(
    "/demo/synthetic",
    response_model=Report,
    summary="Run synthetic demo pipeline",
    responses={
        429: {"description": "Demo request rate limit exceeded."},
        503: {"description": "Pipeline capacity is temporarily exhausted."},
    },
)
async def run_synthetic_demo(request: Request) -> Report:
    """Run the canonical pipeline on the synthetic fixture."""
    return await _execute_demo_pipeline(request, SYNTHETIC_DEMO_INPUT_PATH)


@app.post(
    TRIAGE_PATH,
    response_model=Report,
    status_code=200,
    summary="Triage a GeoJSON complaint upload",
    description=(
        "Upload one GeoJSON Feature or non-empty FeatureCollection as the "
        "`file` multipart field. Maximum file size is 2 MiB."
    ),
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "required": ["file"],
                        "properties": {
                            "file": {"type": "string", "format": "binary"}
                        },
                    }
                }
            },
        }
    },
    responses={
        400: {"description": "Invalid multipart request or unsafe filename."},
        413: {"description": "Upload or request body exceeds the configured limit."},
        415: {"description": "Unsupported file extension or media type."},
        422: {"description": "Malformed GeoJSON or invalid complaint data."},
        500: {"description": "Unexpected server-side pipeline failure."},
    },
)
async def triage(request: Request) -> Report:
    """Validate an uploaded GeoJSON document and execute the shared pipeline."""
    _check_rate_limit(request)
    request_id = uuid.uuid4().hex
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip()
    if content_type.lower() != "multipart/form-data":
        raise HTTPException(
            status_code=415,
            detail="Use multipart/form-data with one file field named 'file'.",
        )

    try:
        form = await request.form(
            max_files=1,
            max_fields=0,
            max_part_size=MAX_UPLOAD_BYTES,
        )
    except StarletteHTTPException as exc:
        raise HTTPException(
            status_code=400,
            detail="Expected one multipart file field named 'file'.",
        ) from exc
    except Exception as exc:
        logger.error(
            "Triage request %s failed while parsing multipart data with %s.",
            request_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Triage failed unexpectedly. Try again later.",
        ) from exc

    try:
        items = form.multi_items()
        if (
            len(items) != 1
            or items[0][0] != "file"
            or not isinstance(items[0][1], UploadFile)
        ):
            raise HTTPException(
                status_code=422,
                detail="Provide exactly one uploaded file in the 'file' field.",
            )

        upload = items[0][1]
        raw = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(raw) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds the {MAX_UPLOAD_BYTES}-byte limit.",
            )

        try:
            geojson = await run_in_threadpool(
                _parse_and_validate_upload,
                raw,
                upload.filename or "",
                upload.content_type or "",
            )
        except HTTPException:
            raise
        except InputError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:
            logger.error(
                "Triage request %s failed while validating input with %s.",
                request_id,
                type(exc).__name__,
            )
            raise HTTPException(
                status_code=500,
                detail="Triage failed unexpectedly. Try again later.",
            ) from exc
    finally:
        await form.close()

    try:
        return await _run_bounded_pipeline(geojson)
    except HTTPException:
        raise
    except InputError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.error(
            "Triage request %s failed with %s.",
            request_id,
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=500,
            detail="Triage failed unexpectedly. Try again later.",
        ) from exc


def _parse_and_validate_upload(
    raw: bytes,
    filename: str,
    content_type: str,
) -> object:
    if not filename or _has_path_components(filename):
        raise HTTPException(
            status_code=400,
            detail="The uploaded filename must not contain path components.",
        )
    if PurePath(filename).suffix.lower() not in {".geojson", ".json"}:
        raise HTTPException(
            status_code=415,
            detail="Upload a .geojson or .json file containing GeoJSON.",
        )
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type not in SUPPORTED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail="The uploaded file must use a supported JSON media type.",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise InputError("File must contain valid UTF-8 GeoJSON.") from exc

    if not isinstance(data, dict) or data.get("type") not in {
        "Feature",
        "FeatureCollection",
    }:
        raise InputError("Input must be a GeoJSON Feature or FeatureCollection.")
    if data.get("type") == "FeatureCollection":
        features = data.get("features")
        if not isinstance(features, list):
            raise InputError("GeoJSON FeatureCollection.features must be an array.")
        if not features:
            raise InputError("GeoJSON must contain at least one complaint feature.")
        if len(features) > MAX_FEATURES:
            raise HTTPException(
                status_code=413,
                detail=f"GeoJSON cannot contain more than {MAX_FEATURES} features.",
            )

    complaints = load_complaints(data)
    if not complaints:
        raise InputError("GeoJSON must contain at least one complaint feature.")
    for complaint in complaints:
        media_items = list(complaint.media)
        if complaint.closure is not None:
            media_items.extend(complaint.closure.media)
        for media in media_items:
            if _has_unsafe_media_path(media.path):
                raise InputError(
                    "Media paths must be relative and cannot contain parent "
                    "directory components."
                )
    return data


def _has_path_components(value: str) -> bool:
    windows_path = PureWindowsPath(value)
    native_path = PurePath(value)
    return (
        "/" in value
        or "\\" in value
        or native_path.is_absolute()
        or windows_path.is_absolute()
        or windows_path.drive != ""
        or ".." in native_path.parts
        or ".." in windows_path.parts
    )


def _has_unsafe_media_path(value: str) -> bool:
    native_path = PurePath(value)
    windows_path = PureWindowsPath(value)
    return (
        native_path.is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or ".." in native_path.parts
        or ".." in windows_path.parts
    )


def _run_pipeline(geojson: object, *, mock: bool = True) -> Report:
    if isinstance(geojson, Path):
        return run_pipeline(geojson, mock=mock)
    with tempfile.TemporaryDirectory(prefix="omnigrid-api-media-") as media_root:
        return run_pipeline(geojson, media_root=media_root, mock=mock)


app.mount("/", StaticFiles(directory=WEB_DIRECTORY, html=True), name="dashboard")