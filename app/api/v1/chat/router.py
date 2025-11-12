from fastapi import APIRouter, Depends, Query, Security, HTTPException
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from app.services.chat.chat_service import ChatService
from app.models.chat import *
from app.services.auth.auth_service import get_current_user
from app.utils.decorators import catch_exceptions

router = APIRouter()
security = HTTPBearer()

@router.post("/sessions", response_model=ChatSessionCreateResponse)
@catch_exceptions
async def create_chat_room(
    request_body: ChatSessionCreateRequest,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    チャットセッション作成エンドポイント
      - Authorization ヘッダーからアクセストークンを取得し、ユーザ情報を検証
      - 検証されたユーザ情報をもとにチャットセッションを作成
    """
    current_user = await get_current_user(credentials)
    response = await ChatService.create_chat_session(
        request=request_body,
        current_user=current_user,
    )
    return response

@router.post("/messages", response_model=ChatMessageSendResponse)
@catch_exceptions
async def send_message(
    request_body: ChatMessageSendRequest,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    チャットメッセージ送信エンドポイント
      - セッションID(チャットルームID)の存在確認と会話履歴取得
      - RAG検索およびAzure OpenAIで回答生成
      - ユーザメッセージとシステム回答をDBに保存し、更新後の会話履歴を返却する
    """
    current_user = await get_current_user(credentials)
    response = await ChatService.send_message(
        request=request_body,
        current_user=current_user,
    )
    return response

@router.post("/sessions/end", response_model=ChatSessionEndResponse)
@catch_exceptions
async def end_chat_session(
    request_body: ChatSessionEndRequest,
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    チャットセッション終了エンドポイント
        - 指定されたセッションIDのチャットルームのステータスを "ended" に更新し、セッション終了処理を行う
    """
    current_user = await get_current_user(credentials)
    response = await ChatService.end_chat_session(
        request=request_body,
        current_user=current_user,
    )
    return response

@router.get("/sessions", response_model=ChatSessionListResponse)
@catch_exceptions
async def list_chat_sessions(
    status: ChatSessionStatus = Query(None, description="チャットルームのステータス（active または ended）"),
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    チャットセッション一覧取得エンドポイント
        - 指定されたステータスのチャットルームを取得する
        - ステータスは active または ended のみ有効
    """
    if status and status not in [ChatSessionStatus.ACTIVE, ChatSessionStatus.ENDED]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid status. Must be either 'active' or 'ended'"
        )

    current_user = await get_current_user(credentials)
    response = await ChatService.list_chat_sessions(
        status=status,
        current_user=current_user,
    )
    return response

@router.get("/messages", response_model=ChatMessageListResponse)
@catch_exceptions
async def list_chat_messages(
    session_id: str = Query(..., description="チャットルームID", min_length=1),
    credentials: HTTPAuthorizationCredentials = Security(security),
):
    """
    チャットメッセージ一覧取得エンドポイント
        - 指定されたセッションIDのチャットルームのメッセージを取得する
    """
    current_user = await get_current_user(credentials)
    response = await ChatService.list_chat_messages(
        session_id=session_id,
        current_user=current_user,
    )
    return response