"""
Tests for Google Gemini AI integration.

Tests verify that AI insights are generated correctly and that the system
gracefully falls back to deterministic explanations when Gemini is unavailable.
"""

from unittest.mock import MagicMock, patch

import pytest

from app.gemini_service import (
    generate_crowd_analysis_insight,
    generate_route_recommendation_insight,
)
from app.models import RiskLevel, RouteStatus


class TestCrowdAnalysisInsightGeneration:
    """Test AI insight generation for crowd analysis."""

    @patch("app.gemini_service._initialize_gemini")
    @patch("app.gemini_service._genai")
    def test_gemini_success_generates_ai_insight(self, mock_genai, mock_init):
        """Gemini API success returns AI-generated insight."""
        mock_init.return_value = True

        # Mock Gemini response
        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Gate A shows high risk due to post-match surge and rain. Immediate action required."
        mock_model.generate_content.return_value = mock_response
        mock_genai.GenerativeModel.return_value = mock_model

        insight = generate_crowd_analysis_insight(
            zone="Gate A",
            risk_level=RiskLevel.HIGH,
            severity_score=93.42,
            confidence_score=95.0,
            predicted_congestion=True,
            contributing_factors=[
                "Very high crowd density at 82.0% capacity",
                "Long queue wait of 25 minutes",
            ],
            recommendation="High congestion risk at Gate A. Activate crowd management protocols.",
        )

        assert "Gate A" in insight
        assert "high risk" in insight.lower()
        assert len(insight) > 20  # Non-trivial response

    @patch("app.gemini_service._initialize_gemini")
    def test_gemini_unavailable_uses_fallback(self, mock_init):
        """When Gemini is unavailable, fallback insight is returned."""
        mock_init.return_value = False

        insight = generate_crowd_analysis_insight(
            zone="Gate B",
            risk_level=RiskLevel.MEDIUM,
            severity_score=55.0,
            confidence_score=75.0,
            predicted_congestion=False,
            contributing_factors=["Elevated crowd density at 65.0% capacity"],
            recommendation="Moderate congestion detected at Gate B.",
        )

        assert "Gate B" in insight
        assert "moderate" in insight.lower() or "medium" in insight.lower()
        assert len(insight) > 20

    @patch("app.gemini_service._initialize_gemini")
    def test_low_risk_fallback_insight(self, mock_init):
        """Low risk produces appropriate fallback insight."""
        mock_init.return_value = False

        insight = generate_crowd_analysis_insight(
            zone="Gate C",
            risk_level=RiskLevel.LOW,
            severity_score=15.0,
            confidence_score=60.0,
            predicted_congestion=False,
            contributing_factors=["All conditions within normal operating parameters"],
            recommendation="Gate C is operating within normal parameters.",
        )

        assert "Gate C" in insight
        assert "low" in insight.lower() or "normal" in insight.lower()

    @patch("app.gemini_service._initialize_gemini")
    def test_predicted_congestion_mentioned_in_fallback(self, mock_init):
        """Fallback insight mentions predicted congestion when present."""
        mock_init.return_value = False

        insight = generate_crowd_analysis_insight(
            zone="Gate D",
            risk_level=RiskLevel.MEDIUM,
            severity_score=50.0,
            confidence_score=70.0,
            predicted_congestion=True,
            contributing_factors=["Long queue wait of 35 minutes"],
            recommendation="Moderate congestion detected.",
        )

        assert "worsen" in insight.lower() or "expected" in insight.lower()


class TestRouteRecommendationInsightGeneration:
    """Test AI insight generation for route recommendations."""

    @patch("app.gemini_service._initialize_gemini")
    @patch("app.gemini_service._genai")
    def test_gemini_success_generates_route_insight(self, mock_genai, mock_init):
        """Gemini API success returns AI-generated routing insight."""
        mock_init.return_value = True

        mock_model = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Rerouting to Gate B will save 16 minutes and avoid congestion at Gate A."
        mock_model.generate_content.return_value = mock_response
        mock_genai.GenerativeModel.return_value = mock_model

        insight = generate_route_recommendation_insight(
            current_gate="Gate A",
            alternate_gate="Gate B",
            route_status=RouteStatus.RECOMMENDED,
            estimated_wait_reduction=16,
            confidence_score=87.0,
            reason="Gate A has a crowd density of 85% and a queue time of 25 min.",
        )

        assert "Gate B" in insight or "Gate A" in insight
        assert len(insight) > 20

    @patch("app.gemini_service._initialize_gemini")
    def test_recommended_status_fallback(self, mock_init):
        """RECOMMENDED status produces appropriate fallback insight."""
        mock_init.return_value = False

        insight = generate_route_recommendation_insight(
            current_gate="Gate A",
            alternate_gate="Gate B",
            route_status=RouteStatus.RECOMMENDED,
            estimated_wait_reduction=16,
            confidence_score=87.0,
            reason="Gate A is congested.",
        )

        assert "Gate B" in insight
        assert "16" in insight or "save" in insight.lower()

    @patch("app.gemini_service._initialize_gemini")
    def test_clear_status_fallback(self, mock_init):
        """CLEAR status produces appropriate fallback insight."""
        mock_init.return_value = False

        insight = generate_route_recommendation_insight(
            current_gate="Gate C",
            alternate_gate=None,
            route_status=RouteStatus.CLEAR,
            estimated_wait_reduction=0,
            confidence_score=80.0,
            reason="Crowd density and queue time are below thresholds.",
        )

        assert "clear" in insight.lower() or "current" in insight.lower()

    @patch("app.gemini_service._initialize_gemini")
    def test_no_better_option_fallback(self, mock_init):
        """NO_BETTER_OPTION status produces appropriate fallback insight."""
        mock_init.return_value = False

        insight = generate_route_recommendation_insight(
            current_gate="Gate X",
            alternate_gate=None,
            route_status=RouteStatus.NO_BETTER_OPTION,
            estimated_wait_reduction=0,
            confidence_score=55.0,
            reason="Gate X is congested but no alternatives were provided.",
        )

        assert "no" in insight.lower() or "alternate" in insight.lower()


class TestGeminiIntegrationEndToEnd:
    """Test that Gemini integration doesn't break existing functionality."""

    def test_ai_insight_field_is_optional(self):
        """AI insight field is optional in response models."""
        from app.models import CrowdAnalysisResponse, RouteRecommendationResponse
        from datetime import datetime, timezone

        # CrowdAnalysisResponse without ai_insight
        response = CrowdAnalysisResponse(
            zone="Test",
            risk_level=RiskLevel.LOW,
            predicted_congestion=False,
            recommendation="Test",
            estimated_wait_reduction_minutes=0,
            severity_score=10.0,
            confidence_score=60.0,
            contributing_factors=["Test"],
            analyzed_at=datetime.now(timezone.utc),
        )
        assert response.ai_insight is None

        # RouteRecommendationResponse without ai_insight
        route_response = RouteRecommendationResponse(
            current_gate="Test",
            alternate_gate=None,
            alternate_zone=None,
            route_status=RouteStatus.CLEAR,
            recommendation="Test",
            estimated_wait_reduction_minutes=0,
            confidence_score=80.0,
            reason="Test",
            analyzed_at=datetime.now(timezone.utc),
        )
        assert route_response.ai_insight is None

    def test_ai_insight_field_accepts_string(self):
        """AI insight field accepts string values."""
        from app.models import CrowdAnalysisResponse
        from datetime import datetime, timezone

        response = CrowdAnalysisResponse(
            zone="Test",
            risk_level=RiskLevel.LOW,
            predicted_congestion=False,
            recommendation="Test",
            estimated_wait_reduction_minutes=0,
            severity_score=10.0,
            confidence_score=60.0,
            contributing_factors=["Test"],
            ai_insight="This is an AI-generated insight.",
            analyzed_at=datetime.now(timezone.utc),
        )
        assert response.ai_insight == "This is an AI-generated insight."
