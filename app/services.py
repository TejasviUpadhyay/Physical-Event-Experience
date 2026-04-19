"""
Business logic layer for SmartFlow AI.

All domain logic for crowd analysis and route recommendation lives here.
Functions are kept small, deterministic, and independently testable.
No FastAPI machinery is imported; this layer is framework-agnostic.
"""

from __future__ import annotations

from app.models import (
    CrowdAnalysisRequest,
    CrowdAnalysisResponse,
    EventPhase,
    RiskLevel,
    RouteRecommendationRequest,
    RouteRecommendationResponse,
    RouteStatus,
    WeatherCondition,
)
from app.utils import clamp, format_factors, normalize_to_percentage, round_score, utc_now
from app.gemini_service import (
    generate_crowd_analysis_insight,
    generate_route_recommendation_insight,
)

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

# crowd_density (0–100) contributes 50 % of the base score.
_DENSITY_WEIGHT: float = 0.50

# queue_time_minutes, capped and normalised to 0–100, contributes 25 %.
_QUEUE_WEIGHT: float = 0.25

# Maximum queue time used for normalisation; values above this are treated as
# equally severe (diminishing returns beyond 60 min are not meaningful here).
_QUEUE_CAP_MINUTES: int = 60

# ---------------------------------------------------------------------------
# Contextual score bumps (additive, applied after weighted base)
# ---------------------------------------------------------------------------

# Each event phase adds a fixed bump reflecting typical crowd movement intensity.
_PHASE_BUMPS: dict[EventPhase, float] = {
    EventPhase.PRE_MATCH: 5.0,    # Arrivals create moderate inbound pressure.
    EventPhase.LIVE_PLAY: 0.0,    # Crowd is seated; movement is minimal.
    EventPhase.HALFTIME: 15.0,    # Concession/restroom rush — high lateral movement.
    EventPhase.POST_MATCH: 20.0,  # Mass simultaneous exit — highest movement pressure.
}

# Weather conditions that impede movement or concentrate crowds under cover.
_WEATHER_BUMPS: dict[WeatherCondition, float] = {
    WeatherCondition.CLEAR: 0.0,
    WeatherCondition.WINDY: 3.0,
    WeatherCondition.HOT: 6.0,    # Heat drives people toward shade/water points.
    WeatherCondition.RAINY: 10.0, # Rain concentrates crowds under covered areas.
}

# A special event (goal, penalty, fireworks) causes sudden crowd surges.
_SPECIAL_EVENT_BUMP: float = 12.0

# ---------------------------------------------------------------------------
# Risk classification thresholds
# ---------------------------------------------------------------------------

# Severity score bands → RiskLevel mapping.
_HIGH_RISK_THRESHOLD: float = 65.0
_MEDIUM_RISK_THRESHOLD: float = 35.0

# Queue time floor: a queue this long or longer guarantees at least MEDIUM
# risk regardless of crowd density.  Without this floor, low-density + long
# queue scenarios (e.g. density=10%, queue=40min) can never reach MEDIUM
# because the density component dominates and the queue weight alone is
# insufficient.  A 30-minute queue is operationally significant at any venue.
_QUEUE_MEDIUM_FLOOR_MINUTES: int = 30

# ---------------------------------------------------------------------------
# Congestion prediction thresholds
# ---------------------------------------------------------------------------

# Density must exceed this value before near-term worsening is predicted.
_CONGESTION_PREDICTION_DENSITY_THRESHOLD: float = 60.0

# ---------------------------------------------------------------------------
# Route recommendation thresholds
# ---------------------------------------------------------------------------

# Either condition alone is sufficient to trigger a reroute recommendation.
_REROUTE_DENSITY_THRESHOLD: float = 60.0
_REROUTE_QUEUE_THRESHOLD_MINUTES: int = 15

# ---------------------------------------------------------------------------
# Wait reduction estimation
# ---------------------------------------------------------------------------

# Fraction of current queue time saved by moving to an alternate route,
# per risk level.  Low risk → no benefit from rerouting.
_WAIT_REDUCTION_RATIOS: dict[RiskLevel, float] = {
    RiskLevel.LOW: 0.0,
    RiskLevel.MEDIUM: 0.35,
    RiskLevel.HIGH: 0.55,
}

# Base fraction of queue time saved when rerouting to an alternate gate.
_REROUTE_BASE_REDUCTION_RATIO: float = 0.45

# Additional minutes saved during high-movement phases when rerouting is
# especially effective (halftime / post-match mass exits).
_REROUTE_PHASE_BONUS_MINUTES: int = 5

# Phases where simultaneous mass movement amplifies rerouting benefit.
_HIGH_MOVEMENT_PHASES: frozenset[EventPhase] = frozenset(
    {EventPhase.HALFTIME, EventPhase.POST_MATCH}
)

# ---------------------------------------------------------------------------
# Confidence score parameters
# ---------------------------------------------------------------------------

# Base confidence when a single factor supports the risk classification.
_CONFIDENCE_BASE: float = 50.0

# Each additional corroborating factor adds this many points (up to the cap).
_CONFIDENCE_PER_FACTOR: float = 10.0

# Maximum achievable confidence score.
_CONFIDENCE_MAX: float = 95.0

# Penalty applied when the severity score sits near a classification boundary,
# where the risk level assignment is less certain.
_CONFIDENCE_BOUNDARY_PENALTY: float = 10.0

# Distance from a threshold within which a score is considered "near boundary".
_CONFIDENCE_BOUNDARY_MARGIN: float = 5.0

# Maximum realistic wait reduction to report.  Values above this are
# operationally meaningless (e.g. queue=480min → 168min reduction).
_MAX_WAIT_REDUCTION_MINUTES: int = 60

# ---------------------------------------------------------------------------
# Weather impact descriptions (module-level constant — never rebuilt per call)
# ---------------------------------------------------------------------------

_WEATHER_IMPACT_DESCRIPTIONS: dict[WeatherCondition, str] = {
    WeatherCondition.RAINY: "Rain concentrating crowds under covered areas",
    WeatherCondition.HOT: "Heat driving crowds toward shade and water points",
    WeatherCondition.WINDY: "Wind conditions slowing pedestrian movement",
}


# ---------------------------------------------------------------------------
# Internal helpers — scoring
# ---------------------------------------------------------------------------


def _compute_severity_score(
    crowd_density: float,
    queue_time_minutes: int,
    event_phase: EventPhase,
    weather: WeatherCondition,
    special_event: bool,
) -> float:
    """Compute a weighted severity score in the range [0, 100].

    Combines a density component, a queue-time component, and additive
    contextual bumps for event phase, weather, and special events.

    Args:
        crowd_density: Crowd density percentage (0–100).
        queue_time_minutes: Current queue wait time in minutes.
        event_phase: Current phase of the sporting event.
        weather: Current weather condition.
        special_event: Whether a crowd-amplifying moment is active.

    Returns:
        Severity score clamped to [0.0, 100.0], rounded to 2 decimal places.
    """
    density_component = crowd_density * _DENSITY_WEIGHT

    queue_normalised = normalize_to_percentage(queue_time_minutes, _QUEUE_CAP_MINUTES)
    queue_component = queue_normalised * _QUEUE_WEIGHT

    phase_bump = _PHASE_BUMPS.get(event_phase, 0.0)
    weather_bump = _WEATHER_BUMPS.get(weather, 0.0)
    special_bump = _SPECIAL_EVENT_BUMP if special_event else 0.0

    raw_score = density_component + queue_component + phase_bump + weather_bump + special_bump
    return round_score(clamp(raw_score, 0.0, 100.0))


def _classify_risk(severity_score: float, queue_time_minutes: int) -> RiskLevel:
    """Map a severity score to a RiskLevel enum value.

    A queue floor override ensures that a very long queue (>= 30 min) always
    produces at least MEDIUM risk, even when crowd density is low and the
    weighted severity score alone would not reach the MEDIUM threshold.

    Args:
        severity_score: Score in [0, 100].
        queue_time_minutes: Current queue wait time.  Required — callers must
            always pass the actual queue time so the floor check is never
            silently skipped.

    Returns:
        RiskLevel.HIGH   if score >= 65
        RiskLevel.MEDIUM if score >= 35, or if queue >= _QUEUE_MEDIUM_FLOOR_MINUTES
        RiskLevel.LOW    otherwise
    """
    if severity_score >= _HIGH_RISK_THRESHOLD:
        return RiskLevel.HIGH
    if severity_score >= _MEDIUM_RISK_THRESHOLD:
        return RiskLevel.MEDIUM
    if queue_time_minutes >= _QUEUE_MEDIUM_FLOOR_MINUTES:
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def _compute_confidence_score(
    contributing_factors: list[str],
    severity_score: float,
) -> float:
    """Estimate confidence in the risk classification (0–100).

    Confidence increases when multiple independent factors align with the
    assigned risk level and decreases when the severity score sits near a
    classification boundary, where the assignment is less certain.

    Args:
        contributing_factors: Factors that elevated the score.
        severity_score: The computed severity score.

    Returns:
        Confidence score clamped to [0.0, _CONFIDENCE_MAX].
    """
    raw_confidence = _CONFIDENCE_BASE + (len(contributing_factors) * _CONFIDENCE_PER_FACTOR)

    near_high_boundary = abs(severity_score - _HIGH_RISK_THRESHOLD) <= _CONFIDENCE_BOUNDARY_MARGIN
    near_medium_boundary = abs(severity_score - _MEDIUM_RISK_THRESHOLD) <= _CONFIDENCE_BOUNDARY_MARGIN
    if near_high_boundary or near_medium_boundary:
        raw_confidence -= _CONFIDENCE_BOUNDARY_PENALTY

    return round_score(clamp(raw_confidence, 0.0, _CONFIDENCE_MAX))


def _predict_congestion(
    crowd_density: float,
    event_phase: EventPhase,
    special_event: bool,
    queue_time_minutes: int,
) -> bool:
    """Return True if crowd conditions are likely to worsen in the near term.

    Two independent triggers:
    1. Density-based: density is already elevated AND the event phase or a
       special event is known to drive further crowd movement.
    2. Queue-floor-based: the queue is long enough to have triggered the MEDIUM
       floor (>= _QUEUE_MEDIUM_FLOOR_MINUTES), which itself signals that
       conditions are already operationally significant and likely to worsen.

    Args:
        crowd_density: Current crowd density percentage.
        event_phase: Current phase of the sporting event.
        special_event: Whether a crowd-amplifying moment is active.
        queue_time_minutes: Current queue wait time.  Required — callers must
            always pass the actual queue time so the floor check is never
            silently skipped.

    Returns:
        True if near-term congestion worsening is predicted.
    """
    density_elevated = crowd_density >= _CONGESTION_PREDICTION_DENSITY_THRESHOLD
    movement_trigger = event_phase in _HIGH_MOVEMENT_PHASES or special_event
    density_based = density_elevated and movement_trigger

    # A queue at or above the MEDIUM floor is itself a worsening signal:
    # it indicates a bottleneck that will grow if not addressed.
    queue_floor_based = queue_time_minutes >= _QUEUE_MEDIUM_FLOOR_MINUTES

    return density_based or queue_floor_based


# ---------------------------------------------------------------------------
# Internal helpers — factor and recommendation text
# ---------------------------------------------------------------------------


def _build_contributing_factors(
    crowd_density: float,
    queue_time_minutes: int,
    event_phase: EventPhase,
    weather: WeatherCondition,
    special_event: bool,
) -> list[str]:
    """Produce an ordered, human-readable list of factors that elevated the risk score.

    Factors are listed from most to least impactful to aid operator triage.

    Args:
        crowd_density: Current crowd density percentage.
        queue_time_minutes: Current queue wait time in minutes.
        event_phase: Current phase of the sporting event.
        weather: Current weather condition.
        special_event: Whether a crowd-amplifying moment is active.

    Returns:
        Non-empty list of factor description strings.
    """
    factors: list[str] = []

    # Density — highest weight, listed first.
    if crowd_density >= 75.0:
        factors.append(f"Very high crowd density at {crowd_density:.1f}% capacity")
    elif crowd_density >= 50.0:
        factors.append(f"Elevated crowd density at {crowd_density:.1f}% capacity")

    # Queue time.
    if queue_time_minutes >= 20:
        factors.append(f"Long queue wait of {queue_time_minutes} minutes")
    elif queue_time_minutes >= 10:
        factors.append(f"Moderate queue wait of {queue_time_minutes} minutes")

    # Event phase — only flag phases that actively drive crowd movement.
    if event_phase in _HIGH_MOVEMENT_PHASES:
        phase_label = event_phase.value.replace("_", " ").title()
        factors.append(f"{phase_label} phase driving simultaneous crowd movement")

    # Weather — only flag non-clear conditions.
    if weather != WeatherCondition.CLEAR:
        factors.append(
            _WEATHER_IMPACT_DESCRIPTIONS.get(weather, f"Adverse weather ({weather.value})")
        )

    # Special event — listed last as it is transient.
    if special_event:
        factors.append("Active crowd-amplifying event causing sudden density surge")

    return factors if factors else ["All conditions within normal operating parameters"]


def _build_crowd_recommendation(
    risk_level: RiskLevel,
    zone: str,
    predicted_congestion: bool,
    factor_count: int = 0,
    queue_time_minutes: int = 0,
) -> str:
    """Generate a clear, actionable recommendation based on risk level and prediction.

    For LOW risk, a monitoring advisory is issued whenever:
    - two or more contributing factors are present, OR
    - a queue factor is present (queue >= 10 min), since listing a queue factor
      while simultaneously saying "no action required" is a direct contradiction.

    Args:
        risk_level: Classified risk level for the zone.
        zone: Name of the zone being analyzed.
        predicted_congestion: Whether conditions are expected to worsen.
        factor_count: Number of contributing factors (used to tune LOW wording).
        queue_time_minutes: Current queue wait time; triggers monitoring advisory
            at LOW risk when >= 10 min to avoid contradicting the queue factor.

    Returns:
        Human-readable recommendation string for venue staff or attendees.
    """
    congestion_suffix = (
        " Conditions are predicted to worsen shortly." if predicted_congestion else ""
    )

    if risk_level == RiskLevel.HIGH:
        return (
            f"High congestion risk at {zone}. "
            f"Activate crowd management protocols and redirect attendees immediately."
            f"{congestion_suffix}"
        )

    if risk_level == RiskLevel.MEDIUM:
        return (
            f"Moderate congestion detected at {zone}. "
            f"Consider proactively directing attendees toward nearby alternatives."
            f"{congestion_suffix}"
        )

    # LOW risk — issue a monitoring advisory when any queue factor is present
    # (queue >= 10 min) or when multiple factors are present.  This prevents
    # the contradiction of listing "Long/Moderate queue wait" as a factor while
    # simultaneously saying "No immediate action required."
    queue_factor_present = queue_time_minutes >= 10
    if factor_count >= 2 or queue_factor_present:
        return (
            f"{zone} conditions are currently low risk but elevated queue times or "
            f"multiple factors are present. "
            f"Monitor the situation and be prepared to redirect attendees if conditions worsen."
            f"{congestion_suffix}"
        )

    return (
        f"{zone} is operating within normal parameters. "
        f"No immediate action required.{congestion_suffix}"
    )


def _estimate_crowd_wait_reduction(risk_level: RiskLevel, queue_time_minutes: int) -> int:
    """Estimate wait time reduction (minutes) if attendees move to an alternate zone.

    Reduction is proportional to current queue time and risk severity.
    Low-risk zones yield no benefit from rerouting.  The result is capped at
    _MAX_WAIT_REDUCTION_MINUTES to avoid operationally meaningless values at
    extreme queue lengths (e.g. queue=480min → uncapped 168min reduction).

    Args:
        risk_level: Classified risk level.
        queue_time_minutes: Current queue wait time in minutes.

    Returns:
        Non-negative integer representing estimated minutes saved, capped at
        _MAX_WAIT_REDUCTION_MINUTES.
    """
    ratio = _WAIT_REDUCTION_RATIOS.get(risk_level, 0.0)
    raw = round(queue_time_minutes * ratio)
    return min(raw, _MAX_WAIT_REDUCTION_MINUTES)


def _build_route_congestion_reason(
    current_gate: str,
    crowd_density: float,
    queue_time_minutes: int,
    density_exceeds: bool,
    queue_exceeds: bool,
    recommended_gate: str,
) -> str:
    """Build a factually accurate reason string for a route recommendation.

    The reason text only mentions the condition(s) that actually triggered the
    rerouting recommendation, avoiding the misleading pattern of always citing
    both density and queue time even when only one exceeded its threshold.

    Args:
        current_gate: The congested gate name.
        crowd_density: Current crowd density percentage.
        queue_time_minutes: Current queue wait time in minutes.
        density_exceeds: Whether density alone triggered the recommendation.
        queue_exceeds: Whether queue time alone triggered the recommendation.
        recommended_gate: The suggested alternate gate.

    Returns:
        Human-readable reason string describing the actual trigger conditions.
    """
    if density_exceeds and queue_exceeds:
        trigger = (
            f"a crowd density of {crowd_density:.0f}% and "
            f"a queue time of {queue_time_minutes} min, both exceeding safe thresholds"
        )
    elif density_exceeds:
        trigger = (
            f"a crowd density of {crowd_density:.0f}%, exceeding the density threshold"
        )
    else:
        trigger = (
            f"a queue time of {queue_time_minutes} min, exceeding the queue threshold"
        )

    return (
        f"{current_gate} has {trigger}. "
        f"{recommended_gate} is the nearest viable alternative."
    )


def _estimate_route_wait_reduction(
    queue_time_minutes: int,
    event_phase: EventPhase,
) -> int:
    """Estimate wait time reduction (minutes) when rerouting to an alternate gate.

    Applies a base reduction ratio and adds a phase bonus during high-movement
    phases where rerouting is most effective.  The result is capped at
    _MAX_WAIT_REDUCTION_MINUTES — the same ceiling used by crowd analysis —
    to prevent operationally absurd values at extreme queue lengths
    (e.g. queue=480min, post_match → uncapped 221min reduction).

    Args:
        queue_time_minutes: Current queue wait time at the congested gate.
        event_phase: Current phase of the sporting event.

    Returns:
        Non-negative integer representing estimated minutes saved, capped at
        _MAX_WAIT_REDUCTION_MINUTES.
    """
    base_reduction = round(queue_time_minutes * _REROUTE_BASE_REDUCTION_RATIO)
    phase_bonus = _REROUTE_PHASE_BONUS_MINUTES if event_phase in _HIGH_MOVEMENT_PHASES else 0
    return min(base_reduction + phase_bonus, _MAX_WAIT_REDUCTION_MINUTES)


def _compute_route_confidence(
    crowd_density: float,
    queue_time_minutes: int,
    has_alternatives: bool,
    route_status: RouteStatus,
) -> float:
    """Estimate confidence in the route recommendation (0–100).

    Confidence is higher when both density and queue time clearly exceed
    thresholds and when viable alternatives are available.

    Args:
        crowd_density: Current crowd density percentage.
        queue_time_minutes: Current queue wait time in minutes.
        has_alternatives: Whether nearby alternatives were provided.
        route_status: The determined route status.

    Returns:
        Confidence score clamped to [0.0, _CONFIDENCE_MAX].
    """
    if route_status == RouteStatus.CLEAR:
        return round_score(clamp(80.0, 0.0, _CONFIDENCE_MAX))

    if route_status == RouteStatus.NO_BETTER_OPTION:
        return round_score(clamp(55.0, 0.0, _CONFIDENCE_MAX))

    # RECOMMENDED: score based on how far conditions exceed thresholds.
    score: float = _CONFIDENCE_BASE

    density_margin = crowd_density - _REROUTE_DENSITY_THRESHOLD
    queue_margin = queue_time_minutes - _REROUTE_QUEUE_THRESHOLD_MINUTES

    if density_margin >= 20.0:
        score += 20.0
    elif density_margin >= 5.0:
        score += 10.0

    if queue_margin >= 15:
        score += 15.0
    elif queue_margin >= 5:
        score += 7.0

    if has_alternatives:
        score += 10.0

    return round_score(clamp(score, 0.0, _CONFIDENCE_MAX))


# ---------------------------------------------------------------------------
# Public service functions
# ---------------------------------------------------------------------------


def analyze_crowd_conditions(request: CrowdAnalysisRequest) -> CrowdAnalysisResponse:
    """Analyze crowd conditions at a venue zone and return a structured risk assessment.

    Computes a weighted severity score from crowd density, queue time, event
    phase, weather, and special event status. Maps the score to a risk level,
    predicts near-term congestion, and generates actionable recommendations.

    Args:
        request: Validated crowd analysis input payload.

    Returns:
        CrowdAnalysisResponse containing risk level, congestion prediction,
        recommendation, severity score, confidence score, contributing factors,
        and a UTC analysis timestamp.
    """
    severity_score = _compute_severity_score(
        crowd_density=request.crowd_density,
        queue_time_minutes=request.queue_time_minutes,
        event_phase=request.event_phase,
        weather=request.weather,
        special_event=request.special_event,
    )

    risk_level = _classify_risk(severity_score, request.queue_time_minutes)

    predicted_congestion = _predict_congestion(
        crowd_density=request.crowd_density,
        event_phase=request.event_phase,
        special_event=request.special_event,
        queue_time_minutes=request.queue_time_minutes,
    )

    factors = _build_contributing_factors(
        crowd_density=request.crowd_density,
        queue_time_minutes=request.queue_time_minutes,
        event_phase=request.event_phase,
        weather=request.weather,
        special_event=request.special_event,
    )

    confidence_score = _compute_confidence_score(
        contributing_factors=factors,
        severity_score=severity_score,
    )

    recommendation = _build_crowd_recommendation(
        risk_level=risk_level,
        zone=request.zone,
        predicted_congestion=predicted_congestion,
        factor_count=len(factors),
        queue_time_minutes=request.queue_time_minutes,
    )

    wait_reduction = _estimate_crowd_wait_reduction(
        risk_level=risk_level,
        queue_time_minutes=request.queue_time_minutes,
    )

    # Generate AI-powered operational insight using Google Gemini
    ai_insight, ai_powered = generate_crowd_analysis_insight(
        zone=request.zone,
        risk_level=risk_level,
        severity_score=severity_score,
        confidence_score=confidence_score,
        predicted_congestion=predicted_congestion,
        contributing_factors=factors,
        recommendation=recommendation,
    )

    return CrowdAnalysisResponse(
        zone=request.zone,
        risk_level=risk_level,
        predicted_congestion=predicted_congestion,
        recommendation=recommendation,
        estimated_wait_reduction_minutes=wait_reduction,
        severity_score=severity_score,
        confidence_score=confidence_score,
        contributing_factors=format_factors(factors),
        ai_insight=ai_insight,
        ai_powered=ai_powered,
        analyzed_at=utc_now(),
    )


def recommend_alternate_route(request: RouteRecommendationRequest) -> RouteRecommendationResponse:
    """Recommend an alternate gate or zone when current conditions are congested.

    Evaluates whether the current gate warrants redirection based on crowd
    density and queue time. Selects the first entry from nearby_alternatives
    (venue operators are expected to supply pre-ranked, viable options).
    Degrades gracefully when no alternatives are available or conditions
    are within acceptable limits.

    Args:
        request: Validated route recommendation input payload.

    Returns:
        RouteRecommendationResponse with alternate gate, route status,
        recommendation text, estimated wait reduction, confidence score,
        reason, and a UTC analysis timestamp.
    """
    density_exceeds = request.crowd_density >= _REROUTE_DENSITY_THRESHOLD
    queue_exceeds = request.queue_time_minutes >= _REROUTE_QUEUE_THRESHOLD_MINUTES
    is_congested = density_exceeds or queue_exceeds

    has_alternatives = bool(request.nearby_alternatives)

    if not is_congested:
        confidence = _compute_route_confidence(
            crowd_density=request.crowd_density,
            queue_time_minutes=request.queue_time_minutes,
            has_alternatives=has_alternatives,
            route_status=RouteStatus.CLEAR,
        )

        # Generate AI-powered routing insight using Google Gemini
        ai_insight, ai_powered = generate_route_recommendation_insight(
            current_gate=request.current_gate,
            alternate_gate=None,
            route_status=RouteStatus.CLEAR,
            estimated_wait_reduction=0,
            confidence_score=confidence,
            reason=(
                f"Crowd density ({request.crowd_density:.0f}%) and queue time "
                f"({request.queue_time_minutes} min) are both below rerouting thresholds."
            ),
        )

        return RouteRecommendationResponse(
            current_gate=request.current_gate,
            alternate_gate=None,
            alternate_zone=None,
            route_status=RouteStatus.CLEAR,
            recommendation=(
                f"{request.current_gate} is operating within acceptable limits. "
                "Continue on your current route."
            ),
            estimated_wait_reduction_minutes=0,
            confidence_score=confidence,
            reason=(
                f"Crowd density ({request.crowd_density:.0f}%) and queue time "
                f"({request.queue_time_minutes} min) are both below rerouting thresholds."
            ),
            ai_insight=ai_insight,
            ai_powered=ai_powered,
            analyzed_at=utc_now(),
        )

    if not has_alternatives:
        confidence = _compute_route_confidence(
            crowd_density=request.crowd_density,
            queue_time_minutes=request.queue_time_minutes,
            has_alternatives=False,
            route_status=RouteStatus.NO_BETTER_OPTION,
        )

        # Generate AI-powered routing insight using Google Gemini
        ai_insight, ai_powered = generate_route_recommendation_insight(
            current_gate=request.current_gate,
            alternate_gate=None,
            route_status=RouteStatus.NO_BETTER_OPTION,
            estimated_wait_reduction=0,
            confidence_score=confidence,
            reason=(
                f"{request.current_gate} is congested "
                f"(density {request.crowd_density:.0f}%, "
                f"queue {request.queue_time_minutes} min) "
                "but no nearby alternatives were provided."
            ),
        )

        return RouteRecommendationResponse(
            current_gate=request.current_gate,
            alternate_gate=None,
            alternate_zone=None,
            route_status=RouteStatus.NO_BETTER_OPTION,
            recommendation=(
                f"No alternate routes are available from {request.current_gate}. "
                "Proceed with caution and allow additional time."
            ),
            estimated_wait_reduction_minutes=0,
            confidence_score=confidence,
            reason=(
                f"{request.current_gate} is congested "
                f"(density {request.crowd_density:.0f}%, "
                f"queue {request.queue_time_minutes} min) "
                "but no nearby alternatives were provided."
            ),
            ai_insight=ai_insight,
            ai_powered=ai_powered,
            analyzed_at=utc_now(),
        )

    # Narrow the type: has_alternatives=True guarantees the list is non-empty.
    # Use an explicit check rather than assert so this is safe under -O flag.
    if request.nearby_alternatives is None:
        # This branch is unreachable because has_alternatives=True above,
        # but the explicit guard satisfies the type checker and is safe
        # in all Python execution modes including optimized builds.
        raise RuntimeError("nearby_alternatives is None despite has_alternatives=True")  # pragma: no cover
    recommended_gate = request.nearby_alternatives[0]

    wait_reduction = _estimate_route_wait_reduction(
        queue_time_minutes=request.queue_time_minutes,
        event_phase=request.event_phase,
    )

    confidence = _compute_route_confidence(
        crowd_density=request.crowd_density,
        queue_time_minutes=request.queue_time_minutes,
        has_alternatives=True,
        route_status=RouteStatus.RECOMMENDED,
    )

    destination_clause = (
        f" en route to {request.destination_zone}" if request.destination_zone else ""
    )

    # Generate AI-powered routing insight using Google Gemini
    ai_insight, ai_powered = generate_route_recommendation_insight(
        current_gate=request.current_gate,
        alternate_gate=recommended_gate,
        route_status=RouteStatus.RECOMMENDED,
        estimated_wait_reduction=wait_reduction,
        confidence_score=confidence,
        reason=_build_route_congestion_reason(
            current_gate=request.current_gate,
            crowd_density=request.crowd_density,
            queue_time_minutes=request.queue_time_minutes,
            density_exceeds=density_exceeds,
            queue_exceeds=queue_exceeds,
            recommended_gate=recommended_gate,
        ),
    )

    return RouteRecommendationResponse(
        current_gate=request.current_gate,
        alternate_gate=recommended_gate,
        alternate_zone=request.destination_zone,
        route_status=RouteStatus.RECOMMENDED,
        recommendation=(
            f"Use {recommended_gate} instead of {request.current_gate}{destination_clause}. "
            f"Estimated time saving: {wait_reduction} minute(s)."
        ),
        estimated_wait_reduction_minutes=wait_reduction,
        confidence_score=confidence,
        reason=_build_route_congestion_reason(
            current_gate=request.current_gate,
            crowd_density=request.crowd_density,
            queue_time_minutes=request.queue_time_minutes,
            density_exceeds=density_exceeds,
            queue_exceeds=queue_exceeds,
            recommended_gate=recommended_gate,
        ),
        ai_insight=ai_insight,
        ai_powered=ai_powered,
        analyzed_at=utc_now(),
    )
