from app.core.logging import get_logger
from app.models.chat import *
from app.services.rag.rag_service import RagService
from app.utils.db_client import tenant_client_context_by_company_id
import json
from typing import List
from app.core.exceptions import AppException
from app.core.exceptions import ErrorCode

logger = get_logger(__name__)

class ChatService:
    @staticmethod
    async def create_chat_session(request: ChatSessionCreateRequest, current_user: ChatUser) -> ChatSessionCreateResponse:
        """
        新しいチャットルームを作成し、ルーム情報を返す
        """
        async with tenant_client_context_by_company_id(current_user.company_id) as tenant_client:
            async with tenant_client.tx() as transaction:
                room_name = request.room_name or "Chat Room"

                chat_room = await transaction.chatroom.create(
                    data = {
                        "companyId": current_user.company_id,
                        "ownerId": current_user.user_id,
                        "roomName": room_name,
                    }
                )

                """
                if request.initial_message:
                    await transaction.chatmessage.create(
                        data = {
                            "roomId": chat_room.id,
                            "content": request.initial_message,
                            "metadata": json.dumps({
                                "role": "user",
                            })
                        }
                    )
                """

                return ChatSessionCreateResponse(
                    status="success",
                    session_id=chat_room.id,
                    message="Chat session created successfully"
                )

    @staticmethod
    async def send_message(request: ChatMessageSendRequest, current_user: ChatUser) -> ChatMessageSendResponse:
        """
        ユーザメッセージを受け取り、固定のシステム回答を生成して
        両方をチャットルームの会話履歴に保存し、更新後の履歴を返却する。
        
        処理の流れ:
          - セッションIDに紐づくチャットルームの存在確認
          - ユーザメッセージのDB保存
          - システム回答（固定 "メッセージを受け取りました")の生成とDB保存
          - チャットルームの全メッセージ（会話履歴）を取得し、更新コンテキストとして返却
        """

        async with tenant_client_context_by_company_id(current_user.company_id) as tenant_client:
            async with tenant_client.tx(timeout=20000) as transaction:
                # チャットルームの存在確認
                chat_room = await transaction.chatroom.find_unique(
                    where = {
                        "id": request.session_id,
                        "companyId": current_user.company_id,
                    }
                )

                if not chat_room:
                    raise AppException(
                        error_code="CHAT_ROOM_NOT_FOUND",
                        message="Chat session not found",
                        context = {
                            "session_id": request.session_id,
                        }
                    )

                # ユーザメッセージのDB保存
                await transaction.chatmessage.create(
                    data = {
                        "roomId": chat_room.id,
                        "isAssistant": False,
                        "senpaiId": None,
                        "content": request.user_message,
                        "metadata": json.dumps({})
                    }
                )

                # 先輩の名前取得
                senpai_name = None
                if request.senpai_id:
                    senpai_record = await transaction.senior.find_unique(where={"id": request.senpai_id})
                    if senpai_record:
                        senpai_name = senpai_record.name

                rag_query = f"{senpai_name}について教えて: {request.user_message}" if senpai_name else request.user_message

                # ---- 非同期でRAGを用いて回答を生成 ----
                rag_response =  await RagService.query_index(
                    query=rag_query,
                    company_id=current_user.company_id,
                    top_k=10
                )

                system_reply = rag_response["answer"]

                # BOTの回答を保存
                await transaction.chatmessage.create(
                    data = {
                        "roomId": chat_room.id,
                        "isAssistant": True,
                        "senpaiId": request.senpai_id,
                        "content": system_reply,
                        "metadata": json.dumps({})
                    }
                )

                return ChatMessageSendResponse(
                    status="success",
                    system_reply=system_reply,
                    system_score = rag_response["scores"],
                )

    @staticmethod
    async def end_chat_session(request: ChatSessionEndRequest, current_user: ChatUser) -> ChatSessionEndResponse:
        """
        指定されたセッションIDのチャットルームのステータスを "ended" に更新する
        """
        async with tenant_client_context_by_company_id(current_user.company_id) as tenant_client:
            async with tenant_client.tx() as transaction:
                updated_chat_room = await transaction.chatroom.update(
                    where = {
                        "id": request.session_id,
                        "companyId": current_user.company_id,
                    },
                    data = {
                        "status": "ended",
                    }
                )
                if not updated_chat_room:
                    raise AppException(
                        error_code="CHAT_ROOM_NOT_FOUND",
                        message="Chat session not found",
                        context = {
                            "session_id": request.session_id,
                        }
                    )
                return ChatSessionEndResponse(
                    status="success",
                    message="Chat session ended successfully"
                )

    @staticmethod
    async def list_chat_sessions(status: ChatSessionStatus, current_user: ChatUser) -> ChatSessionListResponse:
        """
        指定されたステータスのチャットルームを取得する
        """
        sessions_list: List[ChatSessionItem] = []

        async with tenant_client_context_by_company_id(current_user.company_id) as tenant_client:
            sessions = await tenant_client.chatroom.find_many(
                where = {
                    "companyId": current_user.company_id,
                    "ownerId": current_user.user_id,
                    "status": status,
                }
            )
            for session in sessions:
                sessions_list.append(ChatSessionItem(
                    session_id=session.id,
                    room_name=session.roomName,
                    status=ChatSessionStatus(session.status),
                    created_at=session.createdAt,
                ))
            return ChatSessionListResponse(
                status="success",
                sessions=sessions_list,
            )

    @staticmethod
    async def list_chat_messages(session_id: str, current_user: ChatUser) -> ChatMessageListResponse:
        """
        指定されたセッションIDのチャットルームのメッセージを取得する
        """
        messages_list: List[ChatMessageItem] = []

        async with tenant_client_context_by_company_id(current_user.company_id) as tenant_client:
            session = await tenant_client.chatroom.find_unique(
                where = {
                    "id": session_id,
                    "companyId": current_user.company_id,
                }
            )
            if not session:
                raise AppException(
                    error_code=ErrorCode.NOT_FOUND,
                    message="Chat session not found",
                    context = {
                        "session_id": session_id,
                    }
                )
            messages = await tenant_client.chatmessage.find_many(
                where = {
                    "roomId": session_id,
                },
                order = {
                    "createdAt": "asc",
                }
            )
            for message in messages:
                messages_list.append(ChatMessageItem(
                    message_id=message.id,
                    content=message.content,
                    is_assistant=message.isAssistant,
                    senpai_id=message.senpaiId,
                    created_at=message.createdAt,
                ))
            return ChatMessageListResponse(
                status="success",
                session_id=session_id,
                messages=messages_list,
            )
        
