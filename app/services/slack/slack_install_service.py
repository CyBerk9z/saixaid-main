from datetime import datetime, timedelta, UTC, timezone
from typing import Dict, Any
from uuid import UUID
import uuid
import httpx

from app.core.exceptions import AppException, ErrorCode
from app.core.config import settings
from app.core.logging import get_logger
from app.services.azure.key_vault import KeyVaultClient
from app.db.master_prisma.prisma import Prisma
from app.utils.db_client import tenant_client_context_by_company_id


logger = get_logger(__name__)

class SlackInstallService:
    _STATE_TTL = timedelta(minutes=10)  # stateの有効期限

    @staticmethod
    async def generate_state(company_id: UUID) -> str:
        """CSRFトークンを生成し、DBに保存する"""
        try:
            # company_idを含むstateを生成
            state = f"{str(uuid.uuid4())}_{str(company_id)}"
            expires_at = datetime.now(UTC) + SlackInstallService._STATE_TTL
            
            async with Prisma() as db:
                await db.slackinstallstate.create(
                    data={
                        "companyId": str(company_id),
                        "state": state,
                        "expiresAt": expires_at
                    }
                )
            
            return state
        except Exception as e:
            logger.error(f"Failed to generate state: {e}")
            raise AppException(
                ErrorCode.DATABASE_ERROR,
                message="Failed to execute operation with client"
            )

    @staticmethod
    async def get_authorize_url(company_id: UUID) -> str:
        """Slackの認証URLを生成する"""
        try:
            scopes = [
                "app_mentions:read",
                "channels:history",
                "channels:read",
                "chat:write",
                "commands",
                "groups:history",
                "groups:read",
                "im:history",
                "im:read",
                "mpim:history",
                "mpim:read",
                "users:read",
                "channels:join"
            ]
            
            # CSRF対策用のstateを生成
            state = await SlackInstallService.generate_state(company_id)
            
            query_params = {
                "client_id": settings.SLACK_CLIENT_ID,
                "scope": ",".join(scopes),
                "user_scope": "",
                "state": state,
                "redirect_uri": f"{settings.SLACK_REDIRECT_URI}"
            }
            
            query_string = "&".join(f"{k}={v}" for k, v in query_params.items())
            return f"https://slack.com/oauth/v2/authorize?{query_string}"
        except Exception as e:
            logger.error(f"Failed to generate authorize URL: {e}")
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message="Failed to generate authorize URL"
            )

    @staticmethod
    async def verify_state(company_id: UUID, state: str) -> bool:
        """stateの検証を行う"""
        try:
            # stateからcompany_idを抽出
            state_parts = state.split('_')
            if len(state_parts) != 2:
                return False
            
            state_uuid, state_company_id = state_parts
            if state_company_id != str(company_id):
                return False

            async with Prisma() as db:
                # 有効期限が切れていないstateを検索
                install_state = await db.slackinstallstate.find_first(
                    where={
                        "companyId": str(company_id),
                        "state": state,
                        "expiresAt": {
                            "gt": datetime.now(UTC)
                        }
                    }
                )
                
                if install_state:
                    # 検証済みのstateは削除
                    await db.slackinstallstate.delete(
                        where={
                            "id": install_state.id
                        }
                    )
                    return True
                
                return False
        except Exception as e:
            logger.error(f"Error verifying state: {e}", exc_info=True)
            return False

    @staticmethod
    async def exchange_code_for_token(code: str) -> Dict[str, Any]:
        """認可コードをアクセストークンに交換する"""
        try:
            async with httpx.AsyncClient() as client:
                response = await client.post(
                    "https://slack.com/api/oauth.v2.access",
                    data={
                        "client_id": settings.SLACK_CLIENT_ID,
                        "client_secret": settings.SLACK_CLIENT_SECRET,
                        "code": code,
                        "redirect_uri": f"{settings.SLACK_REDIRECT_URI}"
                    }
                )
                
                if not response.status_code == 200:
                    raise AppException(
                        ErrorCode.SERVICE_UNAVAILABLE,
                        message="Slack APIへのリクエストに失敗しました"
                    )
                
                data = response.json()
                if not data.get("ok"):
                    raise AppException(
                        ErrorCode.SERVICE_UNAVAILABLE,
                        message=f"Slack APIエラー: {data.get('error', '不明なエラー')}"
                    )
                
                return data
        except Exception as e:
            logger.error(f"Error exchanging code for token: {e}", exc_info=True)
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message="トークンの取得に失敗しました"
            )

    async def save_workspace_info(
        self, company_id: str, token_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Slackワークスペース情報を保存"""
        try:
            logger.info(f"ワークスペース情報の保存を開始: company_id={company_id}, team_id={token_data['team']['id']}")
            
            # トークンをKey Vaultに保存
            secret_name = f"slack-token-{token_data['team']['id']}"
            logger.info(f"Key Vaultにトークンを保存します: secret_name={secret_name}")
            try:
                await KeyVaultClient.set_secret(
                    name=secret_name,
                    value=token_data["access_token"]
                )
                logger.info(f"Key Vaultへのトークン保存が完了しました: secret_name={secret_name}")
            except Exception as e:
                logger.error(f"Key Vaultへのトークン保存に失敗: {str(e)}", exc_info=True)
                raise AppException(
                    ErrorCode.INTERNAL_SERVER_ERROR,
                    message="Key Vaultへのトークン保存に失敗しました。アクセス権限を確認してください。",
                )

            # マスターデータベースにワークスペース情報を保存
            async with Prisma() as db:
                # テナント情報を取得
                logger.info(f"テナント情報を検索します: company_id={company_id}")
                tenant = await db.tenants.find_first(
                    where={"companyId": company_id}
                )
                if not tenant:
                    logger.error(f"テナント情報が見つかりません: company_id={company_id}")
                    raise AppException(
                        ErrorCode.NOT_FOUND,
                        message="テナント情報が見つかりません",
                    )
                logger.info(f"テナント情報を取得しました: tenant_id={tenant.id}")

                # ワークスペース情報を保存
                workspace_data = {
                    "tenantId": str(tenant.id),
                    "teamId": token_data["team"]["id"],
                    "botUserId": token_data["bot_user_id"],
                    "scopes": token_data["scope"].split(","),
                    "installedAt": datetime.now(timezone.utc),
                }
                logger.info(f"ワークスペース情報を保存します: team_id={token_data['team']['id']}, bot_user_id={token_data['bot_user_id']}")
                workspace = await db.slackworkspace.create(data=workspace_data)
                logger.info(f"Slackワークスペース情報を保存しました: workspace_id={workspace.id}, team_id={workspace.teamId}")

                response_data = {
                    "id": str(workspace.id),
                    "teamId": workspace.teamId,
                    "botUserId": workspace.botUserId,
                    "scopes": workspace.scopes,
                    "installedAt": workspace.installedAt,
                }
                logger.info(f"ワークスペース情報の保存が完了しました: workspace_id={workspace.id}")
                return response_data

        except Exception as e:
            logger.error(
                f"ワークスペース情報の保存に失敗: company_id={company_id}, team_id={token_data.get('team', {}).get('id')}, error={str(e)}",
                exc_info=True
            )
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message="ワークスペース情報の保存に失敗しました",
            ) 