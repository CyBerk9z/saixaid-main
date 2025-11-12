from fastapi import HTTPException, status, Header
from app.services.auth.auth_service import decode_and_verify_token
from app.core.exceptions import AppException, ErrorCode
from app.utils.decorators import catch_exceptions
from app.models.chat import ChatUser, ChatAzureUser
from app.services.company.company_service import CompanyService
from app.services.azure.database import get_connection_uri_for_tenant_with_server_name
from app.db.tenant_prisma.prisma import Prisma as TenantClient
from app.utils.env_manager import temporary_env
from app.utils.db_client import tenant_client_context_by_company_id
from app.core.logging import get_logger

logger = get_logger(__name__)

@catch_exceptions
async def get_current_user_from_token(authorization: str = Header(...)) -> ChatUser:
    """
    リクエストヘッダーのAuthorizationからアクセストークンを抽出し,
    Azure ADで発行されたトークンとして検証する

    Authorization: Bearer <token>
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail = "Authorization header must start with 'Bearer '",
        )
    token = authorization.split(" ")[1]
    decoded_token = await decode_and_verify_token(token, "signin")
    if not decoded_token:
        raise AppException(
            error_code = ErrorCode.UNAUTHORIZED,
            message = "Invalid token",
        )
    
    azure_user: ChatAzureUser = ChatAzureUser(
        azure_user_id = decoded_token.get("sub"),
        name = decoded_token.get("name"),
        company_id = decoded_token.get("extension_companyId"),
        role = decoded_token.get("extension_role"),
    )

    async with tenant_client_context_by_company_id(azure_user.company_id) as tenant_client:
        user_info = await tenant_client.companyuser.find_first(
            where = {
                "azureUserId": azure_user.azure_user_id,
            }
        )

        if not user_info:
            raise AppException(
                error_code=ErrorCode.NOT_FOUND,
                message="User not found in database",
                context={
                    "azure_user_id": azure_user.azure_user_id
                }
            )

    user: ChatUser = ChatUser(
        user_id = user_info.id,
        name = azure_user.name,
        company_id = azure_user.company_id,
        role = azure_user.role,
    )

    if not all([user.user_id, user.name, user.company_id, user.role]):
        raise AppException(
            error_code = ErrorCode.UNAUTHORIZED,
            message = "Incomplete user information",
        )
    return user
    