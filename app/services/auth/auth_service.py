from app.core.config import settings
from app.core.logging import get_logger
from app.core.exceptions import AppException, ErrorCode
from app.models.auth import *
from app.utils.db_client import tenant_client_context_by_company_id
from enum import Enum
import jwt
from jwt.exceptions import InvalidTokenError
from jwt.jwks_client import PyJWKClient
import requests
from fastapi import Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi import HTTPException
from fastapi import status

logger = get_logger(__name__)   

security = HTTPBearer()

class AuthType(Enum):
    SIGNUP = "signup"
    SIGNIN = "signin"

async def exchange_code_for_token(code: str, auth_type: str) -> str:
    """
    認可コードを受け取り、トークンエンドポイントにリクエストしてトークン一式(id_token など）を取得する。
    """
    try:
        auth_type_enum = AuthType(auth_type)
    except ValueError:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="Invalid authentication type",
            context={
                "auth_type": auth_type,
                "valid_types": [t.value for t in AuthType]
            }
        )
    tenant_name = settings.AZURE_TENANT_NAME
    client_id = settings.AZURE_CLIENT_ID
    client_secret = settings.AZURE_CLIENT_SECRET
    policy_name = (
        settings.AZURE_B2C_SIGNUP_POLICY_NAME if auth_type_enum == AuthType.SIGNUP
        else settings.AZURE_B2C_SIGNIN_POLICY_NAME
    )

    token_url = (
        f"https://{tenant_name}.b2clogin.com/"
        f"{tenant_name}.onmicrosoft.com/{policy_name}/oauth2/v2.0/token"
    )

    #TODO: 本番環境用のURLに変更する
    redirect_uri = f"https://inthub-fjfjehdsc4akamdg.japaneast-01.azurewebsites.net/api/v1/auth/{auth_type_enum.value}/callback"
    payload = {
        "grant_type": "authorization_code",
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": redirect_uri,
        "scope": f"openid offline_access https://{tenant_name}.onmicrosoft.com/{client_id}/read"
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(token_url, data=payload, headers=headers)
    response.raise_for_status()
    return response.json()

async def decode_and_verify_token(id_token: str, auth_type:str) -> str:
    """
    IDトークンの署名検証を行い、デコードしたトークンを返す
    """
    try:
        auth_type_enum = AuthType(auth_type)
    except ValueError:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="Invalid authentication type",
            context={
                "auth_type": auth_type,
                "valid_types": [t.value for t in AuthType]
            }
        )
    policy_name = (
        settings.AZURE_B2C_SIGNUP_POLICY_NAME if auth_type_enum == AuthType.SIGNUP
        else settings.AZURE_B2C_SIGNIN_POLICY_NAME
    )
    try:
        jwks_url = (
            f"https://{settings.AZURE_TENANT_NAME}.b2clogin.com/"
            f"{settings.AZURE_TENANT_NAME}.onmicrosoft.com/"
            f"{policy_name}/discovery/v2.0/keys"
        )

        jwks_client = PyJWKClient(jwks_url)
        signing_key = jwks_client.get_signing_key_from_jwt(id_token)

        tenant_lower_name = settings.AZURE_TENANT_NAME.lower()

        decoded_token = jwt.decode(
            id_token,
            signing_key.key,
            algorithms=["RS256"],
            audience=settings.AZURE_CLIENT_ID,
            issuer=f"https://{tenant_lower_name}.b2clogin.com/{settings.AZURE_TENANT_ID}/v2.0/",
            options={
                "verify_exp": False,
                "verify_iat": False,
                "verify_nbf": False,
            }
        )

        return decoded_token
    except InvalidTokenError as e:
        logger.error(f"Invalid token: {str(e)}")
        raise AppException(
            error_code=ErrorCode.INVALID_TOKEN,
            message="Invalid token format",
            context={"error": str(e)}
        )

async def process_signup_callback(decoded_token: dict) -> SignUpResponse:
    """
    Azure AD B2C から返されたIDトークンをデコードし、ユーザー属性を抽出
    """

    if not decoded_token:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="ID token is missing"
        )
    
    logger.info("Decoding ID token")

    # 標準属性の抽出
    user_email = decoded_token.get("emails")[0]     
    display_name = decoded_token.get("name")
    azure_user_id = decoded_token.get("sub")

    # 拡張属性の抽出
    company_id_key = f"extension_companyId"
    role_key = f"extension_role"
    company_id = decoded_token.get(company_id_key)
    user_role = decoded_token.get(role_key)

    if (company_id is None or user_role is None or user_email is None or display_name is None or azure_user_id is None):
        raise AppException(
            error_code=ErrorCode.INVALID_TOKEN,
            message="Invalid token format",
            context={"token_claims": list(decoded_token.keys())}
        )
    
    user_info: SignUpResponse = SignUpResponse(
        email=user_email,
        name=display_name,
        azure_user_id=azure_user_id,
        company_id=company_id,
        role=user_role
    )
    
    logger.info("Token processed successfully")
    return user_info

async def process_signin_callback(decoded_token: dict, access_token: str, refresh_token: str) -> SignInResponse:
    """
    Azure AD B2C から返されたIDトークンをデコードし、ユーザー属性を抽出
    """
    if not decoded_token:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="ID token is missing"
        )
    
    logger.info("Decoding ID token")

    # 標準属性の抽出
    display_name = decoded_token.get("name")
    azure_user_id = decoded_token.get("sub")

    # 拡張属性の抽出
    company_id_key = f"extension_companyId"
    role_key = f"extension_role"
    company_id = decoded_token.get(company_id_key)
    user_role = decoded_token.get(role_key)

    if (access_token is None or company_id is None or user_role is None or display_name is None or azure_user_id is None):
        raise AppException(
            error_code=ErrorCode.INVALID_TOKEN,
            message="Invalid token format",
            context={"token_claims": list(decoded_token.keys())}
        )

    user_info: SignInResponse = SignInResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        name=display_name,
        azure_user_id=azure_user_id,
        company_id=company_id,
        role=user_role
    )
    
    logger.info("Token processed successfully")
    return user_info

async def exchange_refresh_token(refresh_token: str) -> str:
    """
    リフレッシュトークンを使用して新しいアクセストークンを取得する
    """
    tenant_name = settings.AZURE_TENANT_NAME
    client_id = settings.AZURE_CLIENT_ID
    client_secret = settings.AZURE_CLIENT_SECRET
    policy_name = settings.AZURE_B2C_SIGNIN_POLICY_NAME

    token_url = (
        f"https://{tenant_name}.b2clogin.com/"
        f"{tenant_name}.onmicrosoft.com/{policy_name}/oauth2/v2.0/token"
    )
    redirect_uri = f"https://inthub-fjfjehdsc4akamdg.japaneast-01.azurewebsites.net/api/v1/auth/signin/callback"

    payload = {
        "grant_type": "refresh_token",
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "redirect_uri": redirect_uri,
        "scope": f"openid offline_access https://{tenant_name}.onmicrosoft.com/{client_id}/read"
    }

    headers = {
        "Content-Type": "application/x-www-form-urlencoded"
    }

    response = requests.post(token_url, data=payload, headers=headers)
    try:
        response.raise_for_status()
    except requests.exceptions.HTTPError as e:
        logger.error(f"Failed to refresh token: {str(e)}")
        raise AppException(
            error_code=ErrorCode.INVALID_TOKEN,
            message="Failed to refresh token"
        )
    return response.json()

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
) -> CurrentUserResponse:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authorization header missing",
        )

    token = credentials.credentials
    decoded = await decode_and_verify_token(token, AuthType.SIGNIN)

    azure_user = AzureUser(
        name=decoded["name"],
        company_id=decoded["extension_companyId"],
        azure_user_id=decoded["sub"],
        role=decoded["extension_role"],
    )

    async with tenant_client_context_by_company_id(azure_user.company_id) as tenant_client:
        user_info = await tenant_client.companyuser.find_first(
            where={
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

    user = CurrentUserResponse(
        user_id=user_info.id,
        name=azure_user.name,
        company_id=azure_user.company_id,
        role=azure_user.role,
    )

    if not all([user.user_id, user.name, user.company_id, user.role]):
        raise AppException(
            error_code=ErrorCode.UNAUTHORIZED,
            message="Incomplete user information",
        )
    return user