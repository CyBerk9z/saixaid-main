from azure.storage.blob import BlobServiceClient
import requests
import os
from app.core.exceptions import AppException, ErrorCode
from azure.ai.inference import ChatCompletionsClient
from azure.core.credentials import AzureKeyCredential
from azure.core.exceptions import AzureError
from app.db.master_prisma.prisma import Prisma as MasterClient
from app.db.tenant_prisma.prisma import Prisma as TenantClient
from app.services.azure.database import execute_with_client
from app.core.logging import get_logger
from app.services.company.company_service import CompanyService
from app.services.azure.database import get_connection_uri_for_tenant_with_server_name
from app.models.system.response import HealthCheckResponse
from azure.search.documents.indexes import SearchIndexClient

from app.core.config import settings

logger = get_logger(__name__)

class HealthCheckService:
    @staticmethod
    async def health_check() -> HealthCheckResponse:
        services_status = {}
        
        # 1.1. Master PostgreSQL Check
        try:
            async def master_operation(client: MasterClient):
                _ = await client.tenants.find_first()
                return "ok"
            master_status = await execute_with_client(MasterClient, master_operation)
            services_status["master_db"] = master_status
        except Exception as e:
            logger.error(f"PostgreSQL error: {e}")
            services_status["master_db"] = "error"

        # 1.2. Tenant PostgreSQL Check
        try:
            async def tenant_operation(client: TenantClient):
                _ = await client.company.find_first(where={"companyName": {"not": ""}})
                return "ok"
            tenant_server_names = await CompanyService.get_all_tenant_server_names()
            for server_name in tenant_server_names:
                db_url = get_connection_uri_for_tenant_with_server_name(server_name)
                try:
                    os.environ["DATABASE_URL"] = db_url
                    _ = await execute_with_client(TenantClient, tenant_operation)
                    services_status[server_name] = "ok"
                except Exception as e:
                    logger.error(f"Tenant DB check error for {server_name}: {e}")
                    services_status[server_name] = f"error ({str(e)})"
        except Exception as e:
            logger.error(f"Error retrieving tenant server names: {e}")
            services_status["tenant_db"] = "error"

        # 2. Azure Blob Storage Check
        try:
            blob_service_client = BlobServiceClient.from_connection_string(
                settings.AZURE_STORAGE_CONNECTION_STRING
            )
            _ = list(blob_service_client.list_containers())
            services_status["blob_storage"] = "ok"
        except Exception as e:
            logger.error(f"Blob Storage error: {e}")
            services_status["blob_storage"] = "error"

        # 3. Azure OpenAI Check
        try:
            client = ChatCompletionsClient(
                endpoint=f"{settings.AZURE_OPENAI_API_ENDPOINT}/openai/deployments/{settings.AZURE_OPENAI_API_DEPLOYMENT_NAME}",
                credential=AzureKeyCredential(settings.AZURE_OPENAI_API_KEY)
            )
            
            test_message = {
                "messages": [
                    {
                        "role": "user",
                        "content": "test"
                    }
                ],
                "max_tokens": 5,
                "temperature": 0.5,
                "top_p": 1,
            }
            
            try:
                response = client.complete(test_message)
                services_status["azure_openai"] = "ok"
            except AzureError as e:
                logger.error(f"Azure OpenAI API error: {e}")
                services_status["azure_openai"] = "error"

        except Exception as e:
            logger.error(f"Azure OpenAI client error: {e}")
            services_status["azure_openai"] = f"error (Client: {str(e)})"

        # 4. Azure AI Search Check
        try:
            _ = SearchIndexClient(
                endpoint=settings.AZURE_SEARCH_SERVICE_ENDPOINT,
                credential=AzureKeyCredential(settings.AZURE_SEARCH_ADMIN_KEY)
            )
            # サービスへの接続のみを確認
            services_status["azure_search"] = "ok"
        except Exception as e:
            logger.error(f"Azure Search error: {e}")
            services_status["azure_search"] = "error"

        overall_status = "ok"
        for val in services_status.values():
            if isinstance(val, str) and val.startswith("error"):
                overall_status = "error"
                break

        if overall_status == "error":
            raise AppException(
                error_code=ErrorCode.SERVICE_UNAVAILABLE,
                message="One or more health checks failed",
                context=services_status
            )

        return HealthCheckResponse(
            status=overall_status,
            services=services_status
        )
