from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Endpoint, HealthCheck
from app.schemas import EndpointCreate, EndpointMetrics, EndpointResponse

router = APIRouter(prefix="/endpoints", tags=["endpoints"])


@router.post("", response_model=EndpointResponse, status_code=status.HTTP_201_CREATED)
async def create_endpoint(
    payload: EndpointCreate,
    db: AsyncSession = Depends(get_db),
) -> Endpoint:
    """Register a new endpoint to monitor."""
    endpoint = Endpoint(**payload.model_dump())
    db.add(endpoint)
    await db.commit()
    await db.refresh(endpoint)
    return endpoint


@router.get("", response_model=list[EndpointResponse])
async def list_endpoints(db: AsyncSession = Depends(get_db)) -> list[Endpoint]:
    """List every monitored endpoint."""
    result = await db.execute(select(Endpoint).order_by(Endpoint.id))
    return list(result.scalars().all())


@router.get("/{endpoint_id}/metrics", response_model=EndpointMetrics)
async def endpoint_metrics(
    endpoint_id: int,
    db: AsyncSession = Depends(get_db),
) -> EndpointMetrics:
    """Uptime and latency analytics derived from this endpoint's check history."""
    existing = (
        await db.execute(select(Endpoint.id).where(Endpoint.id == endpoint_id))
    ).scalar_one_or_none()
    if existing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Endpoint {endpoint_id} not found",
        )

    # One aggregate query: AVG() skips NULL response times by itself.
    total, successful, avg_response_time = (
        await db.execute(
            select(
                func.count(HealthCheck.id),
                func.coalesce(
                    func.sum(case((HealthCheck.is_success.is_(True), 1), else_=0)),
                    0,
                ),
                func.avg(HealthCheck.response_time_ms),
            ).where(HealthCheck.endpoint_id == endpoint_id)
        )
    ).one()

    total = int(total)
    successful = int(successful)
    uptime_percentage = (successful / total * 100.0) if total else 0.0
    avg_response_time_ms = float(avg_response_time) if avg_response_time is not None else 0.0

    return EndpointMetrics(
        endpoint_id=endpoint_id,
        total_checks=total,
        successful_checks=successful,
        uptime_percentage=uptime_percentage,
        avg_response_time_ms=avg_response_time_ms,
    )
