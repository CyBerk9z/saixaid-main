import os
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import httpx

from app.core.config import settings
from app.utils.decorators import catch_exceptions

router = APIRouter()
security = HTTPBearer()

# Pydantic モデル：B2C ユーザー情報（カスタム属性込み）
class B2CUser(BaseModel):
    id: str
    displayName: Optional[str]
    userPrincipalName: Optional[str]
    companyId: Optional[str]
    role: Optional[str]

async def get_graph_token() -> str:
    """
    Client Credentials フローで Graph API 用アクセストークンを取得
    """
    token_url = f"https://login.microsoftonline.com/{settings.AZURE_TENANT_ID}/oauth2/v2.0/token"
    payload = {
        "client_id": settings.AZURE_CLIENT_ID,
        "scope": "https://graph.microsoft.com/.default",
        "client_secret": settings.AZURE_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    headers = {"Content-Type": "application/x-www-form-urlencoded"}

    async with httpx.AsyncClient() as client:
        resp = await client.post(token_url, data=payload, headers=headers)
        if resp.status_code != 200:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"Failed to acquire Graph token: {resp.text}" 
            )
        return resp.json()["access_token"]

async def fetch_all_b2c_users(graph_token: str) -> List[B2CUser]:
    """
    Graph API の /beta/users エンドポイントをページング処理しながら
    カスタム属性（companyId, role）込みで全ユーザーを取得
    """
    # App Client ID をハイフン除去して extension 名を生成
    extension_id = settings.AZURE_B2C_EXTENSION_ID.replace("-", "")
    select_fields = [
        "id",
        "displayName",
        "userPrincipalName",
        f"extension_{extension_id}_companyId",
        f"extension_{extension_id}_role"
    ]
    # 一度に取得する件数
    top = 999
    url = (
        f"https://graph.microsoft.com/beta/users"
        f"?$select={','.join(select_fields)}&$top={top}"
    )
    headers = {"Authorization": f"Bearer {graph_token}"}

    users: List[B2CUser] = []
    async with httpx.AsyncClient() as client:
        while url:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("value", []):
                users.append(
                    B2CUser(
                        id=item.get("id"),
                        displayName=item.get("displayName"),
                        userPrincipalName=item.get("userPrincipalName"),
                        companyId=item.get(f"extension_{extension_id}_companyId"),
                        role=item.get(f"extension_{extension_id}_role"),
                    )
                )
            # 次ページがあれば URL を更新、なければループ終了
            url = data.get("@odata.nextLink")
    return users

@router.get("/admin/b2c-users", response_model=List[B2CUser])
@catch_exceptions
async def list_b2c_users(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> List[B2CUser]:
    """
    Azure AD B2C の全ユーザーをカスタム属性付きで取得するエンドポイント
    - 認証済みリクエスト（HTTP Bearer）を想定
    - Graph API は Client Credentials フローで呼び出し
    """
    # (必要に応じてここで credentials を検証／role チェック)
    graph_token = await get_graph_token()
    users = await fetch_all_b2c_users(graph_token)
    return users
