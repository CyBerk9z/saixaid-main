from contextlib import asynccontextmanager
from app.db.tenant_prisma.prisma import Prisma as TenantClient
from app.utils.env_manager import temporary_env
from app.services.azure.database import get_connection_uri_for_tenant_with_server_name, get_company_server_name_from_company_id
from app.core.logging import get_logger

logger = get_logger(__name__)

@asynccontextmanager
async def tenant_client_context_by_company_id(company_id: str):
    """
    指定された企業IDに紐づくテナントのデータベース接続を管理するコンテキストマネージャー
    """
    server_name = await get_company_server_name_from_company_id(company_id)
    db_url = get_connection_uri_for_tenant_with_server_name(server_name)

    with temporary_env("DATABASE_URL", db_url):
        tenant_client = TenantClient()
        await tenant_client.connect()
        try:
            yield tenant_client
        finally:
            try:
                await tenant_client.disconnect()
            except Exception as disconnect_error:
                logger.error(
                    f"Error disconnecting {tenant_client.__class__.__name__} in cleanup",
                    extra={"error": str(disconnect_error)},
                    exc_info=True
                )