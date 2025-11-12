import os
import urllib
import pandas as pd
from urllib.parse import urlparse, unquote
from datetime import datetime, timezone, UTC, timedelta
from azure.storage.blob import BlobServiceClient
from fastapi import UploadFile
from typing import Optional, Tuple
from app.models.company import *
from app.core.config import settings
from app.db.tenant_prisma.prisma import Prisma as TenantClient
from app.db.master_prisma.prisma import Prisma as MasterClient
from app.core.logging import get_logger
from app.core.exceptions import AppException, ErrorCode
from app.services.azure.database import execute_with_client, get_connection_uri_for_tenant_with_server_name
from app.utils.env_manager import temporary_env
from app.utils.subprocess import prisma_db_push
from app.utils.db_client import tenant_client_context_by_company_id
from app.models.auth import CurrentUserResponse
from io import StringIO
import json
import secrets
import string

logger = get_logger(__name__)

class CompanyService: 
    # 招待コード発行を許可するロール
    ALLOWED_ROLES = ["admin", "company_admin"]  # 必要に応じてロールを追加

    @staticmethod
    async def _get_tenant_db_config() -> Tuple[str, str, str, str]:
        """
        データベース設定を取得
        """
        try:
            return (
                urllib.parse.quote(settings.POSTGRES_USER),
                settings.POSTGRES_PW,
                settings.POSTGRES_DB,
                "require"
            )
        except AttributeError as e:
            raise AppException(
                error_code=ErrorCode.CONFIG_ERROR,
                message="Missing database configuration",
                context={"error": str(e)}
            )
    
    @staticmethod
    async def _is_company_exists(company_name: str) -> bool:
        """
        マスターデータベースに同様の会社名が存在するかを確認する
        """
        async def operation(client: MasterClient):
            company = await client.tenants.find_first(where={"companyName": company_name})
            return company is not None
        return await execute_with_client(MasterClient, operation)

    @staticmethod
    async def _init_tenant_db(db_url: str):
        """
        テナントDBの初期化
        """
        os.environ["DATABASE_URL"] = db_url
        prisma_db_push("./app/db/tenant_prisma/schema.prisma")
        
    @staticmethod
    async def _create_company_in_tenant_db(
        client: TenantClient,
        payload: RegisterCompany
    ) -> dict:
        """
        テナントデータベースに企業を登録する
        """
        try:
            return await client.company.create(
                data={
                    "companyName": payload.company_name,
                    "companyServerName": payload.company_server_name,
                }
            )
        except Exception as e:
            raise AppException(
                message="Failed to create company in tenant database",
                error_code=ErrorCode.DATABASE_ERROR,
                context={"error": str(e)}
            )
        
    @staticmethod
    async def _register_company_in_master_db(
        client: MasterClient,
        payload: RegisterCompany,
        company_id: str
    ):
        """
        マスターデータベースに企業を登録する
        """
        try:
            # 重複チェック
            existing_company = await client.tenants.find_first(
                where={
                    "companyServerName": payload.company_server_name
                }
            )

            if existing_company:
                raise AppException(
                    message="Company server name already exists",
                    error_code=ErrorCode.DUPLICATE_ENTRY,
                    context={"company_server_name": payload.company_server_name}
                )
            
            return await client.tenants.create(
                data={
                    "companyName": payload.company_name,
                    "companyId": company_id,
                    "companyServerName": payload.company_server_name,
                }
            )
        except Exception as e:
            raise AppException(
                message="Failed to register company in master database",
                error_code=ErrorCode.DATABASE_ERROR,
                context={"error": str(e)}
            )
        
    @staticmethod
    async def create_company(payload: RegisterCompany) -> CompanyRegisterResponse:
        tenant_client: Optional[TenantClient] = None
        master_client: Optional[MasterClient] = None
        # テナントデータベースの存在確認
        if await CompanyService._is_company_exists(payload.company_name):
            raise AppException(
                error_code=ErrorCode.DUPLICATE_ENTRY,
                message="Company already exists",
                context={"company_name": payload.company_name}
            )

        # テナントDB初期化
        db_url = get_connection_uri_for_tenant_with_server_name(payload.company_server_name)

        # テナントDB登録
        with temporary_env("DATABASE_URL", db_url):
            await CompanyService._init_tenant_db(db_url)
            tenant_client = TenantClient()
            await tenant_client.connect()
            company = await CompanyService._create_company_in_tenant_db(tenant_client, payload)
        
            # マスターデータベースへの企業登録
            try:
                master_client = MasterClient()
                await master_client.connect()
                await CompanyService._register_company_in_master_db(
                    master_client,
                    payload,
                    company.id
                )

                # デフォルトの招待コードを生成（30日間有効）
                invite_code = CompanyService._generate_invite_code()
                expires_at = CompanyService._calculate_expiry_date(30)
                
                # 招待コードを保存
                await master_client.invitationtoken.create(
                    data={
                        "token": [invite_code],
                        "companyId": company.id,
                        "expiresAt": expires_at,
                        "used": False,
                        "createdAt": datetime.now(UTC),
                        "updatedAt": datetime.now(UTC)
                    }
                )

                return CompanyRegisterResponse(
                    company_id=company.id,
                    status="success",
                    message=f"Company created successfully. Default invite code: {invite_code}"
                )
            except Exception as master_error:
                logger.error(
                    "Master DB registration failed; attempting rollback of tenant DB record",
                    extra={"company_id": company.id, "error": str(master_error)},
                    exc_info=True
                )
                # マスターデータベースへの企業登録に失敗した場合、テナントデータベースの企業登録をロールバック
                try:
                    await tenant_client.company.delete(where={"id": company.id})
                    logger.info("Rollback successful for tenant DB record", extra={"company_id": company.id})
                except Exception as rollback_error:
                    logger.critical(
                        "Rollback failed for tenant DB record",
                        extra={"company_id": company.id, "rollback_error": str(rollback_error)},
                        exc_info=True
                    )
                raise AppException(
                    error_code=ErrorCode.DATABASE_ERROR,
                    message="Failed to register company in master database; rollback attempted",
                    context={"original_error": str(master_error)}
                )
            
            # 接続のクリーンアップ
            finally:
                for client in [tenant_client, master_client]:
                    if client:
                        try:
                            await client.disconnect()
                        except Exception as disconnect_error:
                            logger.error(
                                f"Error disconnecting {client.__class__.__name__} in cleanup",
                                extra={"error": str(disconnect_error)},
                                exc_info=True
                            )

    @staticmethod
    async def get_all_tenants() -> TenantListResponse:
        """
        全てのテナント情報をマスターデータベースから取得する
        """
        async def operation(client: MasterClient):
            return await client.tenants.find_many()
        rows = await execute_with_client(MasterClient, operation)
        tenants = []
        for row in rows:
            tenants.append(TenantInfo(
                id=row.id,
                company_name=row.companyName,
                company_id=row.companyId,
                company_server_name=row.companyServerName,
                created_at=row.createdAt,
                updated_at=row.updatedAt
            ))
        return TenantListResponse(status="success", tenants=tenants)
    
    @staticmethod
    async def get_tenant_by_company_id(company_id: str) -> TenantInfoResponse:
        """
        指定された会社IDに紐づくテナント情報を取得する
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            tenant = await tenant_client.company.find_first(where={"id": company_id})
            tenant_info = TenantInfoFromTenant(
                id=tenant.id,
                company_name=tenant.companyName,
                company_server_name=tenant.companyServerName,
                created_at=tenant.createdAt,
                updated_at=tenant.updatedAt
            )
            return TenantInfoResponse(status="success", tenant=tenant_info)
    
    @staticmethod
    async def get_all_tenant_server_names():
        """
        マスターデータベースから全企業のサーバーネームを取得する
        """
        async def operation(client: MasterClient):
            rows = await client.tenants.find_many()
            return [row.companyServerName for row in rows]
        return await execute_with_client(MasterClient, operation)
    
    @staticmethod
    async def update_all_tenant_schemas():
        """
        全てのテナントデータベースのスキーマを更新する
        """
        failed_server_names = []
        try:
            tenant_server_names = await CompanyService.get_all_tenant_server_names()
            for server_name in tenant_server_names:
                try:
                    db_url = get_connection_uri_for_tenant_with_server_name(server_name)
                    os.environ["DATABASE_URL"] = db_url
                    prisma_db_push("./app/db/tenant_prisma/schema.prisma")
                    logger.info(f"Tenant DB schema updated for {server_name}")
                except Exception as e:
                    logger.error(f"Failed to update tenant DB schema for {server_name}: {str(e)}")
                    failed_server_names.append(server_name)

            if failed_server_names:
                raise AppException(
                    message="Failed to update all tenant schemas",
                    error_code=ErrorCode.DATABASE_ERROR,
                    context={"failed_server_names": failed_server_names}
                )
            return {
                "status": "success",
                "message": "All tenant schemas updated successfully",
            }
        except Exception as e:
            raise AppException(
                message="Failed to update all tenant schemas",
                error_code=ErrorCode.DATABASE_ERROR,
                context={"error": str(e)}
            )
        finally:
            os.environ.pop("DATABASE_URL", None)
    
    @staticmethod
    async def create_company_user(payload: RegisterCompanyUser) -> CompanyUserRegisterResponse:
        """
        既存の企業ユーザー登録（サインアップ/サインイン）：
        Azure AD B2C のユーザーフローでユーザーがサインアップ・サインインし、IDトークンを受け取った後、
        フロントエンドからそのトークン情報(例: azure_user_id、メール、表示名、カスタム属性)を含むリクエストが送られてくる想定です。
        このAPIでは、受け取った情報をもとにテナントDB に企業ユーザーレコードを登録します。

        前提：
            1. ユーザーは B2C のユーザーフローを用いて自分でアカウントを作成している（サインアップ／サインイン）。
            2. フロントエンドは、B2C から発行されたIDトークンから必要な情報(azure_user_id、user_email、user_name、company_id、user_roleなど)を抽出して、       
        """
        async with tenant_client_context_by_company_id(payload.company_id) as tenant_client:

            # 企業アカウントの存在確認
            company = await tenant_client.company.find_first(where={"id": payload.company_id})
            if not company:
                raise AppException(
                    error_code=ErrorCode.NOT_FOUND,
                    message="Company record not found in tenant database",
                    context={"company_id": payload.company_id}
                )

            # テナントDBに企業ユーザーを登録
            user = await tenant_client.companyuser.create(
                data={
                    "companyId": company.id,
                    "email": payload.user_email,
                    "name": payload.user_name,
                    "role": payload.user_role,
                    "azureUserId": payload.azure_user_id
                }
            )

            return CompanyUserRegisterResponse(
                user_id=user.id,
                status="success"
            )      

    @staticmethod
    async def register_senpai(company_id: str, payload: RegisterSenpaiRequest) -> RegisterSenpaiResponse:
        """
        指定した会社の先輩アカウントを登録する
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            senpai = await tenant_client.senior.create(
                data={
                    "companyId": company_id,
                    "name": payload.senpai_name,
                    "profile": json.dumps(payload.profile),
                }
            )

            return RegisterSenpaiResponse(
                senpai_id=senpai.id,
                status="success",
                message="Senpai registered successfully",
            )

    @staticmethod
    async def get_senpai_detail(company_id: str, senpai_id: str) -> GetSenpaiDetailResponse:
        """
        指定した会社の先輩アカウントの詳細を取得する
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            record = await tenant_client.senior.find_first(
                where={"id": senpai_id, "companyId": company_id}
            )
            if not record:
                raise AppException(
                    error_code=ErrorCode.NOT_FOUND,
                    message="Senpai record not found in tenant database",
                    context={"senpai_id": senpai_id}
                )

            return GetSenpaiDetailResponse(
                senpai_id=record.id,
                senpai_name=record.name,
                profile=record.profile,
                created_at=record.createdAt,
            )
                
    @staticmethod
    async def get_senpai_list(company_id: str) -> SenpaiListResponse:
        """
        指定した会社の全先輩アカウントを取得する
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            records = await tenant_client.senior.find_many(
                where={"companyId": company_id}
            )
            senpais = []
            for rec in records:
                senpais.append(
                    GetSenpaiDetailResponse(
                        senpai_id=rec.id,
                        senpai_name=rec.name,
                        profile=rec.profile,
                        created_at=rec.createdAt,
                    )
                )
            return SenpaiListResponse(
                status="success",
                senpais=senpais,
            )

    @staticmethod
    async def upload_csv_to_blob(company_id: str, file: UploadFile):
        """
        CSVファイルをAzure Blob Storageにアップロードし、メタデータをDBに登録する
        """
        CONECTION_STRING = settings.AZURE_STORAGE_CONNECTION_STRING
        CONTAINER_NAME = settings.AZURE_STORAGE_CONTAINER_NAME
        try:
            # CSVファイル形式確認（オプションで拡張可能）
            if file.content_type != "text/csv":
                raise AppException(
                    error_code=ErrorCode.INVALID_REQUEST,
                    message="アップロードされたファイルはCSV形式ではありません。",
                )

            # ファイルサイズ制限チェック（ここでは例として10MBを設定）
            contents = await file.read()
            if len(contents) > 10 * 1024 * 1024:
                raise AppException(
                    error_code=ErrorCode.PAYLOAD_TOO_LARGE,
                    message="ファイルサイズが制限を超えています（10MBまで）",
                )

            # Azure Blob Storageへのアップロード
            blob_service_client = BlobServiceClient.from_connection_string(CONECTION_STRING)
            blob_name = f"{company_id}/{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}_{file.filename}"
            blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
            blob_client.upload_blob(contents)

            blob_url = blob_client.url

            # DBへのメタデータ登録（PrismaにID生成を任せる）
            uploaded_at = datetime.now(timezone.utc)
            async with tenant_client_context_by_company_id(company_id) as tenant_client:
                csv_file_record = await tenant_client.csvfile.create(
                    data={
                        "fileName": file.filename,
                        "size": len(contents),
                        "uploadedAt": uploaded_at,
                        "blobUrl": blob_url,    
                        "status": "uploaded",
                        "companyId": company_id,  
                    }
                )

            return {
                "status": "success",
                "message": "File uploaded successfully",
                "fileId": csv_file_record.id,
            }

        except AppException as ae:
            raise ae  
        except Exception as e:
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="ファイルアップロード中にエラーが発生しました。",
                context={"error": str(e)}
            )

    @staticmethod
    async def restore_csv_metadata(company_id: str):
        """
        Blob StorageからCSVファイルのメタデータを復元する
        """
        CONECTION_STRING = settings.AZURE_STORAGE_CONNECTION_STRING
        CONTAINER_NAME = settings.AZURE_STORAGE_CONTAINER_NAME

        try:
            # Blob Storageからファイル一覧を取得
            blob_service_client = BlobServiceClient.from_connection_string(CONECTION_STRING)
            container_client = blob_service_client.get_container_client(CONTAINER_NAME)
            
            # 会社IDでフィルタリング
            prefix = f"{company_id}/"
            blobs = container_client.list_blobs(name_starts_with=prefix)

            restored_files = []
            async with tenant_client_context_by_company_id(company_id) as tenant_client:
                for blob in blobs:
                    # ファイル名からメタデータを抽出
                    file_name = blob.name.split('/')[-1]  # パスからファイル名を取得
                    uploaded_at = blob.creation_time  # Blobの作成日時

                    # メタデータをデータベースに登録
                    csv_file_record = await tenant_client.csvfile.create(
                        data={
                            "fileName": file_name,
                            "size": blob.size,
                            "uploadedAt": uploaded_at,
                            "blobUrl": f"https://{blob_service_client.account_name}.blob.core.windows.net/{CONTAINER_NAME}/{blob.name}",
                            "status": "uploaded",
                            "companyId": company_id,
                        }
                    )
                    restored_files.append({
                        "fileId": csv_file_record.id,
                        "fileName": file_name,
                        "uploadedAt": uploaded_at.isoformat()
                    })

            return {
                "status": "success",
                "message": f"Restored {len(restored_files)} files",
                "restored_files": restored_files
            }

        except Exception as e:
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="メタデータの復元中にエラーが発生しました",
                context={"error": str(e)}
            )
        
    @staticmethod
    async def get_file_status(company_id: str, file_id: str):
        """
        特定ファイルの処理ステータスを取得
        """
        CONECTION_STRING = settings.AZURE_STORAGE_CONNECTION_STRING
        CONTAINER_NAME = settings.AZURE_STORAGE_CONTAINER_NAME

        try:
            async with tenant_client_context_by_company_id(company_id) as tenant_client:
                csv_file_record = await tenant_client.csvfile.find_first(
                    where={"id": file_id, "companyId": company_id}
                )
            if not csv_file_record:
                raise AppException(
                    error_code=ErrorCode.NOT_FOUND,
                    message="CSVファイルが見つかりません",
                    context={"file_id": file_id}
                )

            # Azure Blob StorageからCSVを取得しレコード数を計算
            blob_service_client = BlobServiceClient.from_connection_string(CONECTION_STRING)
            parsed_url = urlparse(csv_file_record.blobUrl)
            blob_name = unquote(parsed_url.path).replace(f"/{CONTAINER_NAME}/", "", 1)
            blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
            downloader = blob_client.download_blob()
            csv_content = downloader.content_as_text()
            try:
                df = pd.read_csv(StringIO(csv_content))
            except Exception as e:
                logger.error(
                    "Error reading CSV",
                    extra={"company_id": company_id, "file_id": file_id}
                )
                raise AppException(
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                    message="CSVファイルの読み込み中にエラーが発生しました",
                    context={"error": str(e)}
                )
            records_count = len(df)
            return {
                "status": "success",
                "fileStatus": {
                    "fileId": csv_file_record.id,
                    "currentStage": csv_file_record.status,
                    "recordsCount": records_count,
                    "updatedAt": csv_file_record.uploadedAt.isoformat(),
                }
            }
        except Exception as e:
            logger.error(
                "Error getting file status",
                extra={"error": str(e)},
                exc_info=True
            )
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="ファイルのステータス取得中にエラーが発生しました",
                context={"error": str(e)}
            )

        
    @staticmethod
    async def delete_csv_from_blob(company_id: str, file_id: str):
        """
        CSVファイルをAzure Blob Storageから削除し、DBのメタデータも削除する
        """
        CONECTION_STRING = settings.AZURE_STORAGE_CONNECTION_STRING
        CONTAINER_NAME = settings.AZURE_STORAGE_CONTAINER_NAME

        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            # DBからレコードを取得
            csv_file_record = await tenant_client.csvfile.find_first(
                where={"id": file_id, "companyId": company_id}
            )
            if not csv_file_record:
                raise AppException(
                    error_code=ErrorCode.NOT_FOUND,
                    message="CSVファイルが見つかりません",
                    context={"file_id": file_id}
                )

            # Azure Blob Storageから削除
            parsed_url = urlparse(csv_file_record.blobUrl)
            blob_name = unquote(parsed_url.path).replace(f"/{CONTAINER_NAME}/", "", 1)
            blob_service_client = BlobServiceClient.from_connection_string(CONECTION_STRING)
            blob_client = blob_service_client.get_blob_client(container=CONTAINER_NAME, blob=blob_name)
            blob_client.delete_blob()

            # DBからレコードを削除
            await tenant_client.csvfile.delete(where={"id": file_id})

        return {
            "status": "success",
            "message": "File deleted successfully"
        }
    
    async def get_prompt_template(company_id: str) -> str:
        """
        指定された会社のプロンプトテンプレートを取得する
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            prompt_template = await tenant_client.company.find_first(where={"id": company_id})
            return prompt_template.promptTemplate
        
    async def update_prompt_template(company_id: str, prompt_template: str):
        """
        指定された会社のプロンプトテンプレートを更新する
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            await tenant_client.company.update(where={"id": company_id}, data={"promptTemplate": prompt_template})

    async def reset_prompt_template(company_id: str):
        """
        指定された会社のプロンプトテンプレートをリセットする
        """
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            await tenant_client.company.update(where={"id": company_id}, data={"promptTemplate": ""})

    @staticmethod
    def _generate_invite_code(length: int = 12) -> str:
        """
        招待コードを生成する
        
        Args:
            length: 生成するコードの長さ（デフォルト: 12文字）
        
        Returns:
            str: 生成された招待コード
        """
        alphabet = string.ascii_uppercase + string.digits
        return ''.join(secrets.choice(alphabet) for _ in range(length))
    
    @staticmethod
    def _calculate_expiry_date(days: int) -> datetime:
        """
        有効期限の日時を計算する
        
        Args:
            days: 有効期限までの日数
        
        Returns:
            datetime: 有効期限の日時（UTC）
        """
        return datetime.now(UTC) + timedelta(days=days)

    @staticmethod
    def _is_allowed_domain(email: str, allowed_domains: list[str]) -> bool:
        """
        メールアドレスのドメインが許可されているかチェックする
        
        Args:
            email: メールアドレス
            allowed_domains: 許可されたドメインのリスト
        
        Returns:
            bool: ドメインが許可されている場合はTrue
        """
        if not allowed_domains:  # 許可ドメインが設定されていない場合は全て不可
            return False
            
        domain = email.split('@')[-1].lower()
        return domain in [d.lower() for d in allowed_domains]

    @staticmethod
    async def verify_invite_code(company_id: str, email: str, invite_code: str | None = None) -> bool:
        """
        招待コードまたはドメインの検証を行う
        
        Args:
            company_id: 会社ID
            email: メールアドレス
            invite_code: 招待コード（オプション）
        
        Returns:
            bool: 検証が成功した場合はTrue
        
        Raises:
            AppException: 
                - 会社が存在しない場合
                - 招待コードが必要だが無効な場合
        """
        async with MasterClient() as prisma:
            # 会社の存在確認と許可ドメインの取得
            company = await prisma.tenants.find_unique(
                where={"companyId": company_id}
            )
            if not company:
                raise AppException(
                    error_code=ErrorCode.COMPANY_NOT_FOUND,
                    message=f"Company not found: {company_id}"
                )
            
            # ドメインチェック
            allowed_domains = company.allowedDomains or []
            logger.info(f"allowed_domains: {allowed_domains}")
            if CompanyService._is_allowed_domain(email, allowed_domains):
                logger.info(f"domain is allowed: {email}")
                return True
            
            # 許可ドメインでない場合は招待コード必須
            if not invite_code:
                raise AppException(
                    error_code=ErrorCode.INVALID_INVITE_CODE,
                    message="招待コードが必要です"
                )
            
            # 招待コードの検証
            token = await prisma.invitationtoken.find_first(
                where={
                    "token": {
                        "has": invite_code  # 配列内に招待コードが存在するか確認
                    },
                    "expiresAt": {
                        "gt": datetime.now(UTC)
                    }
                }
            )

            logger.info(f"token is used: {token}")
            
            if not token:
                raise AppException(
                    error_code=ErrorCode.INVALID_INVITE_CODE,
                    message="無効な招待コードです"
                )
            
            # 使用したコードを配列から削除
            updated_tokens = [t for t in token.token if t != invite_code]
            await prisma.invitationtoken.update(
                where={"id": token.id},
                data={
                    "token": updated_tokens,
                    "updatedAt": datetime.now(UTC)
                }
            )
            
            return True

    @staticmethod
    async def create_invite_code(company_id: str, request: CreateInviteCodeRequest, current_user: CurrentUserResponse) -> InviteCodeResponse:
        """
        招待コードを生成して保存する
        
        Args:
            company_id: 会社ID
            request: 招待コード生成リクエスト
            current_user: 現在のユーザー情報
        
        Returns:
            InviteCodeResponse: 生成された招待コード情報
        
        Raises:
            AppException: 
                - 会社が存在しない場合
                - 権限エラーの場合（会社IDが一致しない、または許可されていないロール）
        """
        # 会社IDとロールのチェック
        if current_user.company_id != company_id:
            raise AppException(
                error_code=ErrorCode.FORBIDDEN,
                message="You can only create invite codes for your own company"
            )
        
        if current_user.role not in CompanyService.ALLOWED_ROLES:
            raise AppException(
                error_code=ErrorCode.FORBIDDEN,
                message=f"Your role '{current_user.role}' is not allowed to create invite codes. Allowed roles: {', '.join(CompanyService.ALLOWED_ROLES)}"
            )

        async with MasterClient() as prisma:
            # 会社の存在確認
            company = await prisma.tenants.find_unique(
                where={"companyId": company_id}
            )
            if not company:
                raise AppException(
                    error_code=ErrorCode.COMPANY_NOT_FOUND,
                    message=f"Company not found: {company_id}"
                )
            
            # 既存の招待コードを取得
            existing_token = await prisma.invitationtoken.find_unique(
                where={"companyId": company_id}
            )

            # 新しい招待コードを生成
            new_token = CompanyService._generate_invite_code()
            expires_at = CompanyService._calculate_expiry_date(request.expires_in_days)

            if existing_token:
                # 既存のトークン配列に新しいトークンを追加
                updated_tokens = existing_token.token + [new_token]
                invite_token = await prisma.invitationtoken.update(
                    where={"companyId": company_id},
                    data={
                        "token": updated_tokens,
                        "expiresAt": expires_at,
                        "updatedAt": datetime.now(UTC)
                    }
                )
            else:
                # 新しい招待コードレコードを作成
                invite_token = await prisma.invitationtoken.create(
                    data={
                        "token": [new_token],
                        "companyId": company_id,
                        "expiresAt": expires_at,
                        "used": False,
                        "createdAt": datetime.now(UTC),
                        "updatedAt": datetime.now(UTC)
                    }
                )

            return InviteCodeResponse(
                token=new_token,  # 新しく生成したトークンのみを返す
                expires_at=invite_token.expiresAt,
                company_id=invite_token.companyId
            )    

    @staticmethod
    async def update_allowed_domains(
        company_id: str,
        allowed_domains: list[str],
        current_user: CurrentUserResponse
    ) -> AllowedDomainsUpdateResponse:
        """
        会社の許可ドメインを更新する
        
        Args:
            company_id: 会社ID
            allowed_domains: 許可するドメインのリスト
            current_user: 現在のユーザー情報（会社管理者のみ更新可能）
            
        Returns:
            AllowedDomainsUpdateResponse: 更新結果
            
        Raises:
            AppException: 権限エラー、会社が存在しない場合
        """
        # 権限チェック
        if current_user.role not in CompanyService.ALLOWED_ROLES:
            raise AppException(
                error_code=ErrorCode.PERMISSION_DENIED,
                message="ドメインの更新には管理者権限が必要です"
            )
        
        # 会社の存在確認と権限チェック
        async with MasterClient() as prisma:
            company = await prisma.tenants.find_unique(
                where={"companyId": company_id}
            )
            
            if not company:
                raise AppException(
                    error_code=ErrorCode.COMPANY_NOT_FOUND,
                    message="会社が見つかりません"
                )
            
            # 会社管理者の場合は、自分の会社のみ更新可能
            if current_user.role == "company_admin" and current_user.company_id != company_id:
                raise AppException(
                    error_code=ErrorCode.PERMISSION_DENIED,
                    message="他の会社のドメインは更新できません"
                )
            
            # ドメインの形式チェック
            for domain in allowed_domains:
                if not domain.startswith("@"):
                    raise AppException(
                        error_code=ErrorCode.INVALID_DOMAIN_FORMAT,
                        message=f"ドメインは'@'で始まる必要があります: {domain}"
                    )
            
            # ドメインの更新
            await prisma.tenants.update(
                where={"companyId": company_id},
                data={"allowedDomains": allowed_domains}
            )
            
            return AllowedDomainsUpdateResponse(
                status="success",
                message="許可ドメインが更新されました"
            )

    @staticmethod
    async def get_allowed_domains(company_id: str) -> AllowedDomainsResponse:
        """
        会社の許可ドメインを取得する
        """
        async with MasterClient() as prisma:
            company = await prisma.tenants.find_unique(
                where={"companyId": company_id}
            )

            if not company:
                raise AppException(
                    error_code=ErrorCode.COMPANY_NOT_FOUND,
                    message="会社が見つかりません"
                )

            return AllowedDomainsResponse(
                status="success",
                allowed_domains=company.allowedDomains
            )
        