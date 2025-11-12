import sys
import time
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from app.api.v1.system.router import router as system_router
from app.api.v1.chat.router import router as chat_router
from app.api.v1.company.router import router as company_router
from app.api.v1.auth.router import router as auth_router
from app.api.v1.rag.router import router as rag_router
from app.api.v1.users.router import router as user_router
from app.api.v1.internal.router import router as internal_router
from app.api.v1.slack.router import router as slack_router
from app.api.v1.teams.router import router as teams_router
from app.api.v1.meeting.router import router as meeting_router
from app.core.exceptions import AppException, handle_app_exception, handle_unexpected_exception
from app.core.logging import get_logger, set_up_logging


# ロガーの設定
logger = get_logger(__name__)

# FastAPIアプリケーションの作成
app = FastAPI(
    title="Inthub API",
    description="Inthub API Documentation",
    version="1.0.0"
)

# ロギングの設定
set_up_logging()

# ヘルスチェックエンドポイント
@app.get("/")
async def health_check():
    """
    ヘルスチェックエンドポイント
    Azure App Serviceのヘルスチェック用
    """
    return {"status": "healthy"}

# 例外ハンドラーの設定
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return handle_app_exception(exc)

@app.exception_handler(Exception)
async def unexpected_exception_handler(request: Request, exc: Exception):
    return handle_unexpected_exception(exc)

# ミドルウェアの設定
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start_time = time.time()
    
    # リクエスト開始時のログ
    """
    logger.info(
        "Request started",
        extra={
            "custom_dimensions": {
                "method": request.method,
                "path": request.url.path,
                "client_host": request.client.host if request.client else None,
                "query_params": str(request.query_params),
                "headers": {k: v for k, v in request.headers.items() if k.lower() not in ['authorization', 'cookie']}
            }
        }
    )
    """
    
    try:
        response = await call_next(request)
        process_time = time.time() - start_time
        
        # レスポンス時のログ
        """
        logger.info(
            "Request completed",
            extra={
                "custom_dimensions": {
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": response.status_code,
                    "process_time_ms": round(process_time * 1000, 2)
                }
            }
        )
        """
        return response
    except Exception as e:
        process_time = time.time() - start_time
        
        # エラー時のログ
        logger.error(
            "Request failed",
            extra={
                "custom_dimensions": {
                    "method": request.method,
                    "path": request.url.path,
                    "error": str(e),
                    "process_time_ms": round(process_time * 1000, 2)
                }
            }
        )
        raise

# ルーターの設定
# 認証系
app.include_router(auth_router, prefix="/api/v1/auth")

# テナント系
app.include_router(company_router, prefix="/api/v1/company")

# チャット系
app.include_router(chat_router, prefix="/api/v1/chat")

# システム系
app.include_router(system_router, prefix="/api/v1/system")

# RAG系
app.include_router(rag_router, prefix="/api/v1/rag")

# 内部系
app.include_router(internal_router, prefix="/api/v1/internal")

# Slack系
app.include_router(slack_router, prefix="/api/v1/slack")

# Teams系
app.include_router(teams_router, prefix="/api/v1/teams")

# ミーティング系
app.include_router(meeting_router, prefix="/api/v1/meeting")

# ユーザー系
#app.include_router(user_router, prefix="/api/v1/user")

# 起動コマンド
# uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
