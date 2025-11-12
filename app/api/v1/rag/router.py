from fastapi import APIRouter, HTTPException, status, Depends, Body, Query
from app.services.rag.rag_service import RagService
from app.models.rag import *
from app.models.chat import ChatUser
from app.core.exceptions import AppException, handle_app_exception
from app.core.logging import get_logger
from app.api.v1.chat.dependencies import get_current_user_from_token
from pydantic import BaseModel
from app.services.company.company_service import CompanyService
import traceback
from app.utils.db_client import tenant_client_context_by_company_id
from app.utils.decorators import catch_exceptions

router = APIRouter()
logger = get_logger(__name__)

class PromptRequest(BaseModel):
    company_id: str
    prompt: str

@router.post("/build-index") # postgreSQLにアクセスする場合
@catch_exceptions
async def build_rag_index(
    request_body: BuildIndexRequest,
    #current_user: ChatUser = Depends(get_current_user_from_token),
):
    logger.info("Received request to build RAG index", extra={"file_id": request_body.file_id})

    try:
        result = await RagService.build_index(
            file_id=request_body.file_id,
            company_id=request_body.company_id,
        )
        logger.info("RAG index built successfully", extra={"file_id": request_body.file_id})
        return {
            "status": "success",
            "message": "Index built successfully",
            "indexSize": result.get("indexSize")
        }

    except AppException as e:
        handle_app_exception(e)

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error("Unexpected error during RAG index building", extra={"error": error_details}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal Server Error", "error": str(e), "traceback": error_details}
        )
    
@router.delete("/delete-index")
async def delete_rag_index(
    current_user: ChatUser = Depends(get_current_user_from_token),
):
    logger.info("Received request to delete RAG index", extra={"company_id": current_user.company_id})
    try:
        await RagService.delete_index(
            company_id=current_user.company_id,
        )
        logger.info("RAG index deleted successfully", extra={"company_id": current_user.company_id})
        return {
            "status": "success",
            "message": "Index deleted successfully",
        }

    except AppException as e:
        handle_app_exception(e)

    except Exception as e:
        error_details = traceback.format_exc()
        logger.error("Unexpected error during RAG index deletion", extra={"error": error_details}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail={"message": "Internal Server Error", "error": str(e), "traceback": error_details}
        )
    
@router.post("/build-index-from-blob")
async def build_index_from_blob(
    blob_url: str,
):
    try:
        result = await RagService.build_index_from_blob(
            blob_url=blob_url,
            index_name="saixgen_index",
        )
        return {
            "status": "success",
            "message": "Index built successfully",
            "indexSize": result.get("indexSize")
        }
    except Exception as e:
        logger.error(f"Error building index from blob: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )

# @router.post("/build-index-from-blob") # blobに直接アクセスする場合
# async def build_index_from_blob(
#     payload: dict,
#     authorization: str = Header(..., alias="Authorization"),
#     # x_tenant_id: str = Header(..., alias="x-tenant-id")
# ):
#     logger.info("Received request to build RAG index from blob", extra={"blob_url": payload.get("blobUrl")})

#     try:
#         result = await RagService.build_index_from_blob(
#             blob_url=payload.get("blobUrl"),
#             # embedding_model=payload.get("embeddingModel"),　# embedding_modelは今は固定
#             index_name=payload.get("indexName"),
#             # tenant_id=x_tenant_id,
#             connection_string=payload.get("connectionString")
#         )
#         logger.info("RAG index from blob built successfully", extra={"blob_url": payload.get("blobUrl")})
#         return {
#             "status": "success",
#             "message": "Index built successfully",
#             "indexSize": result.get("indexSize")
#         }

#     except AppException as e:
#         handle_app_exception(e)

#     except Exception as e:
#         logger.error("Unexpected error during RAG index building from blob", extra={"error": str(e)}, exc_info=True)
#         raise HTTPException(
#             status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
#             detail="Internal Server Error"
#         )

"""
@router.post("/query")
async def query_rag_index(
    payload: dict,
    # authorization: str = Header(..., alias="Authorization") # test用にコメントアウト
):
    logger.info("Received RAG query request", extra={"query": payload.get("query")})

    try:
        results = await RagService.query_index(
            query=payload.get("query"),
            index_name=payload.get("indexName"),
            top_k=payload.get("topK", 5)
        )

        logger.info("RAG query executed successfully", extra={"query": payload.get("query")})
        return {"status": "success", "results": results}

    except AppException as e:
        handle_app_exception(e)

    except Exception as e:
        logger.error("Unexpected error during RAG query", extra={"error": str(e)}, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Internal Server Error"
        )
"""

class QueryRequest(BaseModel):
    company_id: str
    query: str
    index_name: str
    top_k: int = 5

@router.post("/query")
async def query_rag_index(
    payload: QueryRequest,
):
    try:
        logger.info("Received RAG query request", extra={"query": payload.query})
        
        # クエリの拡張
        expanded_query = await RagService.expand_query(payload.query)
        logger.info(f"Expanded query: {expanded_query}")

        # RAG検索の実行
        result = await RagService.query_index(
            query=expanded_query,
            company_id=payload.company_id,
            top_k=5
        )

        return result

    except AppException as e:
        logger.error(f"RAG error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        )
    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An unexpected error occurred"
        )


@router.post("/prompt")
async def set_system_prompt(request: PromptRequest):
    """
    システムプロンプトを設定する
    """
    try:
        CompanyService.set_prompt(request.company_id, request.prompt)
        return {"status": "success", "message": "システムプロンプトを更新しました"}
    except Exception as e:
        logger.error(f"Error setting prompt: {e}")
        raise HTTPException(status_code=500, detail="システムプロンプトの更新に失敗しました")

@router.get("/prompt/{company_id}")
async def get_system_prompt(company_id: str):
    """
    システムプロンプトを取得する
    """
    try:
        prompt = CompanyService.get_prompt(company_id)
        return {"status": "success", "prompt": prompt}
    except Exception as e:
        logger.error(f"Error getting prompt: {e}")
        raise HTTPException(status_code=500, detail="システムプロンプトの取得に失敗しました")

@router.delete("/prompt/{company_id}")
async def reset_system_prompt(company_id: str):
    """
    システムプロンプトをデフォルトに戻す
    """
    try:
        CompanyService.reset_prompt(company_id)
        return {"status": "success", "message": "システムプロンプトをデフォルトに戻しました"}
    except Exception as e:
        logger.error(f"Error resetting prompt: {e}")
        raise HTTPException(status_code=500, detail="システムプロンプトのリセットに失敗しました")

@router.get("/index/documents")
async def list_index_documents(
    company_id: str = None,
    limit: int = 10,
    current_user: ChatUser = Depends(get_current_user_from_token)
):
    """
    インデックス内のドキュメントを一覧表示する
    """
    try:
        results = await RagService.list_index_documents(
            company_id=company_id or current_user.company_id,
            limit=limit
        )
        return results
    except Exception as e:
        logger.error(f"Error listing index documents: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list index documents: {str(e)}"
        )

@router.get("/files/{company_id}")
async def list_files(
    company_id: str
):
    """
    会社のファイル一覧を取得する
    """
    try:
        async with tenant_client_context_by_company_id(company_id) as tenant_client:
            files = await tenant_client.csvfile.find_many(
                where={"companyId": company_id}
            )
            return [
                {
                    "id": file.id,
                    "blobUrl": file.blobUrl,
                    "status": file.status
                }
                for file in files
            ]
    except Exception as e:
        logger.error(f"Error listing files: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to list files: {str(e)}"
        )

@router.delete("/documents/{document_id}")
async def delete_document(
    document_id: str,
    index_name: str = Query(..., description="インデックス名"),
    current_user: ChatUser = Depends(get_current_user_from_token)
):
    """
    指定されたインデックスから特定のドキュメントを削除します。
    """
    try:
        result = await RagService.delete_document(
            index_name=index_name,
            document_id=document_id
        )
        return {
            "status": "success",
            "message": "Document deleted successfully"
        }
    except Exception as e:
        logger.error(f"Error deleting document: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to delete document: {str(e)}"
        )