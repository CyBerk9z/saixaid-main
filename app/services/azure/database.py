from app.core.config import settings
from app.core.logging import get_logger
from app.core.exceptions import AppException, ErrorCode
from app.db.master_prisma.prisma import Prisma as MasterClient
import urllib.parse

logger = get_logger(__name__)

async def execute_with_client(client_class, operation, *args, **kwargs):
    """
    client_classのインスタンスを作成し、接続後にoperationを実行する
    最後に必ずdisconnectを呼び出す
    """
    client = None
    try:
        client = client_class()
        await client.connect()
        return await operation(client, *args, **kwargs)
    except AppException as e:
        raise e
    except Exception as e:
        raise AppException(
            error_code=ErrorCode.DATABASE_ERROR,
            message="Failed to execute operation with client",
            context={"error": str(e)}
        )
    finally:
        if client:
            try:
                await client.disconnect()
            except Exception as disconnect_error:
                logger.error(
                    f"Error disconnecting {client_class.__name__}",
                    extra={"error": str(disconnect_error)},
                    exc_info=True
                ) 

def get_connection_uri_for_tenant_with_server_name(company_server_name: str):
    """テナントデータベースの接続URIを生成する"""
    try:
        dbuser = urllib.parse.quote(settings.POSTGRES_USER)
        dbpassword = settings.POSTGRES_PW
        dbhost = f"{company_server_name}.postgres.database.azure.com"
        dbname = settings.POSTGRES_DB
        sslmode = "require"
        return f"postgresql://{dbuser}:{dbpassword}@{dbhost}/{dbname}?sslmode={sslmode}"
    except Exception as e:
        raise Exception(f"Failed to get database connection URI: {str(e)}")
    
async def get_company_server_name_from_company_id(company_id: str) -> str:
    """
    企業IDから企業サーバーネームを取得する
    """
    async def operation(client: MasterClient):
        company = await client.tenants.find_first(where={"companyId": company_id})
        if not company:
            logger.error(f"Company not found for company_id: {company_id}")
            raise AppException(
                error_code=ErrorCode.NOT_FOUND,
                message="Company not found",
                context={"company_id": company_id}
            )
        return company.companyServerName
    return await execute_with_client(MasterClient, operation) 