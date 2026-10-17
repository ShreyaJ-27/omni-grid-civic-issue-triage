"""Shared Pydantic contracts for Omni-Grid pipeline data."""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, model_validator

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

    @property
    def is_within_geofence(self) -> bool:
        """Check if distance is within the runtime geofence threshold."""
        if self.distance_m is None:
            return False
        from .config import get_settings

        return self.distance_m <= get_settings().GEOFENCE_METERS


class VideoKeyframe(BaseModel):
    complaint_id: str
    video_path: str
    frame_index: int
    timestamp_sec: float
    keyframe_path: str
    evidence_meta: EvidenceMeta

    @property
    def source_video_path(self) -> str:
        return self.video_path

    @property
    def phash(self) -> str:
        return self.evidence_meta.phash


VideoEvidenceMeta = VideoKeyframe


class Hotspot(BaseModel):


    id: str
    centroid: tuple[Latitude, Longitude]
    radius_m: float
    complaint_ids: list[str]
    category_mix: dict[str, int] = Field(default_factory=dict)
    severity: Annotated[float, Field(ge=0, le=10)]

    @model_validator(mode="before")
    @classmethod
    def _remap_category_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            if "category_counts" in data and "category_mix" not in data:
                data["category_mix"] = data["category_counts"]
            elif "category_mix" in data and "category_counts" not in data:
                data["category_counts"] = data["category_mix"]
        return data

    @property
    def complaint_count(self) -> int:
        return len(self.complaint_ids)

    @property
    def category_counts(self) -> dict[str, int]:
        return self.category_mix



class Check(BaseModel):
    name: str
    passed: bool | None = None
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
    complaints: list[Complaint] = Field(default_factory=list)
