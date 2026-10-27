from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.database import SessionLocal, dispose_engine, init_db
from app.models import Endpoint, HealthCheck
from app.routers.endpoints import router as endpoints_router
from app.services.monitor import check_endpoint

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("app")

CHECK_INTERVAL_SECONDS = 15


async def run_check_cycle() -> None:
    """Check every registered endpoint once and persist a HealthCheck row each."""
    # Read phase: grab the targets, then release the session before any network I/O.
    async with SessionLocal() as session:
        endpoints = list((await session.execute(select(Endpoint).order_by(Endpoint.id))).scalars().all())
        targets = [
            (e.id, e.url, e.method, e.timeout_seconds, e.expected_status_code) for e in endpoints
        ]

    if not targets:
        return

    # Check phase: pure HTTP, no open DB transaction.
    checks: list[HealthCheck] = []
    for endpoint_id, url, method, timeout, expected in targets:
        result = await check_endpoint(
            url,
            method=method,
            timeout=timeout,
            expected_status_code=expected,
        )
        checks.append(
            HealthCheck(
                endpoint_id=endpoint_id,
                status_code=result.status_code,
                response_time_ms=result.response_time_ms,
                is_success=result.is_success,
            )
        )
        logger.info(
            "checked %s %s -> status=%s time=%s ms success=%s",
            method,
            url,
            result.status_code,
            result.response_time_ms,
            result.is_success,
        )

    # Write phase: one short transaction for the whole batch.
    async with SessionLocal() as session:
        session.add_all(checks)
        await session.commit()


async def monitor_loop() -> None:
    """Run a check cycle immediately, then repeat every CHECK_INTERVAL_SECONDS."""
    while True:
        try:
            await run_check_cycle()
        except Exception:  # noqa: BLE001 — the worker must survive a bad cycle
            logger.exception("monitor cycle failed")
        await asyncio.sleep(CHECK_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: create tables, then start the background monitor.
    await init_db()
    monitor_task = asyncio.create_task(monitor_loop(), name="monitor-loop")
    yield
    # Shutdown: stop the monitor, then close the engine.
    monitor_task.cancel()
    with suppress(asyncio.CancelledError):
        await monitor_task
    await dispose_engine()


app = FastAPI(
    title="API Integration Health Monitor",
    description="Background monitoring service for API endpoint health.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(endpoints_router)


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness probe for the monitor itself."""
    return {"status": "ok"}


# Registered last so the API routes above always win over the SPA files.
app.mount(
    "/",
    StaticFiles(directory=os.path.join(os.path.dirname(__file__), "static"), html=True),
    name="static",
)
