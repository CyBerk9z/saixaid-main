from pydantic import BaseModel

class HealthCheckResponse(BaseModel):
    status: str
    services: dict

class TestResponse(BaseModel):
    message: str