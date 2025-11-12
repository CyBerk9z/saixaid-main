from pydantic import BaseModel
from enum import Enum
from datetime import datetime

class ChatSessionCreateRequest(BaseModel):
    room_name: str | None = None

class ChatSessionCreateResponse(BaseModel):
    status: str
    session_id: str
    message: str

class ChatAzureUser(BaseModel):
    azure_user_id: str
    name: str
    company_id: str
    role: str

class ChatUser(BaseModel):
    user_id: str
    name: str
    company_id: str
    role: str

class ChatMessageSendRequest(BaseModel):
    session_id: str
    user_message: str
    senpai_id: str

class ChatMessageSendResponse(BaseModel):
    status: str
    system_reply: str
    system_score: list

class ChatSessionEndRequest(BaseModel):
    session_id: str

class ChatSessionEndResponse(BaseModel):
    status: str
    message: str

class ChatSessionStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"

class ChatSessionItem(BaseModel):
    session_id: str
    room_name: str
    status: ChatSessionStatus
    created_at: datetime

class ChatSessionListResponse(BaseModel):
    status: str
    sessions: list[ChatSessionItem]

class ChatMessageListRequest(BaseModel):
    session_id: str

class ChatMessageItem(BaseModel):
    message_id: str
    content: str
    is_assistant: bool
    senpai_id: str | None
    created_at: datetime

class ChatMessageListResponse(BaseModel):
    status: str
    session_id: str
    messages: list[ChatMessageItem]
