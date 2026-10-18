from __future__ import annotations

import logging
import sqlite3
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated

from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import ValidationError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from . import __version__
from .collector import Collector
from .comparison import ComparisonError, Search, compare, status_snapshot
from .database import initialize
from .metrics import Metrics
from .settings import Settings, load_settings
from .station_catalog import apply_catalog

LOGGER = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.prepare_directories()
    initialize(settings.database)
    apply_catalog(settings.database, settings.root / "config" / "bulgarian-stations.json")
    metrics = Metrics()
    collector = Collector(settings, metrics)
    templates = Jinja2Templates(directory=str(settings.root / "clean_air" / "templates"))
    templates.env.filters["utc"] = lambda value: (
        datetime.fromtimestamp(value, UTC).strftime("%d %b %Y, %H:%M UTC") if value else "Not yet"
    )

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        if settings.options.collection.enabled:
            collector.start()
        try:
            yield
        finally:
            collector.stop()

    app = FastAPI(
        title="Clean Air Finder",
        description="Historical Bulgarian air-quality comparisons from locally stored EEA measurements.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
    )
    app.state.settings = settings
    app.state.collector = collector
    app.state.metrics = metrics
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.mount(
        "/static", StaticFiles(directory=str(settings.root / "clean_air" / "static")), name="static"
    )

    @app.middleware("http")
    async def observe(request: Request, call_next):
        started = time.perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; script-src 'self'; style-src 'self'; "
                "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
                "base-uri 'self'; form-action 'self'"
            )
            return response
        finally:
            route = getattr(request.scope.get("route"), "path", "unmatched")
            metrics.requests.labels(route, str(status)).inc()
            metrics.latency.labels(route).observe(time.perf_counter() - started)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, error: RequestValidationError):
        messages = [item["msg"] for item in error.errors()]
        LOGGER.warning("Invalid request to %s: %s", request.url.path, messages)
        return JSONResponse(
            {"error": {"code": "INVALID_REQUEST", "message": " ".join(messages)}},
            status_code=422,
        )

    @app.exception_handler(ComparisonError)
    async def invalid_comparison(request: Request, error: ComparisonError):
        LOGGER.warning("Comparison rejected (%s): %s", error.code, error.message)
        return JSONResponse(
            {"error": {"code": error.code, "message": error.message, "details": error.details}},
            status_code=error.status,
        )

    @app.exception_handler(sqlite3.Error)
    async def database_error(request: Request, error: sqlite3.Error):
        LOGGER.error("Database failure on %s", request.url.path, exc_info=error)
        return JSONResponse(
            {
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "Local storage is unavailable. Inspect var\\logs\\application.jsonl.",
                }
            },
            status_code=503,
        )

    def search_context(values: dict[str, str]) -> tuple[dict, int]:
        try:
            search = Search.model_validate(values)
            return {"result": compare(settings, search), "error": None}, 200
        except ValidationError as error:
            message = " ".join(item["msg"] for item in error.errors())
            LOGGER.warning("Invalid browser search: %s", message)
            return {"result": None, "error": {"message": message, "details": None}}, 422
        except ComparisonError as error:
            LOGGER.warning("Browser comparison rejected (%s): %s", error.code, error.message)
            return {
                "result": None, "error": {"message": error.message, "details": error.details}
            }, error.status

    def browser_values(request: Request, snapshot: dict) -> dict[str, str]:
        options = settings.options.comparison
        values = {
            "pollutant": options.default_pollutant, "profile": "who_2021",
            "view": options.default_view, "objective": options.default_objective,
            "target": options.default_target,
        }
        for name in (
            "start", "end", "pollutant", "profile", "threshold", "view", "objective",
            "target", "location",
        ):
            if name in request.query_params and request.query_params[name] != "":
                values[name] = request.query_params[name]
        return values

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def index(request: Request):
        snapshot = status_snapshot(settings)
        values = browser_values(request, snapshot)
        context = {"result": None, "error": None}
        status_code = 200
        if snapshot["ready"]:
            context, status_code = search_context(values)
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "snapshot": snapshot, "values": values, "settings": settings,
                "version": __version__, **context,
            },
            status_code=status_code,
        )

    @app.get("/results", response_class=HTMLResponse, include_in_schema=False)
    def results_fragment(request: Request):
        snapshot = status_snapshot(settings)
        context, status = search_context(browser_values(request, snapshot))
        return templates.TemplateResponse(
            request=request, name="results.html", context=context, status_code=status
        )

    @app.get("/api/cleanest", tags=["Comparison"])
    def cleanest(search: Annotated[Search, Query()]):
        return compare(settings, search)

    @app.get("/docs", response_class=HTMLResponse, include_in_schema=False)
    def documentation(request: Request):
        snapshot = status_snapshot(settings)
        return templates.TemplateResponse(
            request=request, name="docs.html",
            context={"snapshot": snapshot, "settings": settings, "version": __version__},
        )

    @app.get("/api/status", tags=["Operations"])
    def collection_status():
        snapshot = status_snapshot(settings)
        snapshot["worker_running"] = bool(collector.thread and collector.thread.is_alive())
        return snapshot

    @app.get("/health/live", tags=["Operations"])
    def live():
        return {"status": "alive", "version": __version__}

    @app.get("/health/ready", tags=["Operations"])
    def ready():
        snapshot = status_snapshot(settings)
        return JSONResponse(
            {"status": "ready" if snapshot["ready"] else "backfill_pending", "history": snapshot},
            status_code=200 if snapshot["ready"] else 503,
        )

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics():
        metrics.worker_alive.set(bool(collector.thread and collector.thread.is_alive()))
        metrics.worker_expected.set(settings.options.collection.enabled)
        metrics.refresh(settings)
        return Response(generate_latest(metrics.registry), media_type=CONTENT_TYPE_LATEST)

    return app
