# app/services/teams/teams_service.py
from __future__ import annotations
from typing import Dict, Any
from fastapi import HTTPException, status

from botbuilder.core import (
    TurnContext,
    MessageFactory,
    BotFrameworkAdapter,
)
from botbuilder.core.authentication import (
    BotFrameworkAdapterSettings,
    JwtTokenValidation,
    SimpleCredentialProvider,
)
from botbuilder.schema import Activity, ChannelAccount

from app.core.config import settings
from app.core.logging import get_logger
from app.services.rag.rag_service import RagService # RAGサービスをインポート

logger = get_logger(__name__)

# Bot Frameworkの認証設定
CREDENTIAL_PROVIDER = SimpleCredentialProvider(
    app_id=settings.MICROSOFT_APP_ID,
    app_password=settings.MICROSOFT_APP_PASSWORD
)

# Bot Framework Adapterの設定
ADAPTER = BotFrameworkAdapter(CREDENTIAL_PROVIDER)

class TeamsService:
    @staticmethod
    async def verify_request(auth_header: str, body: Dict[str, Any]) -> None:
        """
        Teamsからのリクエストの正当性を検証する
        """
        logger.info("Verifying request from Teams...")
        try:
            activity = Activity().deserialize(body)
            auth_claims = await JwtTokenValidation.validate_auth_header(
                auth_header=auth_header,
                credential_provider=CREDENTIAL_PROVIDER,
                channel_id=activity.channel_id,
                service_url=activity.service_url
            )

            if not auth_claims:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid authentication claims"
                )
            
            logger.info("Request verification successful.")

        except Exception as e:
            logger.error(f"Request verification failed: {e}", exc_info=True)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Request verification failed"
            )

    @staticmethod
    async def handle_activity(activity_body: Dict[str, Any]) -> None:
        """
        TeamsからのActivityを処理する
        """
        activity = Activity().deserialize(activity_body)
        logger.info(f"Handling activity type: {activity.type}")
        
        async def turn_handler(turn_context: TurnContext):
            if turn_context.activity.type == "message":
                text = turn_context.activity.text
                logger.info(f"Received message: {text}")

                # BOTへのメンションを検出
                if turn_context.activity.recipient.id in text:
                    # メンション部分を除去してクエリを抽出
                    query = text.replace(turn_context.activity.recipient.id, "").strip()
                    logger.info(f"Extracted query: {query}")

                    if not query:
                        await turn_context.send_activity(MessageFactory.text("何か質問がありますか？"))
                        return

                    try:
                        # TODO: company_idの取得ロジックを実装
                        # 現時点では仮のcompany_idを使用
                        # SlackService.get_company_id_by_team_id(team_id) のようなロジックが必要
                        company_id = "your_company_id_here" # 仮のcompany_id

                        # RAGサービスを呼び出し (一時的にエコー応答に置き換え)
                        # logger.info(f"Calling RAG service with query: {query}")
                        # rag_response = await RagService.query_index(query=query, company_id=company_id, top_k=10)
                        # answer = rag_response.get("answer", "申し訳ありません、回答が見つかりませんでした。")
                        
                        answer = f"エコー: {query}" # 一時的なエコー応答
                        await turn_context.send_activity(MessageFactory.text(answer))
                        logger.info("Echo response sent to Teams (RAG temporarily bypassed).")

                    except Exception as e:
                        logger.error(f"Error during RAG processing: {e}", exc_info=True)
                        await turn_context.send_activity(MessageFactory.text("申し訳ありません、回答の生成中にエラーが発生しました。"))

                else:
                    # メンションがない場合は無視するか、別の処理を行う
                    logger.info("Message without bot mention. Ignoring for now.")

            elif turn_context.activity.type == "conversationUpdate":
                # BOTがチームやチャットに追加された際の処理
                for member in turn_context.activity.members_added:
                    if member.id == turn_context.activity.recipient.id:
                        # BOT自身が追加された場合
                        await turn_context.send_activity(MessageFactory.text("こんにちは！Teams BOTへようこそ。私にメンションして質問してください。"))
                        logger.info("Welcome message sent on bot added.")

            else:
                logger.info(f"Ignoring activity type: {turn_context.activity.type}")

        # TurnContextを作成し、アダプターで処理
        turn_context = TurnContext(ADAPTER, activity)
        await ADAPTER.process_activity(activity, turn_handler)
