from fastapi import APIRouter, Depends, HTTPException, status, Body
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from app.services.company.company_service import CompanyService
from app.models.company import RegisterCompanyUser
from app.core.exceptions import AppException, ErrorCode
from app.core.config import settings
from app.core.logging import logger
from app.db.master_prisma.prisma import Prisma
import secrets

router = APIRouter()
basic_auth = HTTPBasic()


def verify_basic(creds: HTTPBasicCredentials = Depends(basic_auth)):
    user_ok = secrets.compare_digest(creds.username, settings.B2C_BASIC_USER)
    pwd_ok  = secrets.compare_digest(creds.password, settings.B2C_BASIC_PW)
    if not (user_ok and pwd_ok):
        raise HTTPException(status_code=401, detail="Unauthorized")

@router.post("/b2c/verify-invite", dependencies=[Depends(verify_basic)])
async def verify_invite_code(claims: dict = Body(...)):
    """
    招待コードまたはドメインを検証するエンドポイント
    - メールアドレスのドメインが許可されている場合は招待コード不要
    - 許可されていないドメインの場合は招待コード必須
    """
    email = claims.get("email")
    logger.info("Starting invite verification", extra={"email": email})
    
    # 拡張属性から招待コードを取得
    extension_app_id = settings.AZURE_B2C_EXTENSION_ID.replace("-", "")
    invite_code = claims.get(f"extension_{extension_app_id}_inviteCode")
    company_id = claims.get(f"extension_{extension_app_id}_companyId")
    
    if not company_id:
        logger.warning("Company ID not found in claims", extra={"email": email})
        return {
            "version": "1.0.0",
            "status": 400,
            "action": "ValidationError",
            "userMessage": "会社IDが見つかりません"
        }
    
    try:
        await CompanyService.verify_invite_code(company_id, email, invite_code)
        
        logger.info("Invite verification successful", extra={
            "email": email,
            "company_id": company_id
        })
        
        return {
            "version": "1.0.0",
            "status": 200,
            "action": "Continue",
            "extension_inviteCode": invite_code
        }
    except AppException as e:
        logger.warning("Invite verification failed", extra={
            "email": email,
            "company_id": company_id,
            "error": str(e)
        })
        return {
            "version": "1.0.0",
            "status": 400,
            "action": "ValidationError",
            "userMessage": e.message
        }

@router.post("/b2c/user-provision", dependencies=[Depends(verify_basic)])
async def b2c_user_provision(claims: dict = Body(...)):
    logger.info("Starting B2C user provision process")
    extension_app_id = settings.AZURE_B2C_EXTENSION_ID.replace("-", "")
    payload = RegisterCompanyUser(
        company_id    = claims.get(f"extension_{extension_app_id}_companyId"),
        azure_user_id = claims.get("objectId"),
        user_email    = claims.get("email"),
        user_name     = claims.get("displayName"),
        user_role     = claims.get(f"extension_{extension_app_id}_role"),
    )
    try:
        await CompanyService.create_company_user(payload)
    except AppException as ae:
        # 重複などは 409 で返し、B2C に「既に登録済み」を伝える
        if ae.error_code == ErrorCode.DUPLICATE_ENTRY:
            raise HTTPException(status_code=409, detail="User already exists")
        raise
    # After-User-Creation は 204/200 で OK
    return {"version": "1.0.0", "status": "ok"}
