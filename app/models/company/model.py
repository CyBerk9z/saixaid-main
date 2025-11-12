from pydantic import BaseModel, Field
from typing import Dict, Any, List, Optional
from datetime import datetime
from uuid import UUID

class RegisterCompany(BaseModel) :
    company_name: str
    company_server_name: str

class RegisterCompanyUser(BaseModel):
    company_id: str
    user_name: str
    user_email: str
    user_role: str
    azure_user_id: str

class TenantInfo(BaseModel):
    id: UUID
    company_name: str
    company_id: UUID
    company_server_name: str
    created_at: datetime
    updated_at: datetime

class TenantInfoFromTenant(BaseModel):
    id: UUID
    company_name: str
    company_server_name: str
    created_at: datetime
    updated_at: datetime

class TenantInfoResponse(BaseModel):
    status: str
    tenant: TenantInfoFromTenant

class TenantListResponse(BaseModel):
    tenants: List[TenantInfo]
    status: str

class SignInCompanyUser(BaseModel):
    company_id: str
    user_email: str
    user_password: str

class CompanyRegisterResponse(BaseModel):
    """会社登録レスポンス"""
    company_id: str
    status: str
    message: str

class CompanyUserRegisterResponse(BaseModel):
    user_id: str
    status: str

class CompanyUserSignInResponse(BaseModel):
    user_id: str
    access_token: str
    status: str

class RegisterSenpaiRequest(BaseModel):
    senpai_name: str
    profile: Dict[str, Any]

class RegisterSenpaiResponse(BaseModel):
    status: str
    message: str
    senpai_id: str

class GetSenpaiDetailResponse(BaseModel):
    senpai_id: str
    senpai_name: str
    profile: Dict[str, Any]
    created_at: datetime

class SenpaiListResponse(BaseModel):
    status: str
    senpais: List[GetSenpaiDetailResponse]

class UploadDataResponse(BaseModel):
    status: str
    message: str
    fileId: str 

class RestoreDataResponse(BaseModel):
    status: str
    message: str
    restored_files: List[Dict[str, Any]]

class GetFileStatusRequest(BaseModel):
    file_id: str

class DeleteDataResponse(BaseModel):
    status: str
    message: str

class DeleteDataRequest(BaseModel):
    file_id: str

class B2CProvisionRequest(BaseModel):
    extension_companyId: str
    sub: str
    email: str
    displayName: str
    extension_role: str

class GetPromptResponse(BaseModel):
    prompt: str

class CreateInviteCodeRequest(BaseModel):
    expires_in_days: int = 7  # デフォルトで7日間有効

class InviteCodeResponse(BaseModel):
    token: str
    expires_at: datetime
    company_id: str

class AllowedDomainsUpdateRequest(BaseModel):
    """許可ドメイン更新リクエスト"""
    allowed_domains: list[str] = Field(
        ...,
        description="許可するドメインのリスト（例：['@company.com', '@example.com']）",
        min_items=0
    )

class AllowedDomainsUpdateResponse(BaseModel):
    status: str
    message: str

class AllowedDomainsResponse(BaseModel):
    status: str
    allowed_domains: list[str]

class SlackWorkspaceInfo(BaseModel):
    """Slackワークスペース情報"""
    id: str = Field(..., description="SlackワークスペースID")
    team_id: str = Field(..., description="SlackチームID")
    company_id: str = Field(..., description="会社ID")
    created_at: datetime = Field(..., description="作成日時")
    updated_at: datetime = Field(..., description="更新日時")

class SlackWorkspaceListResponse(BaseModel):
    """Slackワークスペース一覧レスポンス"""
    status: str = Field(..., description="ステータス")
    workspaces: list[SlackWorkspaceInfo] = Field(..., description="Slackワークスペース一覧")
