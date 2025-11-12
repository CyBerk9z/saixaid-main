from fastapi import APIRouter, File, UploadFile, Depends
from app.core.logging import get_logger
from app.models.company import *
from app.services.company.company_service import CompanyService
from app.utils.decorators import catch_exceptions
from app.services.auth.auth_service import get_current_user
from app.models.company.model import *
from app.models.auth import CurrentUserResponse
from app.core.exceptions import AppException, ErrorCode
from app.db.master_prisma import Prisma
from datetime import datetime, UTC

router = APIRouter()
logger = get_logger(__name__)

@router.post("/", response_model = CompanyRegisterResponse)
@catch_exceptions
async def register_company(
    payload: RegisterCompany
) : 
    """
    企業登録API
    """
    logger.info(
        "Starting company registration process",
        extra={"company_name": payload.company_name}
    )
    company = await CompanyService.create_company(payload)
    logger.info(
        "Company registration successful",
        extra={"company_id": company.company_id}
    )
    return company

@router.get("/", response_model = TenantListResponse)
@catch_exceptions
async def get_all_tenants():
    """
    全てのテナント情報を取得する
    """
    logger.info("Starting to get all tenants")
    response = await CompanyService.get_all_tenants()
    logger.info("All tenants retrieved successfully")
    return response

@router.get("/{company_id}", response_model = TenantInfoResponse)
@catch_exceptions
async def get_tenant_by_company_id(company_id: str):
    """
    指定された会社IDに紐づくテナント情報を取得する
    """
    logger.info("Starting to get tenant by company ID", extra={"company_id": company_id})
    response = await CompanyService.get_tenant_by_company_id(company_id)
    logger.info("Tenant retrieved successfully")
    return response
    
"""
@router.post("/update-schemas", response_model = None)
@catch_exceptions
async def update_all_tenant_schemas():
"""
        #全てのテナントデータベースのスキーマを更新する
"""
    logger.info("Starting to update all tenant schemas")

    result = await CompanyService.update_all_tenant_schemas()
    logger.info("All tenant schemas updated successfully")
    return result
"""

"""
@router.post("/user", response_model = CompanyUserRegisterResponse)
@catch_exceptions
async def register_company_user(
    payload: RegisterCompanyUser
) : 
    """
    #企業ユーザー登録API
"""
    logger.info(
        "Starting company user registration process",
        extra={"user_email": payload.user_email}
    )
    user = await CompanyService.create_company_user(payload)
    logger.info(
        "Company user registration successful",
        extra={"user_id": user.user_id}
    )
    return user
"""

@router.post("/{company_id}/senpai", response_model = RegisterSenpaiResponse)
@catch_exceptions
async def register_senpai(
    company_id: str,
    payload: RegisterSenpaiRequest
) : 
    """
    先輩登録API
    会社IDを指定して, 先輩情報を登録する
    """
    logger.info(
        "Starting senpai registration process",
        extra={"company_id": company_id, "senpai_name": payload.senpai_name}
    )
    response = await CompanyService.register_senpai(company_id, payload)
    logger.info(
        "Senpai registration successful",
        extra={"senpai_id": response.senpai_id}
    )
    return response

@router.get("/{company_id}/senpai/{senpai_id}", response_model = GetSenpaiDetailResponse)
@catch_exceptions
async def get_senpai_detail(
    company_id: str,
    senpai_id: str
) : 
    """
    先輩詳細取得API
    会社IDと先輩IDを指定して, 先輩情報を取得する
    """
    logger.info(
        "Starting senpai detail retrieval process",
        extra={"company_id": company_id, "senpai_id": senpai_id}
    )
    response = await CompanyService.get_senpai_detail(company_id, senpai_id)
    logger.info(
        "Senpai detail retrieval successful",
        extra={"senpai_id": response.senpai_id}
    )
    return response
    
@router.get("/{company_id}/senpai", response_model = SenpaiListResponse)
@catch_exceptions
async def get_senpai_list(
    company_id: str
) : 
    """
    先輩一覧取得API
    会社IDを指定して, 全先輩情報を取得する
    """
    logger.info(
        "Starting senpai list retrieval process",
        extra={"company_id": company_id}
    )
    response = await CompanyService.get_senpai_list(company_id)
    logger.info(
        "Senpai list retrieval successful",
        extra={"company_id": company_id}
    )
    return response

@router.post("/{company_id}/data/upload", response_model = UploadDataResponse)
@catch_exceptions
async def upload_data(
    company_id: str,
    file: UploadFile = File(...),
) : 
    """
    データアップロードAPI
    会社IDを指定して, データをアップロードする
    """

    logger.info(
        "Starting data upload process",
        extra={"company_id": company_id, "filename": file.filename}
    )

    result = await CompanyService.upload_csv_to_blob(
        company_id=company_id,
        file=file,
    )

    logger.info(
        "Data upload successful",
        extra={"file_id": result["fileId"], "company_id": company_id}
    )

    return UploadDataResponse(**result)

@router.post("/{company_id}/data/restore", response_model = RestoreDataResponse)
@catch_exceptions
async def restore_data(
    company_id: str,
) : 
    """
    データ復元API
    会社IDを指定して, データを復元する
    """
    logger.info(
        "Starting data restore process",
        extra={"company_id": company_id}
    )

    result = await CompanyService.restore_csv_metadata(
        company_id=company_id,
    )

    logger.info(
        "Data restore successful",
        extra={"company_id": company_id}
    )

    return RestoreDataResponse(**result)

@router.get("/{company_id}/data/status/{file_id}")
@catch_exceptions
async def get_file_status(
    company_id: str,
    file_id: str
):
    """
    ファイルのステータスを取得するAPI
    会社IDとファイルIDを指定して, ファイルのステータスを取得する
    """
    file_status = await CompanyService.get_file_status(company_id=company_id, file_id=file_id)
    return file_status
    """
    try:
        file_status = await CompanyService.get_file_status(company_id=company_id, file_id=file_id)
        return file_status

    except AppException as e:
        logger.error("Error retrieving file status", extra={"error": str(e)}, exc_info=True)
        if e.error_code == "NOT_FOUND":
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=e.message)
        elif e.error_code == "INVALID_REQUEST":
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=e.message)
        else:
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")

    except Exception as e:
        logger.error("Unexpected error retrieving file status", extra={"error": str(e)}, exc_info=True)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Internal Server Error")
    """


@router.delete("/{company_id}/data/delete", response_model = DeleteDataResponse)
@catch_exceptions
async def delete_data(
    company_id: str,
    request_body: DeleteDataRequest
) : 
    """
    データ削除API
    会社IDを指定して, データを削除する
    """

    logger.info(
        "Starting data deletion process",
        extra={"company_id": company_id, "file_id": request_body.file_id}
    )

    result = await CompanyService.delete_csv_from_blob(
        company_id=company_id,
        file_id=request_body.file_id,
    )

    logger.info(
        "Data deletion successful",
        extra={"file_id": request_body.file_id, "company_id": company_id}
    )

    return DeleteDataResponse(**result)

@router.get("/{company_id}/rag/prompt", response_model = GetPromptResponse)
@catch_exceptions
async def get_rag_prompt(
    company_id: str
) : 
    """
    会社IDを指定して, RAGのプロンプトを取得する
    """
    prompt = await CompanyService.get_prompt_template(company_id)
    return GetPromptResponse(prompt=prompt)

@router.post(
    "/{company_id}/invite-codes",
    response_model=InviteCodeResponse,
    summary="招待コードを生成",
    description="会社管理者が招待コードを生成します。"
)
@catch_exceptions
async def create_invite_code(
    company_id: str,
    request: CreateInviteCodeRequest,
    current_user: CurrentUserResponse = Depends(get_current_user)
) -> InviteCodeResponse:
    """
    招待コードを生成するエンドポイント
    
    Args:
        company_id: 会社ID
        request: 招待コード生成リクエスト
        current_user: 現在のユーザー情報
    
    Returns:
        InviteCodeResponse: 生成された招待コード情報
    """
    logger.info(
        "Starting invite code generation",
        extra={"company_id": company_id}
    )
    
    response = await CompanyService.create_invite_code(company_id, request, current_user)
    
    logger.info(
        "Invite code generated successfully",
        extra={"company_id": company_id}
    )
    
    return response

@router.patch(
    "/{company_id}/allowed-domains",
    response_model=AllowedDomainsUpdateResponse,
    dependencies=[Depends(get_current_user)],
    summary="会社の許可ドメインを更新",
    description="会社の許可ドメインを更新します。adminまたはcompany_adminロールのみが実行可能です。"
)
@catch_exceptions
async def update_allowed_domains(
    company_id: str,
    request: AllowedDomainsUpdateRequest,
    current_user: CurrentUserResponse = Depends(get_current_user)
) -> AllowedDomainsUpdateResponse:
    """
    会社の許可ドメインを更新するエンドポイント
    
    - adminまたはcompany_adminロールのみが実行可能
    - company_adminは自分の会社のドメインのみ更新可能
    - ドメインは'@'で始まる必要がある
    """
    # 権限チェック
    if current_user.role not in CompanyService.ALLOWED_ROLES:
        raise AppException(
            error_code=ErrorCode.PERMISSION_DENIED,
            message=f"ドメインの更新には管理者権限が必要です。許可されているロール: {', '.join(CompanyService.ALLOWED_ROLES)}"
        )
    
    logger.info(
        "Starting allowed domains update",
        extra={
            "company_id": company_id,
            "user_id": current_user.user_id,
            "domains": request.allowed_domains
        }
    )
    
    result = await CompanyService.update_allowed_domains(
        company_id=company_id,
        allowed_domains=request.allowed_domains,
        current_user=current_user
    )
    
    logger.info(
        "Allowed domains updated successfully",
        extra={
            "company_id": company_id,
            "user_id": current_user.user_id
        }
    )
    
    return result

@router.get("/{company_id}/allowed-domains", response_model=AllowedDomainsResponse)
@catch_exceptions
async def get_allowed_domains(
    company_id: str,
    current_user: CurrentUserResponse = Depends(get_current_user)
) -> AllowedDomainsResponse:
    """
    会社の許可ドメインを取得するエンドポイント
    
    - adminまたはcompany_adminロールのみが実行可能
    - company_adminは自分の会社のドメインのみ取得可能
    """
    
    # 権限チェック
    if current_user.role not in CompanyService.ALLOWED_ROLES:
        raise AppException(
            error_code=ErrorCode.PERMISSION_DENIED,
            message=f"ドメインの取得には管理者権限が必要です。許可されているロール: {', '.join(CompanyService.ALLOWED_ROLES)}"
        )
    
    logger.info(
        "Starting allowed domains retrieval",
        extra={
            "company_id": company_id,
            "user_id": current_user.user_id
        }
    )

    result = await CompanyService.get_allowed_domains(company_id)

    logger.info(
        "Allowed domains retrieved successfully",
        extra={"company_id": company_id}
    )

    return result

@router.get("/list/slack-workspaces", response_model=SlackWorkspaceListResponse)
@catch_exceptions
async def get_slack_workspaces() -> SlackWorkspaceListResponse:
    """
    Slackワークスペース一覧を取得するエンドポイント
    
    - すべてのSlackワークスペース情報を返す
    """
    logger.info("Starting slack workspaces retrieval")
    ret = []

    try:
        async with Prisma() as prisma:
            # Slackワークスペース一覧を取得
            workspaces = await prisma.slackworkspace.find_many(
                include={
                    "tenant": True  # 関連する会社情報も取得
                }
            )

            for ws in workspaces:
                tenant = await prisma.tenants.find_unique(
                    where={
                        "id": ws.tenantId
                    }
                )
                company_id = tenant.companyId
                ret.append(SlackWorkspaceInfo(
                    id=ws.id,
                    team_id=ws.teamId,
                    company_id=company_id,
                    created_at=ws.createdAt,
                    updated_at=ws.updatedAt
                ))
            
            logger.info(
                "Slack workspaces retrieved successfully",
                extra={
                    "count": len(ret)
                }
            )
            
            return SlackWorkspaceListResponse(
                status="success",
                workspaces=ret
            )
            
    except Exception as e:
        logger.error(
            "Failed to retrieve slack workspaces",
            extra={
                "error": str(e),
                "error_type": type(e).__name__
            },
            exc_info=True
        )
        raise AppException(
            error_code=ErrorCode.DATABASE_ERROR,
            message="Slackワークスペース一覧の取得に失敗しました",
            context={"error": str(e)}
        )
