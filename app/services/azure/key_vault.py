from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient
from app.core.config import settings
from app.core.logging import get_logger
import asyncio
from app.core.exceptions import AppException, ErrorCode

logger = get_logger(__name__)

class KeyVaultClient:
    """Azure Key Vaultクライアント"""

    @staticmethod
    def _get_client() -> SecretClient:
        """Key Vaultクライアントを取得する"""
        try:
            credential = DefaultAzureCredential()
            client = SecretClient(
                vault_url=f"https://{settings.AZURE_KEY_VAULT_NAME}.vault.azure.net/",
                credential=credential,
                location="japaneast"  # リージョンをjapaneastに設定
            )
            return client
        except Exception as e:
            logger.error(f"Failed to initialize Key Vault client: {e}")
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message="Key Vaultクライアントの初期化に失敗しました",
            )

    @staticmethod
    async def set_secret(name: str, value: str) -> None:
        """シークレットを設定します"""
        try:
            client = KeyVaultClient._get_client()
            # 非同期処理を同期的に実行
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: client.set_secret(name, value)
            )
            logger.info(f"Secret {name} set successfully")
        except Exception as e:
            logger.error(f"Failed to set secret {name}: {str(e)}")
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"シークレットの設定に失敗しました: {name}",
            )

    @staticmethod
    async def get_secret(name: str) -> str:
        """シークレットを取得する"""
        try:
            client = KeyVaultClient._get_client()
            # 非同期処理を同期的に実行
            loop = asyncio.get_event_loop()
            secret = await loop.run_in_executor(
                None,
                lambda: client.get_secret(name)
            )
            return secret.value
        except Exception as e:
            logger.error(f"Failed to get secret {name}: {str(e)}", exc_info=True)
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"シークレットの取得に失敗しました: {name}",
            )

    @staticmethod
    async def delete_secret(name: str) -> None:
        """シークレットを削除する"""
        try:
            client = KeyVaultClient._get_client()
            # 非同期処理を同期的に実行
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: client.begin_delete_secret(name)
            )
            logger.info(f"Secret {name} deleted successfully")
        except Exception as e:
            logger.error(f"Failed to delete secret {name}: {e}")
            raise AppException(
                ErrorCode.INTERNAL_SERVER_ERROR,
                message=f"シークレットの削除に失敗しました: {name}",
            )