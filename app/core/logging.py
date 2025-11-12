import sys
from loguru import logger
from app.core.config import settings

def set_up_logging():
    logger.remove()
    log_level = "INFO" if settings.ENVIRONMENT.lower() == "production" else "DEBUG"

    # 標準出力へのログ出力設定
    logger.add(
        sys.stdout,
        level=log_level,
        format="<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        filter=lambda record: "request_line" not in record["extra"] or "/api/v1/auth/" not in record["extra"]["request_line"]
    )

    # エラーログの設定
    logger.add(
        sys.stderr,
        level="ERROR",
        format="<red>{time:YYYY-MM-DD HH:mm:ss.SSS}</red> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        backtrace=True,
        diagnose=True
    )

    # 警告ログの設定
    logger.add(
        sys.stderr,
        level="WARNING",
        format="<yellow>{time:YYYY-MM-DD HH:mm:ss.SSS}</yellow> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>"
    )

    # クリティカルログの設定
    logger.add(
        sys.stderr,
        level="CRITICAL",
        format="<red>{time:YYYY-MM-DD HH:mm:ss.SSS}</red> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        backtrace=True,
        diagnose=True
    )

def get_logger(name: str):
    """
    アプリケーション全体で使用するロガーを取得する
    
    Args:
        name (str): ロガーの名前（通常はモジュール名）
    
    Returns:
        Logger: 設定済みのロガーインスタンス
    """
    return logger.bind(name=name)

def log_exception(logger, exc: Exception, context: dict = None):
    """
    例外をログに記録する
    
    Args:
        logger: ロガーインスタンス
        exc (Exception): 記録する例外
        context (dict, optional): 追加のコンテキスト情報
    """
    if context is None:
        context = {}
    
    logger.exception(
        f"Exception occurred: {str(exc)}",
        extra={
            "custom_dimensions": {
                "exception_type": exc.__class__.__name__,
                "exception_message": str(exc),
                **context
            }
        }
    )
