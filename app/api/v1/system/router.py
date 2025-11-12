from fastapi import APIRouter, status
from app.core.logging import get_logger
from app.services.health_service import HealthCheckService
from app.models.system.response import HealthCheckResponse, TestResponse
from app.utils.decorators import catch_exceptions

router = APIRouter()
logger = get_logger(__name__)

@router.get("/health", response_model=HealthCheckResponse, status_code=status.HTTP_200_OK)
@catch_exceptions
async def health_check():
    health_service = HealthCheckService()
    response = await health_service.health_check()
    logger.info("Health check successful")
    return response

@router.get("/test", response_model=TestResponse, status_code=status.HTTP_200_OK)
@catch_exceptions
async def test():
    return {"message": "2"}
