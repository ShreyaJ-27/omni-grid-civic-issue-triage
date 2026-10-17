"""Optional local HTTP API for the canonical Omni-Grid triage pipeline."""

from __future__ import annotations

import json
import logging
import tempfile
import uuid
from pathlib import PurePath, PureWindowsPath
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
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
TRIAGE_PATH = "/triage"
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
        "Local, optional HTTP access to the canonical Omni-Grid complaint "
        "triage pipeline. Model providers are configured server-side."
    ),
    version="0.1.0",
)
app.add_middleware(RequestSizeLimitMiddleware)


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
        return await run_in_threadpool(_run_pipeline, geojson)
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


def _run_pipeline(geojson: object) -> Report:
    with tempfile.TemporaryDirectory(prefix="omnigrid-api-media-") as media_root:
        return run_pipeline(geojson, media_root=media_root)