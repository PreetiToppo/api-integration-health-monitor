from __future__ import annotations

import asyncio
import os

# Must be set before app.* is imported: the engine reads it at import time.
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./test_monitor.db"

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.main as main_module
from app.database import DATABASE_URL
from app.main import app
from app.models import Base, Endpoint, HealthCheck

TEST_DB_PATH = "./test_monitor.db"


@pytest.fixture(scope="module")
def client():
    """Boot the app against the test database with the monitor loop parked."""
    if os.path.exists(TEST_DB_PATH):
        os.remove(TEST_DB_PATH)

    # Keep the background worker from inserting rows mid-test.
    original_interval = main_module.CHECK_INTERVAL_SECONDS
    main_module.CHECK_INTERVAL_SECONDS = 3600
    try:
        with TestClient(app) as c:
            yield c
    finally:
        main_module.CHECK_INTERVAL_SECONDS = original_interval
        if os.path.exists(TEST_DB_PATH):
            try:
                os.remove(TEST_DB_PATH)
            except OSError:
                pass


async def _insert_checks(endpoint_id: int, rows: list[dict]) -> None:
    """Insert HealthCheck rows on its own engine, created and closed in this loop."""
    engine = create_async_engine(DATABASE_URL)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            session.add_all([HealthCheck(endpoint_id=endpoint_id, **row) for row in rows])
            await session.commit()
    finally:
        await engine.dispose()


def _create_endpoint(client: TestClient, name: str) -> int:
    response = client.post(
        "/endpoints",
        json={
            "name": name,
            "url": "https://example.com",
            "expected_status_code": 200,
            "timeout_seconds": 5,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_metrics_uptime_and_average(client: TestClient) -> None:
    endpoint_id = _create_endpoint(client, "metrics target")
    asyncio.run(
        _insert_checks(
            endpoint_id,
            [
                {"status_code": 200, "response_time_ms": 100.0, "is_success": True},
                {"status_code": 200, "response_time_ms": 200.0, "is_success": True},
                {"status_code": 200, "response_time_ms": 300.0, "is_success": True},
                {"status_code": 500, "response_time_ms": 700.0, "is_success": False},
            ],
        )
    )

    response = client.get(f"/endpoints/{endpoint_id}/metrics")
    assert response.status_code == 200

    metrics = response.json()
    assert metrics["endpoint_id"] == endpoint_id
    assert metrics["total_checks"] == 4
    assert metrics["successful_checks"] == 3
    assert metrics["uptime_percentage"] == 75.0
    # (100 + 200 + 300 + 700) / 4
    assert metrics["avg_response_time_ms"] == 325.0


def test_metrics_ignores_none_response_time(client: TestClient) -> None:
    endpoint_id = _create_endpoint(client, "failed check without timing")
    asyncio.run(
        _insert_checks(
            endpoint_id,
            [
                {"status_code": 200, "response_time_ms": 120.0, "is_success": True},
                {"status_code": None, "response_time_ms": None, "is_success": False},
            ],
        )
    )

    metrics = client.get(f"/endpoints/{endpoint_id}/metrics").json()
    assert metrics["total_checks"] == 2
    assert metrics["successful_checks"] == 1
    assert metrics["uptime_percentage"] == 50.0
    assert metrics["avg_response_time_ms"] == 120.0


def test_metrics_without_checks_returns_zeros(client: TestClient) -> None:
    endpoint_id = _create_endpoint(client, "brand new endpoint")

    metrics = client.get(f"/endpoints/{endpoint_id}/metrics").json()
    assert metrics == {
        "endpoint_id": endpoint_id,
        "total_checks": 0,
        "successful_checks": 0,
        "uptime_percentage": 0.0,
        "avg_response_time_ms": 0.0,
    }


def test_metrics_unknown_endpoint_returns_404(client: TestClient) -> None:
    response = client.get("/endpoints/999999/metrics")
    assert response.status_code == 404


def test_metrics_stored_rows_persist(client: TestClient) -> None:
    """The injected rows are real DB rows, not response shaping."""
    endpoint_id = _create_endpoint(client, "row count target")
    asyncio.run(
        _insert_checks(
            endpoint_id,
            [
                {"status_code": 200, "response_time_ms": 50.0, "is_success": True},
                {"status_code": 200, "response_time_ms": 150.0, "is_success": True},
                {"status_code": 200, "response_time_ms": 250.0, "is_success": True},
                {"status_code": 500, "response_time_ms": 450.0, "is_success": False},
            ],
        )
    )

    endpoints = client.get("/endpoints").json()
    assert any(e["id"] == endpoint_id for e in endpoints)

    metrics = client.get(f"/endpoints/{endpoint_id}/metrics").json()
    assert metrics["total_checks"] == 4
    assert metrics["uptime_percentage"] == 75.0
    assert metrics["avg_response_time_ms"] == 225.0
