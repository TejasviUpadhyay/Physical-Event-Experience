"""
Unit tests for the SmartFlow AI service layer.

These tests call service functions directly — bypassing the HTTP layer —
to verify that the scoring engine, risk classification, congestion prediction,
and route recommendation logic produce exact, deterministic outputs.

Each test is self-contained and uses only the public/internal service
functions and the constants they depend on.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.models import (
    CrowdAnalysisRequest,
    EventPhase,
    RiskLevel,
    RouteRecommendationRequest,
    RouteStatus,
    WeatherCondition,
)
from app.services import (
    _CONFIDENCE_BASE,
    _CONFIDENCE_BOUNDARY_MARGIN,
    _CONFIDENCE_BOUNDARY_PENALTY,
    _CONFIDENCE_MAX,
    _CONFIDENCE_PER_FACTOR,
    _HIGH_RISK_THRESHOLD,
    _MEDIUM_RISK_THRESHOLD,
    _QUEUE_MEDIUM_FLOOR_MINUTES,
    _REROUTE_BASE_REDUCTION_RATIO,
    _REROUTE_DENSITY_THRESHOLD,
    _REROUTE_PHASE_BONUS_MINUTES,
    _REROUTE_QUEUE_THRESHOLD_MINUTES,
    _build_contributing_factors,
    _build_route_congestion_reason,
    _classify_risk,
    _compute_confidence_score,
    _compute_severity_score,
    _estimate_crowd_wait_reduction,
    _estimate_route_wait_reduction,
    _predict_congestion,
    analyze_crowd_conditions,
    recommend_alternate_route,
)


# ---------------------------------------------------------------------------
# _compute_severity_score
# ---------------------------------------------------------------------------


class TestComputeSeverityScore:
    """Verify the weighted severity scoring formula produces exact outputs."""

    def test_minimum_inputs_produce_zero_score(self) -> None:
        # density=0, queue=0, live_play (0 bump), clear (0 bump), no special event
        score = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == 0.0

    def test_density_only_contribution(self) -> None:
        # density=100 * 0.50 = 50.0; all other inputs zero/minimal
        score = _compute_severity_score(100.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == 50.0

    def test_queue_only_contribution(self) -> None:
        # queue=60 (cap) → normalised=100 → 100 * 0.25 = 25.0
        score = _compute_severity_score(0.0, 60, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == 25.0

    def test_queue_above_cap_is_clamped(self) -> None:
        # queue=120 > cap=60 → same as queue=60
        score_at_cap = _compute_severity_score(0.0, 60, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        score_above_cap = _compute_severity_score(0.0, 120, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score_at_cap == score_above_cap

    def test_halftime_phase_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        halftime = _compute_severity_score(0.0, 0, EventPhase.HALFTIME, WeatherCondition.CLEAR, False)
        assert halftime - base == pytest.approx(15.0)

    def test_post_match_phase_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        post_match = _compute_severity_score(0.0, 0, EventPhase.POST_MATCH, WeatherCondition.CLEAR, False)
        assert post_match - base == pytest.approx(20.0)

    def test_pre_match_phase_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        pre_match = _compute_severity_score(0.0, 0, EventPhase.PRE_MATCH, WeatherCondition.CLEAR, False)
        assert pre_match - base == pytest.approx(5.0)

    def test_rainy_weather_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        rainy = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.RAINY, False)
        assert rainy - base == pytest.approx(10.0)

    def test_hot_weather_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        hot = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.HOT, False)
        assert hot - base == pytest.approx(6.0)

    def test_windy_weather_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        windy = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.WINDY, False)
        assert windy - base == pytest.approx(3.0)

    def test_special_event_bump(self) -> None:
        base = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        special = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, True)
        assert special - base == pytest.approx(12.0)

    def test_combined_high_congestion_scenario(self) -> None:
        # density=82 * 0.50 = 41.0
        # queue=25/60 * 100 * 0.25 = 10.42
        # halftime bump = 15.0
        # rainy bump = 10.0
        # total = 76.42 → but let's verify the actual output matches
        score = _compute_severity_score(82.0, 25, EventPhase.HALFTIME, WeatherCondition.RAINY, False)
        assert score == pytest.approx(76.42, abs=0.01)

    def test_score_is_clamped_to_100(self) -> None:
        # Maximum possible inputs should not exceed 100.
        score = _compute_severity_score(100.0, 480, EventPhase.POST_MATCH, WeatherCondition.RAINY, True)
        assert score <= 100.0

    def test_score_is_never_negative(self) -> None:
        score = _compute_severity_score(0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score >= 0.0

    def test_score_is_rounded_to_two_decimal_places(self) -> None:
        score = _compute_severity_score(33.3, 7, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == round(score, 2)


# ---------------------------------------------------------------------------
# _classify_risk
# ---------------------------------------------------------------------------


class TestClassifyRisk:
    """Verify risk level classification at and around threshold boundaries.

    All calls pass queue_time_minutes=0 unless the test is specifically
    exercising the queue floor, so the score-based path is isolated.
    """

    def test_score_at_high_threshold_is_high(self) -> None:
        assert _classify_risk(_HIGH_RISK_THRESHOLD, 0) == RiskLevel.HIGH

    def test_score_above_high_threshold_is_high(self) -> None:
        assert _classify_risk(_HIGH_RISK_THRESHOLD + 1.0, 0) == RiskLevel.HIGH

    def test_score_just_below_high_threshold_is_medium(self) -> None:
        assert _classify_risk(_HIGH_RISK_THRESHOLD - 0.01, 0) == RiskLevel.MEDIUM

    def test_score_at_medium_threshold_is_medium(self) -> None:
        assert _classify_risk(_MEDIUM_RISK_THRESHOLD, 0) == RiskLevel.MEDIUM

    def test_score_above_medium_threshold_is_medium(self) -> None:
        assert _classify_risk(_MEDIUM_RISK_THRESHOLD + 1.0, 0) == RiskLevel.MEDIUM

    def test_score_just_below_medium_threshold_is_low(self) -> None:
        assert _classify_risk(_MEDIUM_RISK_THRESHOLD - 0.01, 0) == RiskLevel.LOW

    def test_score_zero_is_low(self) -> None:
        assert _classify_risk(0.0, 0) == RiskLevel.LOW

    def test_score_100_is_high(self) -> None:
        assert _classify_risk(100.0, 0) == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# _compute_confidence_score
# ---------------------------------------------------------------------------


class TestComputeConfidenceScore:
    """Verify confidence scoring logic including boundary penalties."""

    def test_single_factor_produces_base_plus_one_factor(self) -> None:
        score = _compute_confidence_score(["Factor A"], severity_score=80.0)
        expected = _CONFIDENCE_BASE + _CONFIDENCE_PER_FACTOR
        assert score == pytest.approx(expected)

    def test_multiple_factors_increase_confidence(self) -> None:
        score_one = _compute_confidence_score(["A"], severity_score=80.0)
        score_three = _compute_confidence_score(["A", "B", "C"], severity_score=80.0)
        assert score_three > score_one

    def test_score_near_high_boundary_receives_penalty(self) -> None:
        # severity_score within 5 points of _HIGH_RISK_THRESHOLD (65.0)
        near_boundary = _HIGH_RISK_THRESHOLD + _CONFIDENCE_BOUNDARY_MARGIN - 0.1
        score_near = _compute_confidence_score(["A"], severity_score=near_boundary)
        score_far = _compute_confidence_score(["A"], severity_score=90.0)
        assert score_near < score_far

    def test_score_near_medium_boundary_receives_penalty(self) -> None:
        near_boundary = _MEDIUM_RISK_THRESHOLD + _CONFIDENCE_BOUNDARY_MARGIN - 0.1
        score_near = _compute_confidence_score(["A"], severity_score=near_boundary)
        score_far = _compute_confidence_score(["A"], severity_score=10.0)
        assert score_near < score_far

    def test_confidence_never_exceeds_max(self) -> None:
        many_factors = [f"Factor {i}" for i in range(20)]
        score = _compute_confidence_score(many_factors, severity_score=90.0)
        assert score <= _CONFIDENCE_MAX

    def test_confidence_never_below_zero(self) -> None:
        score = _compute_confidence_score([], severity_score=_HIGH_RISK_THRESHOLD)
        assert score >= 0.0

    def test_boundary_penalty_exact_value(self) -> None:
        # At exactly the boundary, penalty applies.
        score_at_boundary = _compute_confidence_score(["A"], severity_score=_HIGH_RISK_THRESHOLD)
        score_away = _compute_confidence_score(["A"], severity_score=90.0)
        assert score_away - score_at_boundary == pytest.approx(_CONFIDENCE_BOUNDARY_PENALTY)


# ---------------------------------------------------------------------------
# _predict_congestion
# ---------------------------------------------------------------------------


class TestPredictCongestion:
    """Verify congestion prediction logic."""

    def test_high_density_halftime_predicts_congestion(self) -> None:
        assert _predict_congestion(65.0, EventPhase.HALFTIME, False, 0) is True

    def test_high_density_post_match_predicts_congestion(self) -> None:
        assert _predict_congestion(65.0, EventPhase.POST_MATCH, False, 0) is True

    def test_high_density_special_event_predicts_congestion(self) -> None:
        assert _predict_congestion(65.0, EventPhase.LIVE_PLAY, True, 0) is True

    def test_low_density_halftime_does_not_predict_congestion(self) -> None:
        # Density below threshold — no prediction even during halftime.
        assert _predict_congestion(50.0, EventPhase.HALFTIME, False, 0) is False

    def test_high_density_live_play_no_special_event_does_not_predict(self) -> None:
        # Density elevated but no movement trigger.
        assert _predict_congestion(65.0, EventPhase.LIVE_PLAY, False, 0) is False

    def test_density_exactly_at_threshold_with_trigger_predicts(self) -> None:
        assert _predict_congestion(60.0, EventPhase.HALFTIME, False, 0) is True

    def test_density_just_below_threshold_does_not_predict(self) -> None:
        assert _predict_congestion(59.9, EventPhase.HALFTIME, False, 0) is False


# ---------------------------------------------------------------------------
# _estimate_crowd_wait_reduction
# ---------------------------------------------------------------------------


class TestEstimateCrowdWaitReduction:
    """Verify wait reduction estimation for crowd analysis."""

    def test_low_risk_produces_zero_reduction(self) -> None:
        assert _estimate_crowd_wait_reduction(RiskLevel.LOW, 30) == 0

    def test_medium_risk_reduction_ratio(self) -> None:
        result = _estimate_crowd_wait_reduction(RiskLevel.MEDIUM, 20)
        assert result == round(20 * 0.35)

    def test_high_risk_reduction_ratio(self) -> None:
        result = _estimate_crowd_wait_reduction(RiskLevel.HIGH, 20)
        assert result == round(20 * 0.55)

    def test_zero_queue_time_always_produces_zero(self) -> None:
        assert _estimate_crowd_wait_reduction(RiskLevel.HIGH, 0) == 0

    def test_result_is_non_negative(self) -> None:
        for level in RiskLevel:
            assert _estimate_crowd_wait_reduction(level, 10) >= 0


# ---------------------------------------------------------------------------
# _estimate_route_wait_reduction
# ---------------------------------------------------------------------------


class TestEstimateRouteWaitReduction:
    """Verify wait reduction estimation for route recommendations."""

    def test_live_play_no_phase_bonus(self) -> None:
        result = _estimate_route_wait_reduction(20, EventPhase.LIVE_PLAY)
        expected = round(20 * _REROUTE_BASE_REDUCTION_RATIO)
        assert result == expected

    def test_halftime_adds_phase_bonus(self) -> None:
        base = round(20 * _REROUTE_BASE_REDUCTION_RATIO)
        result = _estimate_route_wait_reduction(20, EventPhase.HALFTIME)
        assert result == base + _REROUTE_PHASE_BONUS_MINUTES

    def test_post_match_adds_phase_bonus(self) -> None:
        base = round(20 * _REROUTE_BASE_REDUCTION_RATIO)
        result = _estimate_route_wait_reduction(20, EventPhase.POST_MATCH)
        assert result == base + _REROUTE_PHASE_BONUS_MINUTES

    def test_pre_match_no_phase_bonus(self) -> None:
        result = _estimate_route_wait_reduction(20, EventPhase.PRE_MATCH)
        expected = round(20 * _REROUTE_BASE_REDUCTION_RATIO)
        assert result == expected

    def test_zero_queue_time_produces_phase_bonus_only_for_high_movement(self) -> None:
        result_halftime = _estimate_route_wait_reduction(0, EventPhase.HALFTIME)
        assert result_halftime == _REROUTE_PHASE_BONUS_MINUTES

    def test_zero_queue_time_live_play_produces_zero(self) -> None:
        assert _estimate_route_wait_reduction(0, EventPhase.LIVE_PLAY) == 0


# ---------------------------------------------------------------------------
# _build_contributing_factors
# ---------------------------------------------------------------------------


class TestBuildContributingFactors:
    """Verify every branch of the contributing factors builder produces the correct output.

    Each test isolates a single condition to confirm the exact factor string
    and that unrelated conditions produce no spurious entries.
    """

    # ------------------------------------------------------------------
    # Density branch
    # ------------------------------------------------------------------

    def test_very_high_density_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            80.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Very high crowd density" in f for f in factors)
        assert any("80.0%" in f for f in factors)

    def test_elevated_density_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            60.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Elevated crowd density" in f for f in factors)
        assert any("60.0%" in f for f in factors)

    def test_low_density_produces_no_density_factor(self) -> None:
        factors = _build_contributing_factors(
            30.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert not any("density" in f.lower() for f in factors)

    def test_density_boundary_75_is_very_high(self) -> None:
        factors = _build_contributing_factors(
            75.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Very high crowd density" in f for f in factors)

    def test_density_boundary_50_is_elevated(self) -> None:
        factors = _build_contributing_factors(
            50.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Elevated crowd density" in f for f in factors)

    # ------------------------------------------------------------------
    # Queue time branch
    # ------------------------------------------------------------------

    def test_long_queue_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 25, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Long queue wait of 25 minutes" in f for f in factors)

    def test_moderate_queue_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 15, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Moderate queue wait of 15 minutes" in f for f in factors)

    def test_short_queue_produces_no_queue_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 5, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert not any("queue" in f.lower() for f in factors)

    def test_queue_boundary_20_is_long(self) -> None:
        factors = _build_contributing_factors(
            0.0, 20, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Long queue wait" in f for f in factors)

    def test_queue_boundary_10_is_moderate(self) -> None:
        factors = _build_contributing_factors(
            0.0, 10, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Moderate queue wait" in f for f in factors)

    # ------------------------------------------------------------------
    # Event phase branch
    # ------------------------------------------------------------------

    def test_halftime_phase_produces_phase_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.HALFTIME, WeatherCondition.CLEAR, False
        )
        assert any("Halftime" in f and "simultaneous crowd movement" in f for f in factors)

    def test_post_match_phase_produces_phase_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.POST_MATCH, WeatherCondition.CLEAR, False
        )
        assert any("Post Match" in f and "simultaneous crowd movement" in f for f in factors)

    def test_live_play_produces_no_phase_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert not any("simultaneous crowd movement" in f for f in factors)

    def test_pre_match_produces_no_phase_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.PRE_MATCH, WeatherCondition.CLEAR, False
        )
        assert not any("simultaneous crowd movement" in f for f in factors)

    # ------------------------------------------------------------------
    # Weather branch
    # ------------------------------------------------------------------

    def test_rainy_weather_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.RAINY, False
        )
        assert any("Rain concentrating crowds" in f for f in factors)

    def test_hot_weather_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.HOT, False
        )
        assert any("Heat driving crowds" in f for f in factors)

    def test_windy_weather_produces_correct_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.WINDY, False
        )
        assert any("Wind conditions" in f for f in factors)

    def test_clear_weather_produces_no_weather_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert not any(
            any(w in f.lower() for w in ["rain", "heat", "wind", "weather"])
            for f in factors
        )

    # ------------------------------------------------------------------
    # Special event branch
    # ------------------------------------------------------------------

    def test_special_event_true_produces_surge_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, True
        )
        assert any("crowd-amplifying event" in f for f in factors)

    def test_special_event_false_produces_no_surge_factor(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert not any("crowd-amplifying event" in f for f in factors)

    # ------------------------------------------------------------------
    # All-clear fallback
    # ------------------------------------------------------------------

    def test_all_clear_inputs_produce_fallback_message(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert factors == ["All conditions within normal operating parameters"]

    def test_fallback_list_has_exactly_one_entry(self) -> None:
        factors = _build_contributing_factors(
            0.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert len(factors) == 1

    # ------------------------------------------------------------------
    # Factor ordering
    # ------------------------------------------------------------------

    def test_density_factor_appears_before_queue_factor(self) -> None:
        """Density has the highest weight and must be listed first."""
        factors = _build_contributing_factors(
            80.0, 25, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        density_idx = next(i for i, f in enumerate(factors) if "density" in f.lower())
        queue_idx = next(i for i, f in enumerate(factors) if "queue" in f.lower())
        assert density_idx < queue_idx

    def test_special_event_factor_appears_last(self) -> None:
        """Special event is transient and must be listed last."""
        factors = _build_contributing_factors(
            80.0, 25, EventPhase.HALFTIME, WeatherCondition.RAINY, True
        )
        assert "crowd-amplifying event" in factors[-1]


# ---------------------------------------------------------------------------
# analyze_crowd_conditions (public function — end-to-end service test)
# ---------------------------------------------------------------------------


class TestAnalyzeCrowdConditions:
    """End-to-end service tests for crowd analysis without the HTTP layer."""

    def _make_request(self, **kwargs: Any) -> CrowdAnalysisRequest:
        defaults = {
            "zone": "Gate A",
            "crowd_density": 50.0,
            "queue_time_minutes": 10,
            "event_phase": EventPhase.LIVE_PLAY,
            "weather": WeatherCondition.CLEAR,
            "special_event": False,
        }
        defaults.update(kwargs)
        return CrowdAnalysisRequest(**defaults)

    def test_high_congestion_returns_high_risk(self) -> None:
        req = self._make_request(
            crowd_density=82.0,
            queue_time_minutes=25,
            event_phase=EventPhase.HALFTIME,
            weather=WeatherCondition.RAINY,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.HIGH

    def test_low_congestion_returns_low_risk(self) -> None:
        req = self._make_request(crowd_density=10.0, queue_time_minutes=2)
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.LOW

    def test_low_risk_wait_reduction_is_zero(self) -> None:
        req = self._make_request(crowd_density=10.0, queue_time_minutes=2)
        result = analyze_crowd_conditions(req)
        assert result.estimated_wait_reduction_minutes == 0

    def test_contributing_factors_is_non_empty(self) -> None:
        req = self._make_request()
        result = analyze_crowd_conditions(req)
        assert len(result.contributing_factors) > 0

    def test_zone_is_echoed_in_response(self) -> None:
        req = self._make_request(zone="Section 42")
        result = analyze_crowd_conditions(req)
        assert result.zone == "Section 42"

    def test_predicted_congestion_true_for_high_density_halftime(self) -> None:
        req = self._make_request(
            crowd_density=75.0,
            event_phase=EventPhase.HALFTIME,
        )
        result = analyze_crowd_conditions(req)
        assert result.predicted_congestion is True

    def test_predicted_congestion_false_for_low_density_live_play(self) -> None:
        req = self._make_request(
            crowd_density=30.0,
            event_phase=EventPhase.LIVE_PLAY,
        )
        result = analyze_crowd_conditions(req)
        assert result.predicted_congestion is False

    def test_severity_score_within_bounds(self) -> None:
        req = self._make_request(crowd_density=100.0, queue_time_minutes=480)
        result = analyze_crowd_conditions(req)
        assert 0.0 <= result.severity_score <= 100.0

    def test_confidence_score_within_bounds(self) -> None:
        req = self._make_request()
        result = analyze_crowd_conditions(req)
        assert 0.0 <= result.confidence_score <= 100.0


# ---------------------------------------------------------------------------
# recommend_alternate_route (public function — end-to-end service test)
# ---------------------------------------------------------------------------


class TestRecommendAlternateRoute:
    """End-to-end service tests for route recommendation without the HTTP layer."""

    def _make_request(self, **kwargs: Any) -> RouteRecommendationRequest:
        defaults = {
            "current_gate": "Gate A",
            "crowd_density": 80.0,
            "queue_time_minutes": 20,
            "event_phase": EventPhase.POST_MATCH,
            "nearby_alternatives": ["Gate B", "Gate C"],
        }
        defaults.update(kwargs)
        return RouteRecommendationRequest(**defaults)

    def test_congested_with_alternatives_returns_recommended(self) -> None:
        req = self._make_request()
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.RECOMMENDED

    def test_recommended_gate_is_first_alternative(self) -> None:
        req = self._make_request(nearby_alternatives=["Gate B", "Gate C"])
        result = recommend_alternate_route(req)
        assert result.alternate_gate == "Gate B"

    def test_clear_conditions_return_clear_status(self) -> None:
        req = self._make_request(crowd_density=10.0, queue_time_minutes=3)
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.CLEAR

    def test_clear_conditions_no_alternate_gate(self) -> None:
        req = self._make_request(crowd_density=10.0, queue_time_minutes=3)
        result = recommend_alternate_route(req)
        assert result.alternate_gate is None

    def test_clear_conditions_zero_wait_reduction(self) -> None:
        req = self._make_request(crowd_density=10.0, queue_time_minutes=3)
        result = recommend_alternate_route(req)
        assert result.estimated_wait_reduction_minutes == 0

    def test_no_alternatives_returns_no_better_option(self) -> None:
        req = self._make_request(nearby_alternatives=None)
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.NO_BETTER_OPTION

    def test_no_alternatives_no_alternate_gate(self) -> None:
        req = self._make_request(nearby_alternatives=None)
        result = recommend_alternate_route(req)
        assert result.alternate_gate is None

    def test_recommended_wait_reduction_is_positive(self) -> None:
        req = self._make_request(queue_time_minutes=20)
        result = recommend_alternate_route(req)
        assert result.estimated_wait_reduction_minutes > 0

    def test_post_match_wait_reduction_includes_phase_bonus(self) -> None:
        req_post = self._make_request(event_phase=EventPhase.POST_MATCH, queue_time_minutes=20)
        req_live = self._make_request(event_phase=EventPhase.LIVE_PLAY, queue_time_minutes=20)
        result_post = recommend_alternate_route(req_post)
        result_live = recommend_alternate_route(req_live)
        assert result_post.estimated_wait_reduction_minutes > result_live.estimated_wait_reduction_minutes

    def test_destination_zone_echoed_in_response(self) -> None:
        req = self._make_request(destination_zone="North Stand")
        result = recommend_alternate_route(req)
        assert result.alternate_zone == "North Stand"

    def test_current_gate_echoed_in_response(self) -> None:
        req = self._make_request(current_gate="Gate Z")
        result = recommend_alternate_route(req)
        assert result.current_gate == "Gate Z"

    def test_confidence_score_within_bounds(self) -> None:
        for status in [
            self._make_request(),
            self._make_request(crowd_density=10.0, queue_time_minutes=3),
            self._make_request(nearby_alternatives=None),
        ]:
            result = recommend_alternate_route(status)
            assert 0.0 <= result.confidence_score <= 100.0


# ---------------------------------------------------------------------------
# Issue 1 — density=66 returning LOW (not a bug — documented here as a test)
# ---------------------------------------------------------------------------


class TestDensityVsSeverityThresholdClarification:
    """Confirm that risk classification is based on SEVERITY SCORE, not raw density.

    The HIGH threshold (65.0) applies to the severity score, not to
    crowd_density.  density=66 * 0.50 weight = 33.0 severity → LOW.
    This is correct and intentional.  These tests document that behaviour
    explicitly so future readers do not mistake it for a bug.
    """

    def test_density_66_with_no_other_signals_is_low_risk(self) -> None:
        """density=66 alone produces severity=33.0, which is below MEDIUM (35.0)."""
        score = _compute_severity_score(66.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == pytest.approx(33.0)
        assert _classify_risk(score, 0) == RiskLevel.LOW

    def test_density_70_with_no_other_signals_is_medium_risk(self) -> None:
        """density=70 produces severity=35.0, which exactly meets MEDIUM threshold."""
        score = _compute_severity_score(70.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == pytest.approx(35.0)
        assert _classify_risk(score, 0) == RiskLevel.MEDIUM

    def test_density_alone_cannot_reach_high_risk(self) -> None:
        """Even density=100 produces severity=50.0, which is below HIGH (65.0)."""
        score = _compute_severity_score(100.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == pytest.approx(50.0)
        assert _classify_risk(score, 0) == RiskLevel.MEDIUM

    def test_high_risk_requires_multiple_signals(self) -> None:
        """HIGH risk requires density + phase/weather/queue combination."""
        # density=80 + post_match bump alone: 40 + 20 = 60 → still MEDIUM
        score_medium = _compute_severity_score(80.0, 0, EventPhase.POST_MATCH, WeatherCondition.CLEAR, False)
        assert _classify_risk(score_medium, 0) == RiskLevel.MEDIUM
        # density=80 + post_match + rainy: 40 + 20 + 10 = 70 → HIGH
        score_high = _compute_severity_score(80.0, 0, EventPhase.POST_MATCH, WeatherCondition.RAINY, False)
        assert _classify_risk(score_high, 0) == RiskLevel.HIGH


# ---------------------------------------------------------------------------
# Issue 2 — Queue floor: long queue guarantees at least MEDIUM
# ---------------------------------------------------------------------------


class TestQueueMediumFloor:
    """Verify that a very long queue always produces at least MEDIUM risk.

    Without the floor, low-density + long-queue scenarios could never reach
    MEDIUM because the density component dominates the weighted score.
    """

    def test_low_density_long_queue_reaches_medium(self) -> None:
        """density=10, queue=40min should now be MEDIUM (floor at 30min)."""
        score = _compute_severity_score(10.0, 40, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        risk = _classify_risk(score, queue_time_minutes=40)
        assert risk == RiskLevel.MEDIUM

    def test_zero_density_queue_at_floor_is_medium(self) -> None:
        """density=0, queue=30min (exactly at floor) → MEDIUM."""
        score = _compute_severity_score(0.0, 30, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        risk = _classify_risk(score, queue_time_minutes=30)
        assert risk == RiskLevel.MEDIUM

    def test_zero_density_queue_below_floor_is_low(self) -> None:
        """density=0, queue=29min (just below floor) → LOW."""
        score = _compute_severity_score(0.0, 29, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        risk = _classify_risk(score, queue_time_minutes=29)
        assert risk == RiskLevel.LOW

    def test_floor_does_not_override_high_risk(self) -> None:
        """A score that already reaches HIGH is not downgraded by the floor logic."""
        score = _compute_severity_score(100.0, 60, EventPhase.POST_MATCH, WeatherCondition.RAINY, True)
        risk = _classify_risk(score, queue_time_minutes=60)
        assert risk == RiskLevel.HIGH

    def test_floor_does_not_affect_normal_medium_classification(self) -> None:
        """A score that reaches MEDIUM via the formula is unaffected by the floor."""
        score = _compute_severity_score(70.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False)
        assert score == pytest.approx(35.0)
        risk = _classify_risk(score, queue_time_minutes=0)
        assert risk == RiskLevel.MEDIUM

    def test_floor_constant_value(self) -> None:
        """The floor constant is 30 minutes."""
        assert _QUEUE_MEDIUM_FLOOR_MINUTES == 30

    def test_analyze_crowd_conditions_applies_floor(self) -> None:
        """End-to-end: low density + 40min queue returns MEDIUM via the service."""
        from app.models import CrowdAnalysisRequest
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=10.0,
            queue_time_minutes=40,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.MEDIUM

    def test_analyze_crowd_conditions_queue_29_stays_low(self) -> None:
        """End-to-end: low density + 29min queue stays LOW (below floor)."""
        from app.models import CrowdAnalysisRequest
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=10.0,
            queue_time_minutes=29,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.LOW


# ---------------------------------------------------------------------------
# Issue 3 — Recommendation wording for multi-factor LOW cases
# ---------------------------------------------------------------------------


class TestMultiFactorLowRecommendationWording:
    """Verify that LOW risk with 2+ real factors produces a monitoring advisory."""

    def test_single_factor_low_says_no_action_required(self) -> None:
        """One factor (or fallback) → standard 'no immediate action' wording."""
        from app.models import CrowdAnalysisRequest
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=5.0,
            queue_time_minutes=0,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.LOW
        assert "No immediate action required" in result.recommendation

    def test_two_factor_low_says_monitor(self) -> None:
        """Two factors (density + queue) → monitoring advisory wording."""
        from app.models import CrowdAnalysisRequest
        # density=50 (elevated) + queue=10 (moderate) + windy = 3 factors → LOW
        req = CrowdAnalysisRequest(
            zone="Gate E",
            crowd_density=50.0,
            queue_time_minutes=10,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.WINDY,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.LOW
        assert len(result.contributing_factors) >= 2
        assert "monitor" in result.recommendation.lower()

    def test_multi_factor_low_recommendation_does_not_say_no_action(self) -> None:
        """Multi-factor LOW must not say 'No immediate action required'."""
        from app.models import CrowdAnalysisRequest
        req = CrowdAnalysisRequest(
            zone="Gate E",
            crowd_density=50.0,
            queue_time_minutes=10,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.WINDY,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert "No immediate action required" not in result.recommendation

    def test_medium_risk_wording_unchanged(self) -> None:
        """MEDIUM risk wording is not affected by the factor_count change."""
        from app.models import CrowdAnalysisRequest
        req = CrowdAnalysisRequest(
            zone="Gate B",
            crowd_density=70.0,
            queue_time_minutes=0,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.MEDIUM
        assert "Moderate congestion detected" in result.recommendation

    def test_high_risk_wording_unchanged(self) -> None:
        """HIGH risk wording is not affected by the factor_count change."""
        from app.models import CrowdAnalysisRequest
        req = CrowdAnalysisRequest(
            zone="Gate C",
            crowd_density=90.0,
            queue_time_minutes=35,
            event_phase=EventPhase.HALFTIME,
            weather=WeatherCondition.RAINY,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.HIGH
        assert "Activate crowd management protocols" in result.recommendation


# ---------------------------------------------------------------------------
# Issue 4 — Route reason text accuracy
# ---------------------------------------------------------------------------


class TestRouteReasonTextAccuracy:
    """Verify that route reason text only cites the condition(s) that triggered."""

    def test_both_conditions_exceed_cites_both(self) -> None:
        """When both density and queue exceed thresholds, reason mentions both."""
        reason = _build_route_congestion_reason(
            current_gate="Gate A",
            crowd_density=85.0,
            queue_time_minutes=25,
            density_exceeds=True,
            queue_exceeds=True,
            recommended_gate="Gate B",
        )
        assert "85%" in reason
        assert "25 min" in reason
        assert "both exceeding" in reason

    def test_only_density_exceeds_cites_density_only(self) -> None:
        """When only density triggers, reason mentions density threshold only."""
        reason = _build_route_congestion_reason(
            current_gate="Gate A",
            crowd_density=70.0,
            queue_time_minutes=5,
            density_exceeds=True,
            queue_exceeds=False,
            recommended_gate="Gate B",
        )
        assert "70%" in reason
        assert "density threshold" in reason
        assert "queue" not in reason.lower()

    def test_only_queue_exceeds_cites_queue_only(self) -> None:
        """When only queue triggers, reason mentions queue threshold only."""
        reason = _build_route_congestion_reason(
            current_gate="Gate A",
            crowd_density=10.0,
            queue_time_minutes=15,
            density_exceeds=False,
            queue_exceeds=True,
            recommended_gate="Gate B",
        )
        assert "15 min" in reason
        assert "queue threshold" in reason
        # density value should not appear as a trigger claim
        assert "density threshold" not in reason

    def test_queue_only_reason_does_not_claim_density_exceeds(self) -> None:
        """The F6 scenario: density=10%, queue=15min — reason must not say density exceeded."""
        from app.models import RouteRecommendationRequest
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=10.0,
            queue_time_minutes=15,
            event_phase=EventPhase.LIVE_PLAY,
            nearby_alternatives=["Gate B"],
        )
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.RECOMMENDED
        # Must not claim density exceeded the threshold
        assert "density threshold" not in result.reason
        # Must correctly cite the queue as the trigger
        assert "queue threshold" in result.reason

    def test_both_exceed_reason_is_accurate_end_to_end(self) -> None:
        """When both conditions exceed, end-to-end reason cites both correctly."""
        from app.models import RouteRecommendationRequest
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=85.0,
            queue_time_minutes=25,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B"],
        )
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.RECOMMENDED
        assert "85%" in result.reason
        assert "25 min" in result.reason
        assert "both exceeding" in result.reason

    def test_density_only_trigger_reason_end_to_end(self) -> None:
        """density=70%, queue=5min — only density triggers, reason reflects that."""
        from app.models import RouteRecommendationRequest
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=70.0,
            queue_time_minutes=5,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B"],
        )
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.RECOMMENDED
        assert "density threshold" in result.reason
        assert "queue threshold" not in result.reason


# ---------------------------------------------------------------------------
# Fix 1 — _classify_risk requires queue_time_minutes (no silent default)
# ---------------------------------------------------------------------------


class TestClassifyRiskRequiredQueueParameter:
    """Verify that _classify_risk enforces the queue_time_minutes parameter.

    The parameter was previously optional (default=0), which created a silent
    failure risk: callers that forgot to pass it would never trigger the floor.
    It is now required, so omitting it raises TypeError at call time.
    """

    def test_classify_risk_requires_queue_time_minutes(self) -> None:
        """Calling _classify_risk without queue_time_minutes must raise TypeError."""
        import pytest as _pytest
        with _pytest.raises(TypeError, match="queue_time_minutes"):
            _classify_risk(30.0)  # type: ignore[call-arg]

    def test_classify_risk_with_zero_queue_behaves_as_score_only(self) -> None:
        """Passing queue=0 explicitly isolates the score-based path."""
        assert _classify_risk(34.99, 0) == RiskLevel.LOW
        assert _classify_risk(35.0, 0) == RiskLevel.MEDIUM
        assert _classify_risk(65.0, 0) == RiskLevel.HIGH

    def test_classify_risk_floor_fires_only_when_queue_passed(self) -> None:
        """Floor fires when queue >= 30 is explicitly passed."""
        assert _classify_risk(10.0, 30) == RiskLevel.MEDIUM
        assert _classify_risk(10.0, 29) == RiskLevel.LOW


# ---------------------------------------------------------------------------
# Fix 2 — Congestion prediction fires for queue-floor MEDIUM cases
# ---------------------------------------------------------------------------


class TestCongestionPredictionQueueFloor:
    """Verify that predicted_congestion=True when the queue floor elevates risk.

    Previously, a case like density=10%, queue=40min would be elevated to
    MEDIUM via the floor but still return predicted_congestion=False, because
    the density-based prediction check (density >= 60%) was not met.  A long
    queue is itself a worsening signal and should trigger the prediction.
    """

    def test_queue_at_floor_predicts_congestion(self) -> None:
        """queue=30min (exactly at floor) → predicted_congestion=True."""
        assert _predict_congestion(10.0, EventPhase.LIVE_PLAY, False, 30) is True

    def test_queue_above_floor_predicts_congestion(self) -> None:
        """queue=40min → predicted_congestion=True."""
        assert _predict_congestion(10.0, EventPhase.LIVE_PLAY, False, 40) is True

    def test_queue_below_floor_does_not_predict_congestion(self) -> None:
        """queue=29min (below floor) → predicted_congestion=False (no density trigger)."""
        assert _predict_congestion(10.0, EventPhase.LIVE_PLAY, False, 29) is False

    def test_density_trigger_still_works_independently(self) -> None:
        """Density-based prediction is unaffected by the queue parameter."""
        assert _predict_congestion(65.0, EventPhase.HALFTIME, False, 0) is True
        assert _predict_congestion(65.0, EventPhase.LIVE_PLAY, False, 0) is False

    def test_both_triggers_active_returns_true(self) -> None:
        """Both density and queue triggers active → True."""
        assert _predict_congestion(65.0, EventPhase.HALFTIME, False, 40) is True

    def test_analyze_crowd_conditions_sets_predicted_congestion_for_long_queue(self) -> None:
        """End-to-end: low density + 40min queue → MEDIUM + predicted_congestion=True."""
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=10.0,
            queue_time_minutes=40,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.MEDIUM
        assert result.predicted_congestion is True

    def test_analyze_crowd_conditions_queue_29_no_congestion_prediction(self) -> None:
        """End-to-end: low density + 29min queue → LOW + predicted_congestion=False."""
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=10.0,
            queue_time_minutes=29,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.risk_level == RiskLevel.LOW
        assert result.predicted_congestion is False

    def test_analyze_crowd_conditions_queue_floor_recommendation_includes_worsen_warning(self) -> None:
        """End-to-end: queue floor MEDIUM includes 'worsen shortly' in recommendation."""
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=10.0,
            queue_time_minutes=40,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert "worsen shortly" in result.recommendation


# ---------------------------------------------------------------------------
# Fix 3 — Route validation: current_gate cannot appear in nearby_alternatives
# ---------------------------------------------------------------------------


class TestRouteAlternativesMustDifferFromCurrentGate:
    """Verify that nearby_alternatives cannot contain the current gate.

    Recommending the gate the attendee is already at is nonsensical and would
    produce a misleading response ('Use Gate A instead of Gate A').
    """

    def test_current_gate_in_alternatives_raises_422_via_http(self) -> None:
        """HTTP layer: current_gate in alternatives → 422."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        payload = {
            "current_gate": "Gate A",
            "crowd_density": 80.0,
            "queue_time_minutes": 20,
            "event_phase": "post_match",
            "nearby_alternatives": ["Gate A", "Gate B"],
        }
        response = client.post("/api/v1/recommend-route", json=payload)
        assert response.status_code == 422

    def test_current_gate_as_only_alternative_raises_422(self) -> None:
        """Single-entry alternatives list containing current_gate → 422."""
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app, raise_server_exceptions=False)
        payload = {
            "current_gate": "Gate A",
            "crowd_density": 80.0,
            "queue_time_minutes": 20,
            "event_phase": "post_match",
            "nearby_alternatives": ["Gate A"],
        }
        response = client.post("/api/v1/recommend-route", json=payload)
        assert response.status_code == 422

    def test_current_gate_in_alternatives_model_validation(self) -> None:
        """Model-level: current_gate in alternatives raises ValidationError."""
        import pytest as _pytest
        from pydantic import ValidationError
        with _pytest.raises(ValidationError, match="must not contain current_gate"):
            RouteRecommendationRequest(
                current_gate="Gate A",
                crowd_density=80.0,
                queue_time_minutes=20,
                event_phase=EventPhase.POST_MATCH,
                nearby_alternatives=["Gate A", "Gate B"],
            )

    def test_valid_alternatives_not_containing_current_gate_accepted(self) -> None:
        """Alternatives that do not include current_gate are accepted normally."""
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=20,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B", "Gate C"],
        )
        assert req.nearby_alternatives == ["Gate B", "Gate C"]

    def test_case_insensitive_current_gate_check(self) -> None:
        """Check is case-insensitive: 'gate a' matches 'Gate A'."""
        import pytest as _pytest
        from pydantic import ValidationError
        with _pytest.raises(ValidationError, match="must not contain current_gate"):
            RouteRecommendationRequest(
                current_gate="Gate A",
                crowd_density=80.0,
                queue_time_minutes=20,
                event_phase=EventPhase.POST_MATCH,
                nearby_alternatives=["gate a", "Gate B"],
            )

    def test_none_alternatives_still_accepted(self) -> None:
        """None alternatives (no alternatives provided) is still valid."""
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=20,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=None,
        )
        assert req.nearby_alternatives is None


# ---------------------------------------------------------------------------
# Fix: _predict_congestion requires queue_time_minutes (no silent default)
# ---------------------------------------------------------------------------


class TestPredictCongestionRequiredQueueParameter:
    """Verify _predict_congestion enforces the queue_time_minutes parameter."""

    def test_predict_congestion_requires_queue_time_minutes(self) -> None:
        """Calling without queue_time_minutes must raise TypeError."""
        with pytest.raises(TypeError):
            _predict_congestion(65.0, EventPhase.HALFTIME, False)  # type: ignore[call-arg]

    def test_queue_zero_isolates_density_path(self) -> None:
        """queue=0 means only the density trigger can fire."""
        assert _predict_congestion(65.0, EventPhase.HALFTIME, False, 0) is True
        assert _predict_congestion(50.0, EventPhase.HALFTIME, False, 0) is False


# ---------------------------------------------------------------------------
# Fix: factor label uses :.1f (no rounding artefacts)
# ---------------------------------------------------------------------------


class TestFactorLabelPrecision:
    """Verify density factor labels show one decimal place, not rounded integers."""

    def test_density_69_9_shows_correct_label(self) -> None:
        """density=69.9 must show '69.9%', not '70%'."""
        factors = _build_contributing_factors(
            69.9, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("69.9%" in f for f in factors)
        assert not any("70%" in f for f in factors)

    def test_density_74_9_shows_correct_label(self) -> None:
        """density=74.9 must show '74.9%', not '75%'."""
        factors = _build_contributing_factors(
            74.9, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("74.9%" in f for f in factors)
        assert not any("75%" in f for f in factors)

    def test_density_80_0_shows_one_decimal(self) -> None:
        """Whole-number density shows one decimal place for consistency."""
        factors = _build_contributing_factors(
            80.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("80.0%" in f for f in factors)

    def test_density_75_0_boundary_is_very_high_with_correct_label(self) -> None:
        """Exactly 75.0 triggers 'Very high' with label '75.0%'."""
        factors = _build_contributing_factors(
            75.0, 0, EventPhase.LIVE_PLAY, WeatherCondition.CLEAR, False
        )
        assert any("Very high crowd density" in f for f in factors)
        assert any("75.0%" in f for f in factors)


# ---------------------------------------------------------------------------
# Fix: estimated_wait_reduction_minutes capped at 60
# ---------------------------------------------------------------------------


class TestWaitReductionCap:
    """Verify that estimated wait reduction is capped at _MAX_WAIT_REDUCTION_MINUTES."""

    def test_extreme_queue_reduction_is_capped(self) -> None:
        """queue=480min, HIGH risk → raw=264, capped at 60."""
        result = _estimate_crowd_wait_reduction(RiskLevel.HIGH, 480)
        assert result == 60

    def test_queue_60_high_risk_is_not_capped(self) -> None:
        """queue=60min, HIGH risk → round(60*0.55)=33, below cap."""
        result = _estimate_crowd_wait_reduction(RiskLevel.HIGH, 60)
        assert result == 33
        assert result <= 60

    def test_queue_200_medium_risk_is_capped(self) -> None:
        """queue=200min, MEDIUM risk → raw=70, capped at 60."""
        result = _estimate_crowd_wait_reduction(RiskLevel.MEDIUM, 200)
        assert result == 60

    def test_zero_queue_is_never_capped(self) -> None:
        """queue=0 always produces 0 regardless of cap."""
        assert _estimate_crowd_wait_reduction(RiskLevel.HIGH, 0) == 0

    def test_analyze_crowd_conditions_caps_wait_reduction(self) -> None:
        """End-to-end: extreme queue produces capped wait reduction."""
        req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=0.0,
            queue_time_minutes=480,
            event_phase=EventPhase.LIVE_PLAY,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        result = analyze_crowd_conditions(req)
        assert result.estimated_wait_reduction_minutes <= 60


# ---------------------------------------------------------------------------
# Fix: assert replaced with explicit guard (safe under -O flag)
# ---------------------------------------------------------------------------


class TestRecommendRouteNoAssert:
    """Verify the route recommendation path does not use bare assert for type narrowing."""

    def test_recommended_path_returns_correct_gate(self) -> None:
        """The RECOMMENDED path must work correctly — no assert crash."""
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=20,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B", "Gate C"],
        )
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.RECOMMENDED
        assert result.alternate_gate == "Gate B"

    def test_recommended_path_with_single_alternative(self) -> None:
        """Single alternative list works correctly on the RECOMMENDED path."""
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=20,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate Z"],
        )
        result = recommend_alternate_route(req)
        assert result.alternate_gate == "Gate Z"


# ---------------------------------------------------------------------------
# Fix: ValidationInfo used instead of Any in model validator
# ---------------------------------------------------------------------------


class TestValidationInfoTyping:
    """Verify the alternatives validator correctly uses ValidationInfo context."""

    def test_current_gate_context_available_in_validator(self) -> None:
        """The validator must correctly read current_gate from validation context."""
        from pydantic import ValidationError
        with pytest.raises(ValidationError, match="must not contain current_gate"):
            RouteRecommendationRequest(
                current_gate="Gate A",
                crowd_density=80.0,
                queue_time_minutes=20,
                event_phase=EventPhase.POST_MATCH,
                nearby_alternatives=["Gate A", "Gate B"],
            )

    def test_valid_alternatives_pass_validator(self) -> None:
        """Alternatives that differ from current_gate pass without error."""
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=20,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B", "Gate C"],
        )
        assert req.nearby_alternatives == ["Gate B", "Gate C"]


# ---------------------------------------------------------------------------
# Fix: _estimate_route_wait_reduction capped at _MAX_WAIT_REDUCTION_MINUTES
# ---------------------------------------------------------------------------


class TestRouteWaitReductionCap:
    """Verify that route wait reduction is capped at _MAX_WAIT_REDUCTION_MINUTES.

    Previously _estimate_route_wait_reduction had no upper bound, meaning
    extreme queue lengths (e.g. queue=480min, post_match) could produce
    absurd values like 221 minutes — inconsistent with the crowd analysis
    endpoint which caps at 60 minutes.
    """

    from app.services import _MAX_WAIT_REDUCTION_MINUTES as _CAP

    def test_extreme_queue_live_play_is_capped(self) -> None:
        """queue=480min, live_play → raw=216, capped at 60."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        result = _estimate_route_wait_reduction(480, EventPhase.LIVE_PLAY)
        assert result == _MAX_WAIT_REDUCTION_MINUTES

    def test_extreme_queue_post_match_is_capped(self) -> None:
        """queue=480min, post_match → raw=216+5=221, capped at 60."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        result = _estimate_route_wait_reduction(480, EventPhase.POST_MATCH)
        assert result == _MAX_WAIT_REDUCTION_MINUTES

    def test_moderate_queue_live_play_is_not_capped(self) -> None:
        """queue=20min, live_play → raw=9, well below cap."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        result = _estimate_route_wait_reduction(20, EventPhase.LIVE_PLAY)
        assert result == round(20 * _REROUTE_BASE_REDUCTION_RATIO)
        assert result < _MAX_WAIT_REDUCTION_MINUTES

    def test_moderate_queue_post_match_is_not_capped(self) -> None:
        """queue=20min, post_match → raw=9+5=14, well below cap."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        result = _estimate_route_wait_reduction(20, EventPhase.POST_MATCH)
        assert result == round(20 * _REROUTE_BASE_REDUCTION_RATIO) + _REROUTE_PHASE_BONUS_MINUTES
        assert result < _MAX_WAIT_REDUCTION_MINUTES

    def test_zero_queue_is_never_capped(self) -> None:
        """queue=0 always produces 0 (or phase bonus only), never hits cap."""
        assert _estimate_route_wait_reduction(0, EventPhase.LIVE_PLAY) == 0
        assert _estimate_route_wait_reduction(0, EventPhase.POST_MATCH) == _REROUTE_PHASE_BONUS_MINUTES

    def test_result_never_exceeds_cap_for_any_phase(self) -> None:
        """Cap holds for all event phases at maximum queue length."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        for phase in EventPhase:
            result = _estimate_route_wait_reduction(480, phase)
            assert result <= _MAX_WAIT_REDUCTION_MINUTES, (
                f"Phase {phase.value}: result {result} exceeds cap {_MAX_WAIT_REDUCTION_MINUTES}"
            )

    def test_recommend_alternate_route_caps_wait_reduction(self) -> None:
        """End-to-end: extreme queue via recommend_alternate_route is capped."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=480,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B"],
        )
        result = recommend_alternate_route(req)
        assert result.route_status == RouteStatus.RECOMMENDED
        assert result.estimated_wait_reduction_minutes <= _MAX_WAIT_REDUCTION_MINUTES

    def test_route_and_crowd_caps_are_consistent(self) -> None:
        """Both endpoints must use the same 60-minute ceiling."""
        from app.services import _MAX_WAIT_REDUCTION_MINUTES
        crowd_req = CrowdAnalysisRequest(
            zone="Gate A",
            crowd_density=0.0,
            queue_time_minutes=480,
            event_phase=EventPhase.POST_MATCH,
            weather=WeatherCondition.CLEAR,
            special_event=False,
        )
        route_req = RouteRecommendationRequest(
            current_gate="Gate A",
            crowd_density=80.0,
            queue_time_minutes=480,
            event_phase=EventPhase.POST_MATCH,
            nearby_alternatives=["Gate B"],
        )
        crowd_result = analyze_crowd_conditions(crowd_req)
        route_result = recommend_alternate_route(route_req)
        assert crowd_result.estimated_wait_reduction_minutes <= _MAX_WAIT_REDUCTION_MINUTES
        assert route_result.estimated_wait_reduction_minutes <= _MAX_WAIT_REDUCTION_MINUTES
