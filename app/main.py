"""
SmartFlow AI — FastAPI application entry point.

Initialises the application, configures logging and middleware, registers
all API routes, and wires request handling to the service layer.

Route handlers are intentionally thin: validation is owned by Pydantic,
business logic is owned by services.py, and configuration is owned by
config.py.  This file is responsible only for wiring those layers together.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import uvicorn
from fastapi import APIRouter, FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.config import Settings, get_settings
from app.models import (
    CrowdAnalysisRequest,
    CrowdAnalysisResponse,
    HealthResponse,
    RouteRecommendationRequest,
    RouteRecommendationResponse,
    ServiceInfoResponse,
)
from app.services import analyze_crowd_conditions, recommend_alternate_route
from app.utils import utc_now_iso

# Module-level logger — handlers are attached inside _configure_logging(),
# which is called from _create_app() so no side effects occur at import time.
logger = logging.getLogger(__name__)


def _configure_logging(settings: Settings) -> None:
    """Configure logging for the current runtime environment.

    Attaches Google Cloud Logging when running on Cloud Run (detected via the
    K_SERVICE environment variable injected by the platform).  Falls back to
    standard stream logging for local development and test runs.

    Deferring this call to _create_app() — rather than running it at module
    import time — means importing app.main in tests does not trigger GCP
    credential lookups or alter the root logger unexpectedly.

    Args:
        settings: Resolved application settings supplying the log level.
    """
    on_cloud_run = bool(os.environ.get("K_SERVICE"))

    if on_cloud_run:
        try:
            import google.cloud.logging as gcp_logging  # pragma: no cover

            _gcp_client = gcp_logging.Client()  # pragma: no cover
            _gcp_client.setup_logging(  # pragma: no cover
                log_level=logging.getLevelName(settings.log_level)
            )
        except (ImportError, OSError, ValueError):  # pragma: no cover
            # ImportError:  google-cloud-logging not installed (shouldn't happen but safe).
            # OSError:      credentials file missing or unreadable.
            # ValueError:   malformed credentials or invalid log level.
            # Fall back to standard logging so the service still starts.
            logging.basicConfig(
                level=logging.getLevelName(settings.log_level),
                format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            )
    else:
        logging.basicConfig(
            level=logging.getLevelName(settings.log_level),
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )

# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

_DESCRIPTION = """
**SmartFlow AI** is a crowd intelligence backend for large sporting venues.

It analyses real-time crowd conditions at gates and zones, predicts
near-term congestion, and recommends safer or faster alternate routes —
helping venue operators improve attendee flow and safety.

### Capabilities
- **Crowd analysis** — risk classification, severity scoring, and congestion prediction
- **Route recommendation** — alternate gate suggestions with estimated time savings
"""


def _create_app() -> FastAPI:
    """Construct and configure the FastAPI application instance.

    Separating construction into a factory function keeps the module-level
    namespace clean and makes the app straightforward to instantiate in tests
    without triggering side effects at import time.

    Returns:
        Configured FastAPI application ready to serve requests.
    """
    settings = get_settings()

    _configure_logging(settings)

    application = FastAPI(
        title=settings.app_name,
        description=_DESCRIPTION,
        version=settings.app_version,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    _register_middleware(application, settings)
    _register_exception_handlers(application)
    _register_routes(application, settings)

    # Serve the single-page UI from app/static/.
    _static_dir = Path(__file__).parent / "static"
    if _static_dir.is_dir():
        application.mount(
            "/static",
            StaticFiles(directory=str(_static_dir)),
            name="static",
        )

    logger.info(
        "SmartFlow AI initialised | version=%s debug=%s log_level=%s",
        settings.app_version,
        settings.debug,
        settings.log_level,
    )

    return application


def _register_middleware(application: FastAPI, settings: Settings) -> None:
    """Attach middleware to the application.

    CORS middleware is only added when allowed_origins is explicitly
    configured.  An empty allowlist means no cross-origin access is
    permitted, which is the secure default.

    Rate limiting is expected to be handled at the reverse proxy layer
    (Cloud Run ingress, API Gateway, or nginx) before this service is
    exposed publicly.  See README "Future Improvements" for details.

    Args:
        application: The FastAPI instance to configure.
        settings: Resolved application settings.
    """
    if settings.allowed_origins:
        application.add_middleware(
            CORSMiddleware,
            allow_origins=settings.allowed_origins,
            allow_methods=["GET", "POST"],
            allow_headers=["Content-Type", "Accept"],
            allow_credentials=False,
        )
        logger.debug("CORS middleware enabled for origins: %s", settings.allowed_origins)


def _register_exception_handlers(application: FastAPI) -> None:
    """Register application-level exception handlers.

    A single catch-all handler for unhandled server errors ensures that
    internal stack traces and implementation details are never exposed in
    production responses.

    Args:
        application: The FastAPI instance to configure.
    """

    @application.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        """Return a generic 500 response for any unhandled exception.

        The error is logged server-side with full context; the client
        receives only a safe, non-revealing message.
        """
        logger.exception(
            "Unhandled exception on %s %s", request.method, request.url.path
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "An unexpected error occurred. Please try again later.",
                "timestamp": utc_now_iso(),
            },
        )


def _register_routes(application: FastAPI, settings: Settings) -> None:
    """Mount all routers onto the application.

    Args:
        application: The FastAPI instance to configure.
        settings: Resolved application settings, used for the API prefix.
    """
    application.include_router(_build_root_router())
    application.include_router(
        _build_api_router(),
        prefix=settings.api_prefix,
    )


# ---------------------------------------------------------------------------
# Root router  (/ and /health — no versioned prefix)
# ---------------------------------------------------------------------------


def _build_root_router() -> APIRouter:
    """Build the root router containing service-level utility endpoints.

    Returns:
        APIRouter with GET / and GET /health registered.
    """
    router = APIRouter(tags=["Service"])

    @router.get(
        "/ui",
        summary="Web interface",
        response_description="Single-page crowd analysis UI.",
        include_in_schema=False,
    )
    async def ui() -> FileResponse:
        """Serve the single-page crowd analysis and route recommendation UI."""
        return FileResponse(
            Path(__file__).parent / "static" / "index.html",
            media_type="text/html",
        )

    @router.get(
        "/",
        response_model=ServiceInfoResponse,
        summary="Service overview",
        response_description="Application identity and project summary.",
    )
    async def root() -> ServiceInfoResponse:
        """Return application identity and a concise project summary.

        Useful for evaluators and operators to quickly confirm which
        service they are talking to and what it does.
        """
        _s = get_settings()
        return ServiceInfoResponse(
            service=_s.app_name,
            version=_s.app_version,
            summary=(
                "Crowd intelligence backend for large sporting venues. "
                "Analyses crowd conditions, predicts congestion, and recommends "
                "safer or faster alternate routes and gates."
            ),
            docs="/docs",
        )

    @router.get(
        "/health",
        response_model=HealthResponse,
        summary="Health check",
        response_description="Service liveness confirmation with UTC timestamp.",
    )
    async def health() -> HealthResponse:
        """Confirm the service is running and return a UTC liveness timestamp.

        Designed for use by Cloud Run health probes, load balancers, and
        uptime monitors.  The response is deterministic and lightweight.
        """
        return HealthResponse(
            status="ok",
            service=get_settings().app_name,
            timestamp=utc_now_iso(),
        )

    @router.get(
        "/health/gemini",
        summary="Gemini AI health check",
        response_description="Verifies Google Gemini AI integration status with real API call.",
        include_in_schema=True,
    )
    async def gemini_health() -> dict:
        """Check if Google Gemini AI is properly configured and accessible.

        Performs a REAL API call to verify the integration is working.
        This endpoint helps verify that AI-powered insights are active.
        """
        from app.gemini_service import test_gemini_connection
        from app.utils import utc_now_iso
        
        test_result = test_gemini_connection()
        
        # Determine overall status
        if test_result["api_call_successful"] and test_result["response_received"]:
            status = "operational"
        elif test_result["client_created"]:
            status = "client_ready_but_api_failed"
        elif test_result["api_key_configured"]:
            status = "api_key_set_but_client_failed"
        else:
            status = "fallback_mode"
        
        return {
            **test_result,
            "status": status,
            "timestamp": utc_now_iso(),
        }

    return router


# ---------------------------------------------------------------------------
# API router  (versioned, mounted at /api/v1)
# ---------------------------------------------------------------------------


def _build_api_router() -> APIRouter:
    """Build the versioned API router containing domain endpoints.

    Returns:
        APIRouter with POST /analyze-crowd and POST /recommend-route registered.
    """
    router = APIRouter(tags=["Crowd Intelligence"])

    @router.post(
        "/analyze-crowd",
        response_model=CrowdAnalysisResponse,
        status_code=status.HTTP_200_OK,
        summary="Analyze crowd conditions at a venue zone",
        response_description=(
            "Risk level, congestion prediction, severity score, confidence score, "
            "contributing factors, and an actionable recommendation."
        ),
    )
    async def analyze_crowd(request: CrowdAnalysisRequest) -> CrowdAnalysisResponse:
        """Analyse crowd conditions at a specific gate or zone.

        Accepts a validated crowd snapshot and returns a structured risk
        assessment including risk level, near-term congestion prediction,
        severity and confidence scores, and a human-readable recommendation.

        All business logic is delegated to the service layer.
        """
        logger.info(
            "Crowd analysis requested | zone=%s density=%.1f queue=%dmin phase=%s",
            request.zone,
            request.crowd_density,
            request.queue_time_minutes,
            request.event_phase.value,
        )
        return analyze_crowd_conditions(request)

    @router.post(
        "/recommend-route",
        response_model=RouteRecommendationResponse,
        status_code=status.HTTP_200_OK,
        summary="Recommend an alternate gate or route",
        response_description=(
            "Alternate gate suggestion, route status, confidence score, "
            "estimated wait reduction, and reasoning."
        ),
    )
    async def recommend_route(
        request: RouteRecommendationRequest,
    ) -> RouteRecommendationResponse:
        """Recommend an alternate gate or route for a congested location.

        Evaluates current crowd density and queue time at the specified gate.
        Returns a rerouting recommendation with estimated time savings when
        conditions exceed safe thresholds, or a clear advisory when they do not.

        All business logic is delegated to the service layer.
        """
        logger.info(
            "Route recommendation requested | gate=%s density=%.1f queue=%dmin phase=%s",
            request.current_gate,
            request.crowd_density,
            request.queue_time_minutes,
            request.event_phase.value,
        )
        return recommend_alternate_route(request)

    return router


# ---------------------------------------------------------------------------
# Application instance
# ---------------------------------------------------------------------------

app: FastAPI = _create_app()

# ---------------------------------------------------------------------------
# Local development entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    # Intended for local development only.
    # In production (Cloud Run), the container starts uvicorn directly:
    #   uvicorn app.main:app --host 0.0.0.0 --port $PORT
    _dev_settings = get_settings()
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=8000,
        reload=_dev_settings.debug,
        log_level=_dev_settings.log_level.lower(),
    )
