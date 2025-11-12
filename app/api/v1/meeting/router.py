from fastapi import APIRouter, UploadFile, File, HTTPException, status
from app.services.meeting.meeting_service import MeetingService
from app.core.logging import get_logger
from app.core.exceptions import AppException

router = APIRouter()
logger = get_logger(__name__)

@router.post("/{company_id}/upload")
async def upload_meeting_text(
    company_id: str,
    file: UploadFile = File(...),
):
    """
    テキストファイルをCSVに変換してBlobに保存するAPI
    """
    try:
        result = await MeetingService.convert_txt_to_csv_and_upload(
            company_id=company_id,
            file=file
        )
        return {
            "status": "success",
            "message": "ファイルが正常にアップロードされました",
            "fileId": result["fileId"]
        }
    except AppException as e:
        logger.error(f"Error uploading meeting text: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(e)
        ) 