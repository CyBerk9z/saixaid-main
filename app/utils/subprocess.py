import os
import subprocess
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger

logger = get_logger(__name__)

def prisma_db_push(schema_path: str) -> None:
    try:
        subprocess.run(
            ["prisma", "db", "push", f"--schema={schema_path}"],
            env=os.environ,
            check=True,
        )
        logger.info(f"Prisma DB push completed successfully for schema: {schema_path}")
    except subprocess.CalledProcessError as e:
        logger.error("Prisma db push failed", extra={"error": str(e), "schema": schema_path}, exc_info=True)
        raise AppException(
            error_code=ErrorCode.DATABASE_ERROR,
            message="Prisma DB push failed",
            context={"error": str(e), "schema": schema_path}
        )