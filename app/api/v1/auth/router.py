from fastapi import APIRouter
from app.core.exceptions import AppException, ErrorCode
from logging import getLogger
from app.services.auth import *
from app.models.auth import *
from app.utils.decorators import catch_exceptions
from fastapi import Query, Depends
from typing import Annotated

router = APIRouter()
logger = getLogger(__name__)

# GETリクエスト？ポストリクエスト？
@router.get("/signup/callback")
@catch_exceptions
async def signup_callback(code: str = Query(..., description="Azure AD B2C のサインアップ完了後に呼ばれるコールバックエンドポイント。クエリパラメータに含まれるIDトークンを受け取り、ユーザー属性を抽出して返却します。")):
    """
    Azure AD B2C のサインアップ完了後に呼ばれるコールバックエンドポイント。
    クエリパラメータに含まれるIDトークンを受け取り、ユーザー属性を抽出して返却します。
    """
    logger.info("Starting signup callback process")
    token_response = await exchange_code_for_token(code, "signup")
    id_token = token_response.get("id_token")
    decoded_token = await decode_and_verify_token(id_token, "signup")
    if not decoded_token:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="ID token is not valid"
        )
    user_info = await process_signup_callback(decoded_token)
    logger.info("Signup callback process completed successfully")
    return user_info

@router.get("/signin/callback")
@catch_exceptions
async def signin_callback(code: str = Query(..., description="Azure AD B2C のサインイン完了後に呼ばれるコールバックエンドポイント。クエリパラメータに含まれるIDトークンを受け取り、ユーザー属性を抽出して返却します。")):
    """
    Azure AD B2C のサインイン完了後に呼ばれるコールバックエンドポイント。
    クエリパラメータに含まれるIDトークンを受け取り、ユーザー属性を抽出して返却します。
    """
    logger.info("Starting signin callback process")
    token_response = await exchange_code_for_token(code, "signin")
    id_token = token_response.get("id_token")
    decoded_token = await decode_and_verify_token(id_token, "signin")
    if not decoded_token:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="ID token is not valid"
        )
    access_token = token_response.get("access_token")
    refresh_token = token_response.get("refresh_token")
    user_info = await process_signin_callback(decoded_token, access_token, refresh_token)
    logger.info("Signin callback process completed successfully")
    return user_info

@router.get("/me")
@catch_exceptions
async def me(current_user: Annotated[CurrentUserResponse, Depends(get_current_user)]):
    """
    現在ログインしているユーザーの情報を取得します。
    """
    return current_user

@router.post("/refresh-token")
@catch_exceptions
async def refresh_token(request_body: RefreshTokenRequest) -> RefreshTokenResponse:
    """
    リフレッシュトークンを使用して新しいアクセストークンを取得します。
    """
    refresh_token = request_body.refresh_token
    token_response = await exchange_refresh_token(refresh_token)
    new_access_token = token_response.get("access_token")
    new_refresh_token = token_response.get("refresh_token")
    id_token = token_response.get("id_token")

    if not new_access_token or not new_refresh_token or not id_token:
        raise AppException(
            error_code=ErrorCode.INVALID_REQUEST,
            message="Invalid token response"
        )
    
    return RefreshTokenResponse(
        access_token=new_access_token,
        refresh_token=new_refresh_token,
        id_token=id_token
    )
