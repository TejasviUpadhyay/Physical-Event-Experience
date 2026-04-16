"""
Google Gemini AI integration for SmartFlow AI.

Provides AI-powered operational insights that enhance the deterministic
scoring system with natural language explanations and tactical guidance.

The integration is fail-safe: if Gemini is unavailable or the API key is
missing, the system falls back to deterministic explanations without
degrading functionality.
"""

from __future__ import annotations

import logging
from typing import Optional

from app.config import get_settings
from app.models import RiskLevel, RouteStatus

logger = logging.getLogger(__name__)

# Lazy import to avoid startup failure if google-generativeai is not installed
_gemini_available = False
_genai = None

try:
    import google.generativeai as genai
    _genai = genai
    _gemini_available = True
except ImportError:
    logger.warning("google-generativeai not installed. AI insights will use fallback mode.")


def _initialize_gemini() -> bool:
    """Initialize Gemini API with the configured API key.

    Returns:
        True if initialization succeeded, False otherwise.
    """
    if not _gemini_available:
        return False

    settings = get_settings()
    if not settings.gemini_api_key:
        logger.info("GEMINI_API_KEY not configured. AI insights will use fallback mode.")
        return False

    try:
        _genai.configure(api_key=settings.gemini_api_key)
        return True
    except Exception as e:
        logger.warning(f"Failed to initialize Gemini API: {e}. Using fallback mode.")
        return False


def generate_crowd_analysis_insight(
    zone: str,
    risk_level: RiskLevel,
    severity_score: float,
    confidence_score: float,
    predicted_congestion: bool,
    contributing_factors: list[str],
    recommendation: str,
) -> str:
    """Generate AI-powered operational insight for crowd analysis.

    Uses Google Gemini to produce a natural language explanation that
    synthesizes the deterministic analysis into actionable guidance for
    venue operators.

    Args:
        zone: Name of the analyzed zone or gate.
        risk_level: Classified risk level (LOW, MEDIUM, HIGH).
        severity_score: Computed severity score (0-100).
        confidence_score: Confidence in the classification (0-100).
        predicted_congestion: Whether congestion is expected to worsen.
        contributing_factors: List of factors that elevated the score.
        recommendation: Deterministic recommendation text.

    Returns:
        AI-generated insight string. Falls back to deterministic explanation
        if Gemini is unavailable.
    """
    if not _initialize_gemini():
        return _fallback_crowd_insight(zone, risk_level, predicted_congestion)

    try:
        model = _genai.GenerativeModel("gemini-1.5-flash")

        prompt = f"""You are an AI assistant for SmartFlow AI, a crowd intelligence system for sporting venues.

Analyze this crowd situation and provide a concise operational insight (2-3 sentences max):

Zone: {zone}
Risk Level: {risk_level.value.upper()}
Severity Score: {severity_score:.1f}/100
Confidence: {confidence_score:.1f}%
Congestion Predicted: {"Yes" if predicted_congestion else "No"}
Contributing Factors:
{chr(10).join(f"- {factor}" for factor in contributing_factors)}

System Recommendation: {recommendation}

Provide a clear, actionable insight for venue operators. Focus on:
1. What's happening right now
2. Why it matters operationally
3. What action to take (if any)

Keep it professional, concise, and tactical. Do not repeat the recommendation verbatim."""

        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,  # Low temperature for consistent, factual output
                "max_output_tokens": 150,
            },
        )

        if response and response.text:
            insight = response.text.strip()
            logger.info(f"Generated Gemini insight for {zone} ({risk_level.value})")
            return insight

        return _fallback_crowd_insight(zone, risk_level, predicted_congestion)

    except Exception as e:
        logger.warning(f"Gemini API call failed: {e}. Using fallback.")
        return _fallback_crowd_insight(zone, risk_level, predicted_congestion)


def generate_route_recommendation_insight(
    current_gate: str,
    alternate_gate: Optional[str],
    route_status: RouteStatus,
    estimated_wait_reduction: int,
    confidence_score: float,
    reason: str,
) -> str:
    """Generate AI-powered operational insight for route recommendation.

    Uses Google Gemini to produce a natural language explanation that
    helps attendees understand the routing decision and take action.

    Args:
        current_gate: The gate the attendee is currently at.
        alternate_gate: Recommended alternate gate (None if no better option).
        route_status: Route advisability status (RECOMMENDED, CLEAR, NO_BETTER_OPTION).
        estimated_wait_reduction: Estimated time savings in minutes.
        confidence_score: Confidence in the recommendation (0-100).
        reason: Deterministic reason text.

    Returns:
        AI-generated insight string. Falls back to deterministic explanation
        if Gemini is unavailable.
    """
    if not _initialize_gemini():
        return _fallback_route_insight(route_status, alternate_gate, estimated_wait_reduction)

    try:
        model = _genai.GenerativeModel("gemini-1.5-flash")

        prompt = f"""You are an AI assistant for SmartFlow AI, a crowd intelligence system for sporting venues.

Analyze this routing situation and provide a concise tactical insight (2-3 sentences max):

Current Gate: {current_gate}
Alternate Gate: {alternate_gate or "None"}
Route Status: {route_status.value.upper()}
Estimated Time Savings: {estimated_wait_reduction} minutes
Confidence: {confidence_score:.1f}%
Reason: {reason}

Provide a clear, actionable insight for attendees. Focus on:
1. What the routing decision means
2. Why it's the right choice
3. What to do next

Keep it friendly, concise, and helpful. Do not repeat the reason verbatim."""

        response = model.generate_content(
            prompt,
            generation_config={
                "temperature": 0.3,
                "max_output_tokens": 150,
            },
        )

        if response and response.text:
            insight = response.text.strip()
            logger.info(f"Generated Gemini route insight for {current_gate} ({route_status.value})")
            return insight

        return _fallback_route_insight(route_status, alternate_gate, estimated_wait_reduction)

    except Exception as e:
        logger.warning(f"Gemini API call failed: {e}. Using fallback.")
        return _fallback_route_insight(route_status, alternate_gate, estimated_wait_reduction)


def _fallback_crowd_insight(zone: str, risk_level: RiskLevel, predicted_congestion: bool) -> str:
    """Generate deterministic fallback insight for crowd analysis.

    Used when Gemini is unavailable or fails.

    Args:
        zone: Name of the analyzed zone.
        risk_level: Classified risk level.
        predicted_congestion: Whether congestion is predicted.

    Returns:
        Deterministic insight string.
    """
    if risk_level == RiskLevel.HIGH:
        base = f"{zone} is experiencing high congestion risk requiring immediate intervention."
    elif risk_level == RiskLevel.MEDIUM:
        base = f"{zone} shows moderate congestion that warrants proactive monitoring."
    else:
        base = f"{zone} is operating normally with low congestion risk."

    if predicted_congestion:
        return f"{base} Conditions are expected to worsen, so early action is recommended."

    return f"{base} Continue standard operations and monitor for changes."


def _fallback_route_insight(
    route_status: RouteStatus,
    alternate_gate: Optional[str],
    estimated_wait_reduction: int,
) -> str:
    """Generate deterministic fallback insight for route recommendation.

    Used when Gemini is unavailable or fails.

    Args:
        route_status: Route advisability status.
        alternate_gate: Recommended alternate gate (None if no better option).
        estimated_wait_reduction: Estimated time savings in minutes.

    Returns:
        Deterministic insight string.
    """
    if route_status == RouteStatus.RECOMMENDED and alternate_gate:
        return (
            f"Rerouting to {alternate_gate} is recommended to save approximately "
            f"{estimated_wait_reduction} minutes and avoid congestion."
        )
    elif route_status == RouteStatus.NO_BETTER_OPTION:
        return (
            "No alternate routes are currently available. "
            "Proceed with your current route and allow extra time."
        )
    else:
        return "Your current route is clear. Continue as planned with normal wait times expected."
