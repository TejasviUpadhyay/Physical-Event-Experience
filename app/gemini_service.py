"""
Google Gemini AI integration for SmartFlow AI.

Provides AI-powered operational insights that enhance the deterministic
scoring system with natural language explanations and tactical guidance.

Uses the NEW google-genai SDK (not the deprecated google-generativeai).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from app.config import get_settings
from app.models import RiskLevel, RouteStatus

logger = logging.getLogger(__name__)

# Lazy import to avoid startup failure if google-genai is not installed
_gemini_available = False
_genai_client = None

try:
    from google import genai
    from google.genai import types
    _gemini_available = True
    logger.info("✓ Google Gemini SDK (google-genai) loaded successfully")
except ImportError as e:
    logger.warning(f"google-genai not installed: {e}. AI insights will use fallback mode.")


def _get_gemini_client():
    """Get or create the Gemini client instance.
    
    Returns:
        Configured Gemini client or None if unavailable.
    """
    global _genai_client
    
    if not _gemini_available:
        logger.warning("Gemini SDK not available")
        return None
    
    settings = get_settings()
    if not settings.gemini_api_key:
        logger.warning("GEMINI_API_KEY not configured. AI insights will use fallback mode.")
        return None
    
    # Create client if not exists or if API key changed
    if _genai_client is None:
        try:
            _genai_client = genai.Client(api_key=settings.gemini_api_key)
            logger.info("✓ Gemini client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to create Gemini client: {type(e).__name__}: {e}")
            return None
    
    return _genai_client


def generate_crowd_analysis_insight(
    zone: str,
    risk_level: RiskLevel,
    severity_score: float,
    confidence_score: float,
    predicted_congestion: bool,
    contributing_factors: list[str],
    recommendation: str,
) -> tuple[str, bool]:
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
        Tuple of (insight_text, was_ai_powered).
        - insight_text: AI-generated or fallback insight string
        - was_ai_powered: True if Gemini was used, False if fallback
    """
    client = _get_gemini_client()
    
    if client is None:
        logger.info(f"Using fallback insight for {zone} - Gemini client not available")
        return (_fallback_crowd_insight(zone, risk_level, predicted_congestion), False)

    try:
        # Enhanced prompt for more distinctive AI output
        prompt = f"""You are an expert crowd safety AI assistant for SmartFlow AI, analyzing real-time conditions at a major sporting venue.

**Current Situation:**
- Location: {zone}
- Risk Level: {risk_level.value.upper()}
- Severity Score: {severity_score:.1f}/100
- Confidence: {confidence_score:.1f}%
- Congestion Forecast: {"WORSENING" if predicted_congestion else "STABLE"}

**Key Factors:**
{chr(10).join(f"• {factor}" for factor in contributing_factors)}

**System Assessment:** {recommendation}

**Your Task:**
Provide a 2-3 sentence operational insight for venue operators that:
1. Explains the current situation in clear, professional language
2. Highlights the most critical operational concern
3. Suggests specific tactical actions (e.g., deploy staff, redirect crowds, activate protocols)

Be direct, actionable, and specific. Use natural language, not templates. Focus on what operators should DO right now."""

        # Use the new SDK's generate_content method
        response = client.models.generate_content(
            model='gemini-1.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,  # Higher for more variation
                max_output_tokens=250,
                top_p=0.95,
                top_k=40,
            )
        )

        if response and response.text:
            insight = response.text.strip()
            logger.info(f"✓ Gemini AI generated insight for {zone} ({risk_level.value}) - {len(insight)} chars")
            return (insight, True)

        logger.warning(f"Gemini returned empty response for {zone}")
        return (_fallback_crowd_insight(zone, risk_level, predicted_congestion), False)

    except Exception as e:
        logger.error(f"Gemini API call failed for {zone}: {type(e).__name__}: {e}")
        return (_fallback_crowd_insight(zone, risk_level, predicted_congestion), False)


def generate_route_recommendation_insight(
    current_gate: str,
    alternate_gate: Optional[str],
    route_status: RouteStatus,
    estimated_wait_reduction: int,
    confidence_score: float,
    reason: str,
) -> tuple[str, bool]:
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
        Tuple of (insight_text, was_ai_powered).
        - insight_text: AI-generated or fallback insight string
        - was_ai_powered: True if Gemini was used, False if fallback
    """
    client = _get_gemini_client()
    
    if client is None:
        logger.info(f"Using fallback route insight for {current_gate} - Gemini client not available")
        return (_fallback_route_insight(route_status, alternate_gate, estimated_wait_reduction), False)

    try:
        # Enhanced prompt for more distinctive AI output
        prompt = f"""You are an expert crowd navigation AI assistant for SmartFlow AI, helping attendees at a major sporting venue.

**Current Situation:**
- Current Location: {current_gate}
- Recommended Alternative: {alternate_gate or "None available"}
- Route Status: {route_status.value.upper().replace('_', ' ')}
- Potential Time Savings: {estimated_wait_reduction} minutes
- Confidence: {confidence_score:.1f}%

**Analysis:** {reason}

**Your Task:**
Provide a 2-3 sentence routing insight for attendees that:
1. Explains the routing decision in friendly, clear language
2. Highlights the key benefit or reason for the recommendation
3. Gives specific next steps (e.g., "Head to Gate B now", "Stay on current path", "Allow extra time")

Be helpful, conversational, and specific. Use natural language that sounds like a knowledgeable venue guide, not a robot."""

        # Use the new SDK's generate_content method
        response = client.models.generate_content(
            model='gemini-2.0-flash-exp',
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.7,  # Higher for more variation
                max_output_tokens=250,
                top_p=0.95,
                top_k=40,
            )
        )

        if response and response.text:
            insight = response.text.strip()
            logger.info(f"✓ Gemini AI generated route insight for {current_gate} ({route_status.value}) - {len(insight)} chars")
            return (insight, True)

        logger.warning(f"Gemini returned empty response for {current_gate}")
        return (_fallback_route_insight(route_status, alternate_gate, estimated_wait_reduction), False)

    except Exception as e:
        logger.error(f"Gemini API call failed for {current_gate}: {type(e).__name__}: {e}")
        return (_fallback_route_insight(route_status, alternate_gate, estimated_wait_reduction), False)


def _fallback_crowd_insight(zone: str, risk_level: RiskLevel, predicted_congestion: bool) -> str:
    """Generate deterministic fallback insight for crowd analysis.

    Used when Gemini is unavailable or fails.

    Args:
        zone: Name of the analyzed zone.
        risk_level: Classified risk level.
        predicted_congestion: Whether congestion is predicted.

    Returns:
        Deterministic insight string with clear fallback indicator.
    """
    if risk_level == RiskLevel.HIGH:
        base = f"{zone} is experiencing high congestion risk requiring immediate intervention."
    elif risk_level == RiskLevel.MEDIUM:
        base = f"{zone} shows moderate congestion that warrants proactive monitoring."
    else:
        base = f"{zone} is operating normally with low congestion risk."

    if predicted_congestion:
        return f"[Deterministic Analysis] {base} Conditions are expected to worsen, so early action is recommended."

    return f"[Deterministic Analysis] {base} Continue standard operations and monitor for changes."


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
        Deterministic insight string with clear fallback indicator.
    """
    if route_status == RouteStatus.RECOMMENDED and alternate_gate:
        return (
            f"[Deterministic Analysis] Rerouting to {alternate_gate} is recommended to save approximately "
            f"{estimated_wait_reduction} minutes and avoid congestion."
        )
    elif route_status == RouteStatus.NO_BETTER_OPTION:
        return (
            "[Deterministic Analysis] No alternate routes are currently available. "
            "Proceed with your current route and allow extra time."
        )
    else:
        return "[Deterministic Analysis] Your current route is clear. Continue as planned with normal wait times expected."


def test_gemini_connection() -> dict:
    """Test Gemini API connection with a real API call.
    
    Returns:
        Dictionary with test results including success status and any errors.
    """
    result = {
        "sdk_available": _gemini_available,
        "api_key_configured": False,
        "client_created": False,
        "api_call_successful": False,
        "response_received": False,
        "error": None,
    }
    
    settings = get_settings()
    result["api_key_configured"] = bool(settings.gemini_api_key)
    
    if not _gemini_available:
        result["error"] = "google-genai SDK not installed"
        return result
    
    if not settings.gemini_api_key:
        result["error"] = "GEMINI_API_KEY environment variable not set"
        return result
    
    try:
        client = _get_gemini_client()
        if client is None:
            result["error"] = "Failed to create Gemini client"
            return result
        
        result["client_created"] = True
        
        # Perform actual API call test
        from google.genai import types
        response = client.models.generate_content(
            model='gemini-2.0-flash-exp',
            contents="Say 'Hello from SmartFlow AI' in exactly 5 words.",
            config=types.GenerateContentConfig(
                temperature=0.3,
                max_output_tokens=50,
            )
        )
        
        result["api_call_successful"] = True
        
        if response and response.text:
            result["response_received"] = True
            result["sample_response"] = response.text.strip()[:100]
        
    except Exception as e:
        result["error"] = f"{type(e).__name__}: {str(e)}"
        logger.error(f"Gemini connection test failed: {result['error']}")
    
    return result
