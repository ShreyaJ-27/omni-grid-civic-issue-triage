"""Shared Pydantic contracts for Omni-Grid pipeline data."""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

Latitude = Annotated[float, Field(ge=-90, le=90)]
Longitude = Annotated[float, Field(ge=-180, le=180)]


class Media(BaseModel):
    path: str
    type: Literal["image", "video"]


class Closure(BaseModel):
    closed_at: datetime
    media: list[Media]
    closed_by: str | None


class Complaint(BaseModel):
    id: str
    category: Literal["pothole", "waterlogging", "streetlight", "other"]
    text: str
    lat: Latitude
    lon: Longitude
    created_at: datetime
    status: Literal["open", "closed"]
    media: list[Media] = Field(default_factory=list)
    closure: Closure | None = None


class EvidenceMeta(BaseModel):
    complaint_id: str
    path: str
    gps: tuple[Latitude, Longitude] | None
    taken_at: datetime | None
    phash: str
    distance_m: float | None


class Hotspot(BaseModel):
    id: str
    centroid: tuple[Latitude, Longitude]
    radius_m: float
    complaint_ids: list[str]
    category_mix: dict[str, int]
    severity: Annotated[float, Field(ge=0, le=10)]


class Check(BaseModel):
    name: str
    passed: bool
    detail: str
    value: float | None = None


class Verdict(BaseModel):
    complaint_id: str
    verdict: Literal["verified", "suspect", "inconclusive"]
    checks: list[Check]
    confidence: Annotated[float, Field(ge=0, le=1)]


class Claim(BaseModel):
    text: str
    cites: list[str]


class Report(BaseModel):
    hotspots: list[Hotspot]
    verdicts: list[Verdict]
    claims: list[Claim]
    stats: dict
