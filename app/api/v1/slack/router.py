# app/api/v1/slack/router.py
# from __future__ import annotations

from fastapi import APIRouter, Body, Request, HTTPException, Depends, Form, BackgroundTasks, status, Query
from fastapi.responses import JSONResponse
from fastapi.security import HTTPBearer
import json
import asyncio
from typing import List, Dict, Set, Any
from datetime import datetime, UTC
import uuid
from contextlib import asynccontextmanager
import httpx
from uuid import UUID

from app.models.slack import *
from app.services.slack.slack_service import SlackService
from app.utils.decorators import catch_exceptions
from app.core.config import settings
from app.core.logging import get_logger
from app.services.company.company_service import CompanyService
from app.core.exceptions import AppException, ErrorCode
from app.models.slack import SlackInstallResponse
from app.services.slack.slack_install_service import SlackInstallService
from app.services.azure.key_vault import KeyVaultClient
from app.db.master_prisma.prisma import Prisma
from app.models.auth import CurrentUserResponse
from app.api.v1.auth.router import get_current_user

logger = get_logger(__name__)

@asynccontextmanager
async def lifespan(app):
    """アプリケーションのライフサイクル管理"""
    app.state.httpx_client = httpx.AsyncClient(timeout=10.0)
    yield
    await app.state.httpx_client.aclose()

router = APIRouter(lifespan=lifespan)
security = HTTPBearer()

async def get_httpx_client(request: Request) -> httpx.AsyncClient:
    """httpxクライアントを取得する依存関係"""
    return request.app.state.httpx_client

# -----------------------------------------------------------
# イベント受信エンドポイント
# -----------------------------------------------------------
@router.post("/events")
async def slack_events(
    request: Request,
    client: httpx.AsyncClient = Depends(get_httpx_client),
):
    try:
        raw = await request.body()
        logger.info(f"Received Slack event")

        logger.info(f"Verifying slack signature")
        SlackService.verify_slack_signature(request, raw)
        logger.info(f"Slack signature verified")
        body = json.loads(raw)

        # URL verification
        if "challenge" in body:
            logger.info("Handling URL verification challenge")
            return {"challenge": body["challenge"]}

        # リトライなら早期 200
        if request.headers.get("x-slack-retry-num"):
            logger.info("Received retry request, returning early")
            return {"ok": True}

        event = body.get("event", {})
        event_id = body.get("event_id")
        logger.info(f"Processing event, type: {event.get('type')}")

        if event_id and SlackService.is_duplicate(event_id):
            logger.info(f"Duplicate event detected: {event_id}")
            return {"ok": True}

        if event.get("type") == "app_mention":
            logger.info(f"Handling app mention: {event}")
            # 非同期で処理し、Slack へは即 ACK
            asyncio.create_task(SlackService.handle_mention(event, client))
    except Exception as e:
        logger.error(f"Error processing Slack event: {str(e)}", exc_info=True)
        raise
    return {"ok": True}

# -----------------------------------------------------------
# インタラクティブコンポーネント
# -----------------------------------------------------------
@router.post("/interactivity")
async def interactivity(
    request: Request,
    client: httpx.AsyncClient = Depends(get_httpx_client),
):
    try:
        logger.info("=== Interactivity Endpoint Called ===")
        # リクエストボディを1回だけ読み取る
        raw = await request.body()
        logger.info(f"Received raw payload: {raw.decode()}")
        
        # 署名の検証
        logger.info("Verifying Slack signature...")
        SlackService.verify_slack_signature(request, raw)
        logger.info("Signature verification successful")

        # フォームデータを解析
        form = await request.form()
        logger.info(f"Received form data: {form}")
        
        if "payload" not in form:
            logger.error("Payload not found in form data")
            raise HTTPException(status_code=400, detail="payload not found")

        payload = json.loads(form["payload"])
        logger.info(f"Parsed payload: {payload}")

        # 必須フィールドの確認
        if not payload.get("team", {}).get("id"):
            raise AppException(
                ErrorCode.BAD_REQUEST,
                message="team_idが見つかりません"
            )

        action = payload["actions"][0]
        if action.get("action_id") != "senpai_select":
            raise AppException(
                ErrorCode.BAD_REQUEST,
                message="不正なアクションIDです"
            )

        if not action.get("selected_option"):
            raise AppException(
                ErrorCode.BAD_REQUEST,
                message="選択された先輩が見つかりません"
            )

        # 必要な情報を抽出
        senpai = action["selected_option"]["value"]
        channel_id = payload["channel"]["id"]
        thread_ts = payload["message"]["ts"]
        team_id = payload["team"]["id"]
        meta = (
            payload.get("message", {})
            .get("metadata", {})
            .get("event_payload", {})
        )
        question = meta.get("question", "")
        thread_messages = meta.get("thread_messages", [])

        # 即座にokを返す
        response = JSONResponse({"ok": True})
        response.headers["X-Slack-No-Retry"] = "1"
        logger.info("Returning immediate OK response")

        # バックグラウンドで処理を実行
        async def process_in_background():
            try:
                # トークンを取得
                token = await KeyVaultClient.get_secret(name=f"slack-token-{team_id}")
                if not token:
                    raise AppException(
                        ErrorCode.SERVICE_UNAVAILABLE,
                        message="Slackトークンの取得に失敗しました"
                    )

                # 先輩のユーザー情報を取得
                logger.info(f"Fetching user info for senpai: {senpai}")
                user_resp = await client.get(
                    "https://slack.com/api/users.info",
                    params={"user": senpai},
                    headers={"Authorization": f"Bearer {token}"},
                )
                user_data = user_resp.json()
                if not user_data.get("ok"):
                    logger.error(f"Failed to get user info: {user_data.get('error')}")
                    senpai_name = senpai
                else:
                    user = user_data["user"]
                    senpai_name = user.get("real_name") or user.get("name") or senpai
                    logger.info(f"Retrieved senpai name: {senpai_name}")

                # 処理中メッセージ送信
                logger.info("Sending loading message...")
                loading = await client.post(
                    "https://slack.com/api/chat.postMessage",
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "channel": channel_id,
                        "text": f"*{senpai_name}* について回答を生成中です...",
                        "thread_ts": thread_ts,
                    },
                )
                loading.raise_for_status()
                message_ts = loading.json().get("ts")
                logger.info(f"Loading message sent successfully, ts: {message_ts}")

                # RAG処理を実行
                await SlackService.process_rag_and_update(
                    client=client,
                    channel_id=channel_id,
                    message_ts=message_ts,
                    senpai=senpai,
                    question=question,
                    thread_ts=thread_ts,
                    thread_messages=thread_messages,
                    team_id=team_id,
                )
            except Exception as e:
                logger.error(f"Error in background processing: {str(e)}", exc_info=True)
                # エラー時の処理はprocess_rag_and_update内で行われる

        # バックグラウンドタスクを開始
        asyncio.create_task(process_in_background())
        logger.info("Background processing task created successfully")

        return response

    except AppException as e:
        logger.error(f"Application error in interactivity: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=400,
            content={"ok": False, "error": e.message}
        )
    except Exception as e:
        logger.error(f"Unexpected error in interactivity: {str(e)}", exc_info=True)
        return JSONResponse(
            status_code=500,
            content={"ok": False, "error": "Internal server error"}
        )

@router.post("/commands", include_in_schema=True)
async def handle_slash_command(
    request: Request,
    token: str = Form(...),
    team_id: str = Form(...),
    team_domain: str = Form(...),
    channel_id: str = Form(...),
    channel_name: str = Form(...),
    user_id: str = Form(...),
    user_name: str = Form(...),
    command: str = Form(...),
    text: str = Form(None),
    response_url: str = Form(...),
    trigger_id: str = Form(...),
    api_app_id: str = Form(...),
    client: httpx.AsyncClient = Depends(get_httpx_client),
):
    """
    Slackのスラッシュコマンドを処理するエンドポイント
    """
    logger.info(f"Received slash command: command={command}, text={text}, user_id={user_id}, channel_id={channel_id}")

    # コマンドの検証
    if command not in ["/fetch-messages", "/reset-prompt", "/set-prompt"]:
        logger.warning(f"Invalid command received: {command}")
        return JSONResponse(
            content={"text": "無効なコマンドです"},
            status_code=400
        )

    # 即座にレスポンスを返す
    response = {
        "response_type": "ephemeral",
        "text": "コマンドを処理中です..."
    }

    # 非同期で処理を実行
    async def process_command(team_id: str):
        try:
            company_id = await SlackService.get_company_id_by_team_id(team_id)
            if command == "/fetch-messages":
                await client.post(
                    response_url,
                    json={
                        "response_type": "ephemeral",
                        "text": "メッセージの取得を開始しました。処理状況は後ほど通知されます。"
                    }
                )
                await SlackService.process_fetch_messages(
                    company_id=company_id,
                    team_id=team_id,
                    response_url=response_url,
                    client=client,
                    days=7
                )
            elif command == "/reset-prompt":
                await CompanyService.reset_prompt_template(company_id)
                await client.post(
                    response_url,
                    json={
                        "response_type": "ephemeral",
                        "text": "プロンプトをリセットしました。"
                    }
                )
            elif command == "/set-prompt":
                if not text:
                    await client.post(
                        response_url,
                        json={
                            "response_type": "ephemeral",
                            "text": "プロンプトの内容を指定してください。"
                        }
                    )
                    return
                await CompanyService.update_prompt_template(company_id, text)
                await client.post(
                    response_url,
                    json={
                        "response_type": "ephemeral",
                        "text": "プロンプトを更新しました。"
                    }
                )
        except Exception as e:
            logger.error(f"Error in command processing: {str(e)}", exc_info=True)
            await client.post(
                response_url,
                json={
                    "response_type": "ephemeral",
                    "text": "エラーが発生しました。管理者にお問い合わせください。"
                }
            )

    # 非同期処理を開始
    asyncio.create_task(process_command(team_id))

    # 即時レスポンスを返す
    return response

@router.post("/fetch")
@catch_exceptions
async def slack_fetch_messages(
    body: SlackFetchRequest = Body(...),
    client: httpx.AsyncClient = Depends(get_httpx_client),
):
    """
    Slack の会話を一括取得し、アーカイブ / RAG 取り込み用にCSV 化して保存するエンドポイント
    
    - adminロールのみが実行可能
    """
    try:
        # SlackServiceを使用してメッセージを取得
        result = await SlackService.process_fetch_messages(
            company_id=body.companyId,
            team_id=body.teamId,
            response_url=None,
            days=1,
            client=client
        )
        return JSONResponse(result)

    except Exception as e:
        error_msg = f"メッセージ取得中にエラーが発生しました: {str(e)}"
        logger.error(error_msg, exc_info=True)
        raise AppException(
            error_code=ErrorCode.SLACK_ERROR,
            message=error_msg
        )

@router.get("/authorize-url")
async def get_authorize_url(company_id: UUID = Query(...)) -> dict:
    """Slackの認証URLを取得する"""
    try:
        url = await SlackInstallService.get_authorize_url(company_id)
        return {"url": url}
    except Exception as e:
        logger.error(f"Error getting authorize URL: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="認証URLの取得に失敗しました"
        )

@router.get("/install")
@catch_exceptions
async def install_slack(
    code: str,
    state: str,
) -> SlackInstallResponse:
    """Slackアプリのインストールを処理する"""
    try:
        # stateからcompany_idを抽出
        state_parts = state.split('_')
        if len(state_parts) != 2:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid state format"
            )
        
        company_id = UUID(state_parts[1])
        logger.info(f"company_id: {company_id}")
        
        # stateの検証
        if not await SlackInstallService.verify_state(company_id, state):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid state"
            )
        
        # トークンの取得
        token_data = await SlackInstallService.exchange_code_for_token(code)
        logger.info(f"token_data: {token_data}")
        
        # ワークスペース情報の保存
        install_service = SlackInstallService()
        workspace = await install_service.save_workspace_info(
            company_id=str(company_id),
            token_data=token_data
        )
        
        return SlackInstallResponse(
            status="success",
            team_id=workspace["teamId"],
            message="Slackアプリのインストールが完了しました"
        )
    except AppException as e:
        logger.error(f"Installation error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Installation error: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
