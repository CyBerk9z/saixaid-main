from pydantic import BaseModel, Field
from datetime import datetime
from typing import List, Optional
from uuid import UUID

class SlackFetchRequest(BaseModel):
    teamId: str 
    companyId: str

class SlackEvent(BaseModel):
    """Slackイベントのモデル"""
    type: str
    user: str
    text: str
    ts: str
    channel: str
    thread_ts: Optional[str] = None

class SlackEventWrapper(BaseModel):
    """Slackイベントのラッパーモデル"""
    token: str
    challenge: Optional[str] = None
    type: str
    event_id: str
    event: Optional[SlackEvent] = None

class SlackInteractiveAction(BaseModel):
    """Slackインタラクティブアクションのモデル"""
    action_id: str
    selected_option: dict
    value: str

class SlackInteractivePayload(BaseModel):
    """Slackインタラクティブペイロードのモデル"""
    type: str
    actions: List[SlackInteractiveAction]
    channel: dict
    message: dict
    response_url: str 

class SlackAuthorizeUrlResponse(BaseModel):
    """Slack認証URLのレスポンスモデル"""
    url: str

class SlackWorkspace(BaseModel):
    """Slackワークスペースの情報を保持するモデル"""
    company_id: UUID
    team_id: str
    bot_user_id: str
    scopes: List[str]
    installed_at: datetime
    state: Optional[str] = None
    state_expires_at: Optional[datetime] = None

class SlackInstallResponse(BaseModel):
    """Slackインストールのレスポンスモデル"""
    status: str = Field(..., description="インストールの状態")
    team_id: str = Field(..., description="SlackチームID")
    message: str = Field(..., description="ステータスメッセージ") 
