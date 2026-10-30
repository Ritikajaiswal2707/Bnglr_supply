"""FastAPI application factory."""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import protected, public
from .config import Settings, get_settings
from . import models  # noqa: F401  (registers tables)
from .db import Base, get_engine, init_engine
from .seed import seed_if_empty
from .services.assignment import AssignmentError
from .services.geocoding import build_geocoder
from .services.routing import build_routing

log = logging.getLogger("smart_assign")


def create_app(settings: Settings | None = None, auto_seed: bool = True) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        init_engine(settings.database_url)
        Base.metadata.create_all(get_engine())
        if auto_seed:
            out = seed_if_empty()
            if out:
                log.info("Seeded demo data: %s", out)
        yield

    app = FastAPI(title="PeakAge Smart Assign", version="0.1.0", lifespan=lifespan,
                  description="Explainable, travel-aware provider allocation. Prototype with synthetic data only.")
    app.state.settings = settings
    app.state.routing = build_routing(settings)
    app.state.geocoder = build_geocoder(settings)
    if settings.routing_mode == "live" and not settings.ola_api_key:
        log.warning("ROUTING_MODE=live but OLA_MAPS_API_KEY is missing: routing and geocoding will report errors")

    origins = settings.frontend_origins
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                           allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["Content-Type", "X-Access-Token"])

    @app.exception_handler(AssignmentError)
    async def _assignment_error(_: Request, e: AssignmentError):
        return JSONResponse({"detail": {"code": e.code, "message": e.message}}, status_code=e.status)

    app.include_router(public)
    app.include_router(protected)

    static = settings.static_dir
    if static and os.path.isdir(static):
        assets = os.path.join(static, "assets")
        if os.path.isdir(assets):
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/") or path == "api":
                return JSONResponse({"detail": "Not found"}, status_code=404)
            candidate = os.path.realpath(os.path.join(static, path))
            if path and candidate.startswith(os.path.realpath(static) + os.sep) and os.path.isfile(candidate):
                return FileResponse(candidate)
            return FileResponse(os.path.join(static, "index.html"))

    return app


def app_factory() -> FastAPI:
    """uvicorn app.main:app_factory --factory"""
    return create_app()
