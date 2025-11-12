from enum import Enum
from typing import Optional, Dict, Any
from fastapi import HTTPException, status
from app.core.logging import get_logger, log_exception
from fastapi.responses import JSONResponse

logger = get_logger(__name__)

class ErrorCode(Enum):
    """
    エラーコードとデフォルトの設定を定義
    """
    # データベース関連
    DATABASE_ERROR = ("Database operation failed", status.HTTP_500_INTERNAL_SERVER_ERROR)
    DUPLICATE_ENTRY = ("Resource already exists", status.HTTP_409_CONFLICT)

    # 入力検証関連
    INVALID_REQUEST = ("Invalid request parameters", status.HTTP_400_BAD_REQUEST)
    VALIDATION_ERROR = ("Validation error", status.HTTP_422_UNPROCESSABLE_ENTITY)

    # 認証・認可関連
    AUTHENTICATION_ERROR = ("Authentication failed", status.HTTP_401_UNAUTHORIZED)
    AUTHORIZATION_ERROR = ("Authorization failed", status.HTTP_403_FORBIDDEN)
    INVALID_TOKEN = ("Invalid token", status.HTTP_401_UNAUTHORIZED)

    # リソース関連
    NOT_FOUND = ("Resource not found", status.HTTP_404_NOT_FOUND)

    # システムエラー
    INTERNAL_SERVER_ERROR = ("Internal server error", status.HTTP_500_INTERNAL_SERVER_ERROR)
    SERVICE_UNAVAILABLE = ("Service unavailable", status.HTTP_503_SERVICE_UNAVAILABLE)
    CONFIG_ERROR = ("Configuration error", status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Slack関連
    SLACK_ERROR = ("Slack operation failed", status.HTTP_500_INTERNAL_SERVER_ERROR)
    SLACK_TOKEN_ERROR = ("Slack token error", status.HTTP_401_UNAUTHORIZED)
    SLACK_CHANNEL_ERROR = ("Slack channel error", status.HTTP_404_NOT_FOUND)
    SLACK_USER_ERROR = ("Slack user error", status.HTTP_404_NOT_FOUND)
    SLACK_MESSAGE_ERROR = ("Slack message error", status.HTTP_400_BAD_REQUEST)
    SLACK_POST_ERROR = ("Slack post error", status.HTTP_400_BAD_REQUEST)
    SLACK_UPDATE_ERROR = ("Slack update error", status.HTTP_400_BAD_REQUEST)

    INVALID_INVITE_CODE = ("Invalid invite code", status.HTTP_400_BAD_REQUEST)
    INVALID_DOMAIN_FORMAT = ("Invalid domain format", status.HTTP_400_BAD_REQUEST)

    def __init__(self, default_message:str, status_code: int):
        self.default_message = default_message
        self.status_code = status_code

class AppException(Exception):
    """
    アプリケーション共通の例外クラス
    """
    def __init__(
        self,
        error_code: ErrorCode,
        message: Optional[str] = None,
        status_code: Optional[int] = None,
        context: Optional[Dict[str, Any]] = None
    ):
        self.error_code = error_code
        self.message = message or error_code.default_message
        self.status_code = status_code or error_code.status_code
        self.context = context or {}
        super().__init__(self.message)

    def __str__(self):
        return f"[{self.error_code}] {self.message}"

    def to_dict(self) -> Dict[str, Any]:
        """
        例外情報を辞書形式で返す
        """
        return {
            "error_code": self.error_code.name,
            "error_message": self.message,
            "status_code": self.status_code,
            "details": self.context if self.context else None
        }

def handle_app_exception(e: AppException):
    """
    発生した例外をログ出力し、HTTPExceptionとして返す
    """
    # 例外の詳細情報をログに記録
    log_exception(
        logger,
        e,
        context={
            "error_code": e.error_code.name,
            "status_code": e.status_code,
            "context": e.context
        }
    )

    # HTTPExceptionとして返す
    return JSONResponse(
        status_code=e.status_code,
        content=e.to_dict()
    )

def handle_unexpected_exception(e: Exception):
    """
    予期しない例外を処理する
    """
    # 予期しない例外をログに記録
    log_exception(
        logger,
        e,
        context={
            "error_type": "UNEXPECTED_ERROR",
            "error_message": str(e)
        }
    )

    # 内部サーバーエラーとして返す
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail={
            "error_code": "INTERNAL_SERVER_ERROR",
            "error_message": "An unexpected error occurred",
            "details": None
        }
    )

