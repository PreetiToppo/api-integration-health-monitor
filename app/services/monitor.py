from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import httpx

logger = logging.getLogger("app.monitor")


@dataclass
class CheckResult:
    """Outcome of one health check attempt."""

    status_code: int | None
    response_time_ms: float | None
    is_success: bool


async def check_endpoint(
    url: str,
    method: str = "GET",
    timeout: float = 10.0,
    expected_status_code: int = 200,
) -> CheckResult:
    """Request `url` once and report the observed status, latency and outcome.

    Timeouts and any other transport failure are reported as a failed check:
    `status_code=None`, `is_success=False`.
    """
    start = time.perf_counter()
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            response = await client.request(method.upper(), url)
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        is_success = response.status_code == expected_status_code
        return CheckResult(
            status_code=response.status_code,
            response_time_ms=elapsed_ms,
            is_success=is_success,
        )
    except Exception as exc:  # noqa: BLE001 — a failing check must never crash the worker
        elapsed_ms = round((time.perf_counter() - start) * 1000, 2)
        logger.warning("check failed for %s %s (%s): %s", method, url, type(exc).__name__, exc)
        return CheckResult(status_code=None, response_time_ms=elapsed_ms, is_success=False)
