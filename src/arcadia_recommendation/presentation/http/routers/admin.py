from uuid import UUID

from fastapi import APIRouter, status

from arcadia_recommendation.application.dto.recommendation_dto import RefreshReport
from arcadia_recommendation.domain.shared import limits
from arcadia_recommendation.domain.shared.ids import UserId
from arcadia_recommendation.presentation.http.deps.auth import ModeratorDep
from arcadia_recommendation.presentation.http.deps.container import UseCasesDep

router = APIRouter(prefix="/admin/recommendations", tags=["Admin"])


@router.post("/refresh", response_model=RefreshReport, status_code=status.HTTP_200_OK)
async def refresh_all(_actor: ModeratorDep, use_cases: UseCasesDep) -> RefreshReport:
    """Force the batch sweep now, instead of waiting for the scheduler.

    Synchronous on purpose. This exists so an operator — or an end-to-end test — can assert on the result of
    a generation rather than poll until one happens, and returning 202 with nothing to wait on would defeat
    both. It is bounded by GENERATION_BATCH_SIZE, so "synchronous" has a ceiling.
    """
    return await use_cases.refresh_all.execute()


@router.post("/users/{user_id}/refresh", response_model=RefreshReport)
async def refresh_user(user_id: UUID, _actor: ModeratorDep, use_cases: UseCasesDep) -> RefreshReport:
    """Regenerate one user, for investigating a specific complaint without sweeping the platform."""
    return await use_cases.refresh_all.execute_for(UserId(user_id), limits.DEFAULT_RECOMMENDATION_COUNT)
