from __future__ import annotations

import logging
import logging.config

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import health

logger = logging.getLogger(__name__)


def _configure_logging(debug: bool) -> None:
    logging.config.dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {
                "default": {
                    "format": "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                    "datefmt": "%Y-%m-%dT%H:%M:%S",
                },
            },
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "default",
                },
            },
            "root": {
                "handlers": ["console"],
                "level": "DEBUG" if debug else "INFO",
            },
        }
    )


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logger.info("CipherTrade API starting — environment=%s", settings.environment)
    yield
    logger.info("CipherTrade API shutting down")


def create_app() -> FastAPI:
    settings = get_settings()
    _configure_logging(settings.debug)

    app = FastAPI(
        title="CipherTrade API",
        version="0.1.0",
        description="Blockchain-powered peer-to-peer marketplace backend.",
        docs_url="/docs" if settings.debug else None,
        redoc_url="/redoc" if settings.debug else None,
        lifespan=_lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health.router)

    return app
