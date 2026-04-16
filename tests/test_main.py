"""
Integration tests for SmartFlow AI API endpoints.

Uses FastAPI's TestClient (backed by httpx) to exercise the full
request/response cycle — including Pydantic validation — without
starting a live server.  All tests are deterministic and require no
external services.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.models import RiskLevel, RouteStatus

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

client = TestClient(app, raise_server_exceptions=True)

# ---------------------------------------------------------------------------
# Realistic payloads used across multiple tests
# ---------------------------------------------------------------------------

_HIGH_CONGESTION_CROWD_PAYLOAD: dict = {
    "zone": "Gate A",
    "crowd_density": 82.0,
    "queue_time_minutes": 25,
    "event_phase": "halftime",
    "weather": "rainy",
    "special_event": False,
    "nearby_alternatives": ["Gate B", "Gate C"],
}

_LOW_CONGESTION_CROWD_PAYLOAD: dict = {
    "zone": "Gate D",
    "crowd_density": 20.0,
    "queue_time_minutes": 3,
    "event_phase": "live_play",
    "weather": "clear",
    "special_event": False,
}

_CONGESTED_ROUTE_PAYLOAD: dict = {
    "current_gate": "Gate A",
    "crowd_density": 85.0,
    "queue_time_minutes": 25,
    "event_phase": "post_match",
    "nearby_alternatives": ["Gate B", "Gate C"],
    "destination_zone": "North Stand",
}

_CLEAR_ROUTE_PAYLOAD: dict = {
    "current_gate": "Gate D",
    "crowd_density": 15.0,
    "queue_time_minutes": 5,
    "event_phase": "live_play",
}

_NO_ALTERNATIVES_ROUTE_PAYLOAD: dict = {
    "current_gate": "Gate A",
    "crowd_density": 90.0,
    "queue_time_minutes": 30,
    "event_phase": "post_match",
}


# ---------------------------------------------------------------------------
# GET /
# ---------------------------------------------------------------------------


class TestRootEndpoint:
    """Tests for the service overview endpoint."""

    def test_returns_200(self) -> None:
        response = client.get("/")
        assert response.status_code == 200

    def test_response_contains_service_identity(self) -> None:
        body = client.get("/").json()
        assert "service" in body
        assert "version" in body
        assert "summary" in body
        assert "docs" in body

    def test_service_name_is_non_empty_string(self) -> None:
        body = client.get("/").json()
        assert isinstance(body["service"], str)
        assert len(body["service"]) > 0

    def test_version_follows_semver_pattern(self) -> None:
        body = client.get("/").json()
        parts = body["version"].split(".")
        assert len(parts) == 3
        assert all(part.isdigit() for part in parts)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for the liveness health check endpoint."""

    def test_returns_200(self) -> None:
        response = client.get("/health")
        assert response.status_code == 200

    def test_status_is_ok(self) -> None:
        body = client.get("/health").json()
        assert body["status"] == "ok"

    def test_response_includes_timestamp(self) -> None:
        body = client.get("/health").json()
        assert "timestamp" in body
        assert isinstance(body["timestamp"], str)
        assert len(body["timestamp"]) > 0

    def test_response_includes_service_name(self) -> None:
        body = client.get("/health").json()
        assert "service" in body
        assert isinstance(body["service"], str)

    def test_timestamp_is_iso8601(self) -> None:
        """Timestamp must be parseable as an ISO 8601 datetime string."""
        from datetime import datetime
        body = client.get("/health").json()
        # Raises ValueError if the format is invalid.
        datetime.fromisoformat(body["timestamp"])


# ---------------------------------------------------------------------------
# POST /api/v1/analyze-crowd — valid requests
# ---------------------------------------------------------------------------


class TestAnalyzeCrowdValid:
    """Tests for successful crowd analysis requests."""

    def test_returns_200_for_high_congestion(self) -> None:
        response = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD)
        assert response.status_code == 200

    def test_response_contains_all_required_fields(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        required = {
            "zone",
            "risk_level",
            "predicted_congestion",
            "recommendation",
            "estimated_wait_reduction_minutes",
            "severity_score",
            "confidence_score",
            "contributing_factors",
            "analyzed_at",
        }
        assert required.issubset(body.keys())

    def test_zone_echoed_correctly(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert body["zone"] == _HIGH_CONGESTION_CROWD_PAYLOAD["zone"]

    def test_risk_level_is_valid_enum_value(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        valid_levels = {level.value for level in RiskLevel}
        assert body["risk_level"] in valid_levels

    def test_high_congestion_produces_high_or_medium_risk(self) -> None:
        """Density 82%, halftime, rainy must not produce a low risk result."""
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert body["risk_level"] in {RiskLevel.HIGH.value, RiskLevel.MEDIUM.value}

    def test_severity_score_within_bounds(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert 0.0 <= body["severity_score"] <= 100.0

    def test_confidence_score_within_bounds(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert 0.0 <= body["confidence_score"] <= 100.0

    def test_contributing_factors_is_non_empty_list(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert isinstance(body["contributing_factors"], list)
        assert len(body["contributing_factors"]) > 0

    def test_estimated_wait_reduction_is_non_negative(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert body["estimated_wait_reduction_minutes"] >= 0

    def test_analyzed_at_is_iso8601(self) -> None:
        from datetime import datetime
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        datetime.fromisoformat(body["analyzed_at"])

    def test_low_congestion_produces_low_risk(self) -> None:
        """Density 20%, live play, clear weather must produce a low risk result."""
        body = client.post("/api/v1/analyze-crowd", json=_LOW_CONGESTION_CROWD_PAYLOAD).json()
        assert body["risk_level"] == RiskLevel.LOW.value

    def test_low_risk_wait_reduction_is_zero(self) -> None:
        """Low-risk zones offer no benefit from rerouting."""
        body = client.post("/api/v1/analyze-crowd", json=_LOW_CONGESTION_CROWD_PAYLOAD).json()
        assert body["estimated_wait_reduction_minutes"] == 0

    def test_recommendation_is_non_empty_string(self) -> None:
        body = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        assert isinstance(body["recommendation"], str)
        assert len(body["recommendation"]) > 0

    def test_special_event_flag_increases_severity(self) -> None:
        """Adding special_event=True to an already elevated payload must not lower severity."""
        base = client.post("/api/v1/analyze-crowd", json=_HIGH_CONGESTION_CROWD_PAYLOAD).json()
        with_special = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "special_event": True}
        elevated = client.post("/api/v1/analyze-crowd", json=with_special).json()
        assert elevated["severity_score"] >= base["severity_score"]

    def test_post_match_phase_produces_higher_severity_than_live_play(self) -> None:
        """Post-match phase carries a higher bump than live play at equal density."""
        live_play = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "event_phase": "live_play", "weather": "clear"}
        post_match = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "event_phase": "post_match", "weather": "clear"}
        score_live = client.post("/api/v1/analyze-crowd", json=live_play).json()["severity_score"]
        score_post = client.post("/api/v1/analyze-crowd", json=post_match).json()["severity_score"]
        assert score_post > score_live


# ---------------------------------------------------------------------------
# POST /api/v1/analyze-crowd — invalid requests
# ---------------------------------------------------------------------------


class TestAnalyzeCrowdInvalid:
    """Tests for Pydantic validation rejection on malformed crowd analysis requests."""

    def test_density_above_100_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "crowd_density": 101.0}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_density_below_0_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "crowd_density": -1.0}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_negative_queue_time_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "queue_time_minutes": -5}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_queue_time_above_480_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "queue_time_minutes": 481}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_invalid_event_phase_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "event_phase": "overtime"}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_invalid_weather_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "weather": "snowy"}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_blank_zone_returns_422(self) -> None:
        payload = {**_HIGH_CONGESTION_CROWD_PAYLOAD, "zone": "   "}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_missing_required_field_returns_422(self) -> None:
        payload = {k: v for k, v in _HIGH_CONGESTION_CROWD_PAYLOAD.items() if k != "zone"}
        assert client.post("/api/v1/analyze-crowd", json=payload).status_code == 422

    def test_empty_body_returns_422(self) -> None:
        assert client.post("/api/v1/analyze-crowd", json={}).status_code == 422


# ---------------------------------------------------------------------------
# POST /api/v1/recommend-route — valid requests
# ---------------------------------------------------------------------------


class TestRecommendRouteValid:
    """Tests for successful route recommendation requests."""

    def test_returns_200_for_congested_gate(self) -> None:
        response = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD)
        assert response.status_code == 200

    def test_response_contains_all_required_fields(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        required = {
            "current_gate",
            "alternate_gate",
            "alternate_zone",
            "route_status",
            "recommendation",
            "estimated_wait_reduction_minutes",
            "confidence_score",
            "reason",
            "analyzed_at",
        }
        assert required.issubset(body.keys())

    def test_congested_gate_with_alternatives_returns_recommended_status(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        assert body["route_status"] == RouteStatus.RECOMMENDED.value

    def test_alternate_gate_is_from_provided_alternatives(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        assert body["alternate_gate"] in _CONGESTED_ROUTE_PAYLOAD["nearby_alternatives"]

    def test_wait_reduction_is_positive_when_recommended(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        assert body["estimated_wait_reduction_minutes"] > 0

    def test_confidence_score_within_bounds(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        assert 0.0 <= body["confidence_score"] <= 100.0

    def test_reason_is_non_empty_string(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        assert isinstance(body["reason"], str)
        assert len(body["reason"]) > 0

    def test_analyzed_at_is_iso8601(self) -> None:
        from datetime import datetime
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        datetime.fromisoformat(body["analyzed_at"])

    def test_route_status_is_valid_enum_value(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        valid_statuses = {s.value for s in RouteStatus}
        assert body["route_status"] in valid_statuses

    def test_current_gate_echoed_correctly(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CONGESTED_ROUTE_PAYLOAD).json()
        assert body["current_gate"] == _CONGESTED_ROUTE_PAYLOAD["current_gate"]


# ---------------------------------------------------------------------------
# POST /api/v1/recommend-route — clear / fallback scenarios
# ---------------------------------------------------------------------------


class TestRecommendRouteFallback:
    """Tests for non-congested and no-alternatives route recommendation scenarios."""

    def test_clear_conditions_return_clear_status(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CLEAR_ROUTE_PAYLOAD).json()
        assert body["route_status"] == RouteStatus.CLEAR.value

    def test_clear_conditions_return_no_alternate_gate(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CLEAR_ROUTE_PAYLOAD).json()
        assert body["alternate_gate"] is None

    def test_clear_conditions_wait_reduction_is_zero(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_CLEAR_ROUTE_PAYLOAD).json()
        assert body["estimated_wait_reduction_minutes"] == 0

    def test_no_alternatives_returns_no_better_option_status(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_NO_ALTERNATIVES_ROUTE_PAYLOAD).json()
        assert body["route_status"] == RouteStatus.NO_BETTER_OPTION.value

    def test_no_alternatives_returns_no_alternate_gate(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_NO_ALTERNATIVES_ROUTE_PAYLOAD).json()
        assert body["alternate_gate"] is None

    def test_no_alternatives_wait_reduction_is_zero(self) -> None:
        body = client.post("/api/v1/recommend-route", json=_NO_ALTERNATIVES_ROUTE_PAYLOAD).json()
        assert body["estimated_wait_reduction_minutes"] == 0


# ---------------------------------------------------------------------------
# POST /api/v1/recommend-route — invalid requests
# ---------------------------------------------------------------------------


class TestRecommendRouteInvalid:
    """Tests for Pydantic validation rejection on malformed route recommendation requests."""

    def test_density_above_100_returns_422(self) -> None:
        payload = {**_CONGESTED_ROUTE_PAYLOAD, "crowd_density": 105.0}
        assert client.post("/api/v1/recommend-route", json=payload).status_code == 422

    def test_negative_queue_time_returns_422(self) -> None:
        payload = {**_CONGESTED_ROUTE_PAYLOAD, "queue_time_minutes": -1}
        assert client.post("/api/v1/recommend-route", json=payload).status_code == 422

    def test_invalid_event_phase_returns_422(self) -> None:
        payload = {**_CONGESTED_ROUTE_PAYLOAD, "event_phase": "extra_time"}
        assert client.post("/api/v1/recommend-route", json=payload).status_code == 422

    def test_blank_current_gate_returns_422(self) -> None:
        payload = {**_CONGESTED_ROUTE_PAYLOAD, "current_gate": "  "}
        assert client.post("/api/v1/recommend-route", json=payload).status_code == 422

    def test_missing_required_field_returns_422(self) -> None:
        payload = {k: v for k, v in _CONGESTED_ROUTE_PAYLOAD.items() if k != "current_gate"}
        assert client.post("/api/v1/recommend-route", json=payload).status_code == 422

    def test_empty_body_returns_422(self) -> None:
        assert client.post("/api/v1/recommend-route", json={}).status_code == 422


# ---------------------------------------------------------------------------
# GET /ui — web interface
# ---------------------------------------------------------------------------


class TestUIEndpoint:
    """Tests for the single-page web interface endpoint."""

    def test_ui_returns_200(self) -> None:
        response = client.get("/ui")
        assert response.status_code == 200

    def test_ui_response_contains_project_name(self) -> None:
        response = client.get("/ui")
        assert "SmartFlow" in response.text

    def test_ui_content_type_is_html(self) -> None:
        response = client.get("/ui")
        assert "text/html" in response.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Logic consistency — queue factor must not contradict recommendation
# ---------------------------------------------------------------------------


class TestQueueFactorRecommendationConsistency:
    """Verify that a queue-related contributing factor is never paired with
    a 'No immediate action required' recommendation at LOW risk.

    This guards against the contradiction where the system lists
    'Long/Moderate queue wait' as a factor but simultaneously dismisses
    the situation as requiring no action.
    """

    def test_queue_29min_low_density_does_not_say_no_action(self) -> None:
        """queue=29min (below floor, LOW risk) must not say 'No immediate action'."""
        payload = {
            "zone": "Gate A",
            "crowd_density": 5.0,
            "queue_time_minutes": 29,
            "event_phase": "live_play",
            "weather": "clear",
            "special_event": False,
        }
        body = client.post("/api/v1/analyze-crowd", json=payload).json()
        assert body["risk_level"] == RiskLevel.LOW.value
        # A queue factor must be present
        assert any("queue" in f.lower() for f in body["contributing_factors"])
        # Recommendation must NOT dismiss the queue factor
        assert "No immediate action required" not in body["recommendation"]

    def test_queue_10min_low_density_does_not_say_no_action(self) -> None:
        """queue=10min (moderate queue factor) must not say 'No immediate action'."""
        payload = {
            "zone": "Gate B",
            "crowd_density": 5.0,
            "queue_time_minutes": 10,
            "event_phase": "live_play",
            "weather": "clear",
            "special_event": False,
        }
        body = client.post("/api/v1/analyze-crowd", json=payload).json()
        assert body["risk_level"] == RiskLevel.LOW.value
        assert any("queue" in f.lower() for f in body["contributing_factors"])
        assert "No immediate action required" not in body["recommendation"]

    def test_zero_queue_clean_conditions_says_no_action(self) -> None:
        """Truly clean conditions (no factors) must still say 'No immediate action'."""
        payload = {
            "zone": "Gate C",
            "crowd_density": 5.0,
            "queue_time_minutes": 0,
            "event_phase": "live_play",
            "weather": "clear",
            "special_event": False,
        }
        body = client.post("/api/v1/analyze-crowd", json=payload).json()
        assert body["risk_level"] == RiskLevel.LOW.value
        assert "No immediate action required" in body["recommendation"]

    def test_queue_factor_recommendation_contains_monitor(self) -> None:
        """When a queue factor is present at LOW risk, recommendation must say 'monitor'."""
        payload = {
            "zone": "Gate D",
            "crowd_density": 5.0,
            "queue_time_minutes": 15,
            "event_phase": "live_play",
            "weather": "clear",
            "special_event": False,
        }
        body = client.post("/api/v1/analyze-crowd", json=payload).json()
        assert body["risk_level"] == RiskLevel.LOW.value
        assert "monitor" in body["recommendation"].lower()


# ---------------------------------------------------------------------------
# Route wait reduction cap — integration test
# ---------------------------------------------------------------------------


class TestRouteWaitReductionCapIntegration:
    """Verify that the route recommendation endpoint caps wait reduction at 60 minutes.

    This guards against the inconsistency where crowd analysis caps at 60 min
    but route recommendation previously had no ceiling, allowing absurd values
    like 221 minutes for queue=480min, post_match.
    """

    def test_extreme_queue_route_wait_reduction_is_capped(self) -> None:
        """queue=480min, post_match via HTTP must return <= 60 min reduction."""
        payload = {
            "current_gate": "Gate A",
            "crowd_density": 80.0,
            "queue_time_minutes": 480,
            "event_phase": "post_match",
            "nearby_alternatives": ["Gate B"],
        }
        body = client.post("/api/v1/recommend-route", json=payload).json()
        assert body["route_status"] == RouteStatus.RECOMMENDED.value
        assert body["estimated_wait_reduction_minutes"] <= 60

    def test_route_and_crowd_wait_reduction_caps_match(self) -> None:
        """Both endpoints must return <= 60 min for extreme queue inputs."""
        crowd_payload = {
            "zone": "Gate A",
            "crowd_density": 0.0,
            "queue_time_minutes": 480,
            "event_phase": "post_match",
            "weather": "clear",
            "special_event": False,
        }
        route_payload = {
            "current_gate": "Gate A",
            "crowd_density": 80.0,
            "queue_time_minutes": 480,
            "event_phase": "post_match",
            "nearby_alternatives": ["Gate B"],
        }
        crowd_body = client.post("/api/v1/analyze-crowd", json=crowd_payload).json()
        route_body = client.post("/api/v1/recommend-route", json=route_payload).json()
        assert crowd_body["estimated_wait_reduction_minutes"] <= 60
        assert route_body["estimated_wait_reduction_minutes"] <= 60
