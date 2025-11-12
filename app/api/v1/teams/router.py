# app/api/v1/teams/router.py
from fastapi import APIRouter, Request, HTTPException, Depends
from fastapi.responses import JSONResponse
import asyncio

from app.services.teams.teams_service import TeamsService
from app.core.logging import get_logger

logger = get_logger(__name__)
router = APIRouter()

@router.post("/messages", tags=["teams"])
async def handle_teams_message(request: Request):
    """
    Microsoft Teamsからのメッセージを受信するエンドポイント
    """
    # 認証ヘッダーの取得
    auth_header = request.headers.get("Authorization", "")
    
    # リクエストボディの取得
    body = await request.json()
    logger.info(f"Received Teams activity: {body.get('type')}")

    try:
        # 認証処理 (TeamsServiceに実装)
        await TeamsService.verify_request(auth_header, body)

        # 即時応答
        response = JSONResponse(content={})
        
        # バックグラウンドで処理を実行
        asyncio.create_task(TeamsService.handle_activity(body))
        
        logger.info("Returning immediate 200 OK to Teams.")
        return response

    except HTTPException as e:
        logger.error(f"Authentication failed: {e.detail}")
        raise
    except Exception as e:
        logger.error(f"Error processing Teams message: {str(e)}", exc_info=True)
        # Teamsはエラー時に500番台を返すとリトライを試みるため、
        # ログ記録後は200 OKを返すのが望ましい場合がある
        return JSONResponse(content={"status": "error", "message": "Internal Server Error"})
