"""FastAPI Application Entrypoint (CORE-01, SEC-01).

Configures routing, application lifecycle, OpenAPI documentation, and security middlewares.
"""

from contextlib import asynccontextmanager
import logging
from pathlib import Path
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.config import settings
from app.security.middleware import RequestSizeLimitMiddleware, SecurityHeadersMiddleware

# Configure standard structured logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("mlops.core")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Manage application startup and shutdown lifecycle events."""
    logger.info(
        "Starting %s v%s in [%s] environment",
        settings.app_name,
        settings.app_version,
        settings.environment,
    )
    yield
    logger.info("Shutting down %s", settings.app_name)


def create_application() -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description=(
            "An Intelligent Framework for ML Model Drift Detection, Monitoring, "
            "and Automated Recovery. Compliant with academic brief & SRS specifications."
        ),
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )

    # Security Boundary Middleware (Payload size limit <= 1 MB) (SR-04, NFR-12)
    app.add_middleware(
        RequestSizeLimitMiddleware,
        max_size_bytes=settings.max_request_size_bytes,
    )

    # Defensive Security Headers Middleware (OWASP compliance)
    app.add_middleware(SecurityHeadersMiddleware)

    # Cross-Origin Resource Sharing (CORS) Middleware with configured origins (SR-06)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.parsed_cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    # Root route for welcome/info
    @app.get("/", tags=["Root"])
    async def root():
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
            "health": "/health",
            "metrics": f"{settings.api_v1_prefix}/metrics/summary",
            "dashboard": "/dashboard",
        }

    # Mount API routers
    app.include_router(router)

    # Mount static assets and dashboard interface (UI-01, FR-13, NFR-08)
    static_dir = Path(__file__).resolve().parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.get("/dashboard", tags=["Dashboard"], response_class=HTMLResponse)
    async def dashboard():
        """Serve the interactive web dashboard (UI-01, FR-13, NFR-08)."""
        index_file = static_dir / "index.html"
        if index_file.exists():
            return FileResponse(str(index_file))
        return HTMLResponse("<h1>Dashboard index.html not found</h1>", status_code=404)

    return app


app = create_application()
