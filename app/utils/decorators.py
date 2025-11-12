from functools import wraps
from fastapi import HTTPException
from app.core.exceptions import AppException

def catch_exceptions(func):
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except AppException as e:
            raise HTTPException(
                status_code=e.status_code,
                detail=str(e)
            )
        except Exception as e:
            raise HTTPException(
                status_code=500,
                detail=str(e)
            )
    return wrapper
