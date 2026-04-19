"""
Pydantic schemas for SmartFlow AI request and response objects.

All models use strict validation, typed fields, and enums to ensure
clean serialization and reliable API contracts.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Annotated, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_serializer, field_validator


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class EventPhase(str, Enum):
    """Represents the current phase of the sporting event."""

    PRE_MATCH = "pre_match"
    LIVE_PLAY = "live_play"
    HALFTIME = "halftime"
    POST_MATCH = "post_match"


class WeatherCondition(str, Enum):
    """Ambient weather conditions that may affect crowd movement."""

    CLEAR = "clear"
    RAINY = "rainy"
    HOT = "hot"
    WINDY = "windy"


class RiskLevel(str, Enum):
    """Crowd congestion risk classification."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class RouteStatus(str, Enum):
    """Advisability classification for a recommended route."""

    RECOMMENDED = "recommended"
    NO_BETTER_OPTION = "no_better_option"
    CLEAR = "clear"


# ---------------------------------------------------------------------------
# Reusable annotated types
# ---------------------------------------------------------------------------

# Zone / gate name: non-empty, trimmed, max 64 chars.
ZoneName = Annotated[str, Field(min_length=1, max_length=64)]

# Crowd density percentage: 0.0–100.0 inclusive.
DensityPercent = Annotated[float, Field(ge=0.0, le=100.0)]

# Queue wait time: 0–480 minutes (8 hours — realistic upper bound for any event).
QueueMinutes = Annotated[int, Field(ge=0, le=480)]

# Confidence / severity score: 0.0–100.0 inclusive.
ScoreField = Annotated[float, Field(ge=0.0, le=100.0)]


# ---------------------------------------------------------------------------
# Utility response models (GET endpoints)
# ---------------------------------------------------------------------------


class ServiceInfoResponse(BaseModel):
    """Response schema for the GET / service overview endpoint."""

    service: str = Field(..., description="Application name.")
    version: str = Field(..., description="Semantic version (MAJOR.MINOR.PATCH).")
    summary: str = Field(..., description="One-sentence description of the service.")
    docs: str = Field(..., description="Path to the interactive API documentation.")


class HealthResponse(BaseModel):
    """Response schema for the GET /health liveness endpoint."""

    status: str = Field(..., description="Liveness status. Always 'ok' when the service is up.")
    service: str = Field(..., description="Application name.")
    timestamp: str = Field(..., description="UTC ISO 8601 timestamp of the health check.")


# ---------------------------------------------------------------------------
# Request Models
# ---------------------------------------------------------------------------


class CrowdAnalysisRequest(BaseModel):
    """Input payload for crowd condition analysis at a specific zone or gate."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "zone": "Gate A",
                "crowd_density": 78.0,
                "queue_time_minutes": 20,
                "event_phase": "halftime",
                "weather": "clear",
                "special_event": False,
                "nearby_alternatives": ["Gate B", "Gate C"],
            }
        },
    )

    zone: ZoneName = Field(
        ...,
        description="Name of the zone, gate, or section being analyzed.",
        examples=["Gate A", "North Stand", "Section 12"],
    )
    crowd_density: DensityPercent = Field(
        ...,
        description="Current crowd density as a percentage (0 = empty, 100 = fully packed).",
        examples=[72.5],
    )
    queue_time_minutes: QueueMinutes = Field(
        ...,
        description="Estimated queue wait time in minutes at the current zone (0–480).",
        examples=[15],
    )
    event_phase: EventPhase = Field(
        ...,
        description="Current phase of the sporting event.",
        examples=[EventPhase.HALFTIME],
    )
    weather: WeatherCondition = Field(
        default=WeatherCondition.CLEAR,
        description="Current weather condition at the venue.",
    )
    special_event: bool = Field(
        default=False,
        description=(
            "True if a crowd-amplifying moment is occurring "
            "(e.g., goal scored, penalty shootout, fireworks)."
        ),
    )
    nearby_alternatives: Optional[list[ZoneName]] = Field(
        default=None,
        min_length=1,
        max_length=10,
        description="Optional list of nearby alternate gate or zone names (1–10 entries).",
        examples=[["Gate B", "Gate C"]],
    )

    @field_validator("zone")
    @classmethod
    def zone_must_not_be_blank(cls, value: str) -> str:
        """Reject zone names that are blank after whitespace stripping."""
        if not value.strip():
            raise ValueError("zone must not be blank or whitespace-only")
        return value.strip()

    @field_validator("nearby_alternatives")
    @classmethod
    def alternatives_must_not_contain_blanks(
        cls, value: Optional[list[str]]
    ) -> Optional[list[str]]:
        """Reject lists that contain blank or whitespace-only gate names."""
        if value is None:
            return value
        cleaned = [v.strip() for v in value]
        if any(not v for v in cleaned):
            raise ValueError("nearby_alternatives must not contain blank entries")
        return cleaned


class RouteRecommendationRequest(BaseModel):
    """Input payload for alternate route or gate recommendation."""

    model_config = ConfigDict(
        str_strip_whitespace=True,
        json_schema_extra={
            "example": {
                "current_gate": "Gate A",
                "crowd_density": 85.0,
                "queue_time_minutes": 25,
                "event_phase": "post_match",
                "nearby_alternatives": ["Gate B", "Gate C"],
                "destination_zone": "North Stand",
            }
        },
    )

    current_gate: ZoneName = Field(
        ...,
        description="The gate or zone the attendee is currently at.",
        examples=["Gate A"],
    )
    crowd_density: DensityPercent = Field(
        ...,
        description="Current crowd density at the attendee's location (0–100).",
        examples=[85.0],
    )
    queue_time_minutes: QueueMinutes = Field(
        ...,
        description="Current queue wait time in minutes at the attendee's gate (0–480).",
        examples=[25],
    )
    event_phase: EventPhase = Field(
        ...,
        description="Current phase of the sporting event.",
    )
    nearby_alternatives: Optional[list[ZoneName]] = Field(
        default=None,
        min_length=1,
        max_length=10,
        description="Nearby alternate gates or zones available to the attendee (1–10 entries).",
        examples=[["Gate B", "Gate C", "Gate D"]],
    )
    destination_zone: Optional[ZoneName] = Field(
        default=None,
        description="Optional target zone or section the attendee is trying to reach.",
        examples=["North Stand"],
    )

    @field_validator("current_gate", "destination_zone")
    @classmethod
    def gate_names_must_not_be_blank(cls, value: Optional[str]) -> Optional[str]:
        """Reject gate or zone names that are blank after whitespace stripping."""
        if value is not None and not value.strip():
            raise ValueError("Gate and zone names must not be blank or whitespace-only")
        return value.strip() if value else value

    @field_validator("nearby_alternatives")
    @classmethod
    def alternatives_must_not_contain_blanks(
        cls, value: Optional[list[str]]
    ) -> Optional[list[str]]:
        """Reject lists that contain blank or whitespace-only gate names."""
        if value is None:
            return value
        cleaned = [v.strip() for v in value]
        if any(not v for v in cleaned):
            raise ValueError("nearby_alternatives must not contain blank entries")
        return cleaned

    @field_validator("nearby_alternatives")
    @classmethod
    def alternatives_must_not_include_current_gate(
        cls, value: Optional[list[str]], info: ValidationInfo
    ) -> Optional[list[str]]:
        """Reject alternatives lists that contain the current gate.

        Recommending the gate the attendee is already at is nonsensical and
        would produce a misleading response (e.g. 'Use Gate A instead of Gate A').
        """
        if value is None:
            return value
        current = info.data.get("current_gate", "")
        if current and any(v.strip().lower() == current.strip().lower() for v in value):
            raise ValueError(
                "nearby_alternatives must not contain current_gate — "
                "an alternate route must differ from the current location"
            )
        return value


# ---------------------------------------------------------------------------
# Response Models
# ---------------------------------------------------------------------------


class CrowdAnalysisResponse(BaseModel):
    """Result of crowd condition analysis for a given zone."""

    model_config = ConfigDict(str_strip_whitespace=True)

    zone: str = Field(..., description="Zone or gate that was analyzed.")
    risk_level: RiskLevel = Field(..., description="Assessed crowd congestion risk level.")
    predicted_congestion: bool = Field(
        ...,
        description="Whether congestion is predicted to worsen in the near term.",
    )
    recommendation: str = Field(
        ...,
        description="Human-readable action recommendation for venue staff or attendees.",
    )
    estimated_wait_reduction_minutes: int = Field(
        ...,
        ge=0,
        description="Estimated wait time reduction (minutes) if alternate movement is taken.",
    )
    severity_score: ScoreField = Field(
        ...,
        description=(
            "Weighted severity score (0–100) derived from density, queue time, "
            "event phase, weather, and special event status."
        ),
    )
    confidence_score: ScoreField = Field(
        ...,
        description=(
            "Confidence in the risk assessment (0–100). "
            "Higher when multiple independent factors align with the assigned risk level."
        ),
    )
    contributing_factors: list[str] = Field(
        ...,
        description="Ordered list of human-readable factors that elevated the risk score.",
    )
    ai_insight: str | None = Field(
        default=None,
        description=(
            "AI-generated operational insight powered by Google Gemini. "
            "Provides natural language explanation of the situation and actionable guidance. "
            "Falls back to deterministic explanation if Gemini is unavailable."
        ),
    )
    ai_powered: bool = Field(
        default=False,
        description=(
            "Indicates whether the ai_insight was generated by Google Gemini AI (true) "
            "or deterministic fallback (false). This field helps verify active AI integration."
        ),
    )
    analyzed_at: datetime = Field(
        ...,
        description="UTC datetime of when the analysis was performed.",
    )

    @field_serializer("analyzed_at")
    def serialize_analyzed_at(self, value: datetime) -> str:
        """Serialize analyzed_at as a UTC ISO 8601 string."""
        return value.astimezone(timezone.utc).isoformat()


class RouteRecommendationResponse(BaseModel):
    """Result of alternate route or gate recommendation."""

    model_config = ConfigDict(str_strip_whitespace=True)

    current_gate: str = Field(..., description="The gate or zone the attendee is currently at.")
    alternate_gate: Optional[str] = Field(
        default=None,
        description="Recommended alternate gate or zone. None if no better option exists.",
    )
    alternate_zone: Optional[str] = Field(
        default=None,
        description="Broader zone associated with the recommended alternate gate, if applicable.",
    )
    route_status: RouteStatus = Field(
        ...,
        description="Advisability classification for the recommended route.",
    )
    recommendation: str = Field(
        ...,
        description="Human-readable routing advice for the attendee.",
    )
    estimated_wait_reduction_minutes: int = Field(
        ...,
        ge=0,
        description="Estimated wait time reduction (minutes) by taking the alternate route.",
    )
    confidence_score: ScoreField = Field(
        ...,
        description=(
            "Confidence in the route recommendation (0–100). "
            "Reflects how strongly conditions justify the suggested reroute."
        ),
    )
    reason: str = Field(
        ...,
        description="Explanation of why this route was recommended or why no change was advised.",
    )
    ai_insight: str | None = Field(
        default=None,
        description=(
            "AI-generated routing insight powered by Google Gemini. "
            "Provides natural language explanation and tactical guidance. "
            "Falls back to deterministic explanation if Gemini is unavailable."
        ),
    )
    ai_powered: bool = Field(
        default=False,
        description=(
            "Indicates whether the ai_insight was generated by Google Gemini AI (true) "
            "or deterministic fallback (false). This field helps verify active AI integration."
        ),
    )
    analyzed_at: datetime = Field(
        ...,
        description="UTC datetime of when the recommendation was generated.",
    )

    @field_serializer("analyzed_at")
    def serialize_analyzed_at(self, value: datetime) -> str:
        """Serialize analyzed_at as a UTC ISO 8601 string."""
        return value.astimezone(timezone.utc).isoformat()
