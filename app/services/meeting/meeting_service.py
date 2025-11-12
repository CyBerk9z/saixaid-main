import pandas as pd
from io import StringIO
from fastapi import UploadFile
from app.services.company.company_service import CompanyService
from app.core.exceptions import AppException, ErrorCode
from app.core.logging import get_logger
from datetime import datetime, timezone
import re

logger = get_logger(__name__)

class MeetingService:
    @staticmethod
    def _extract_speaker(line: str) -> tuple[str, str]:
        """
        行から発言者とメッセージを抽出する
        例：
        - "山田：こんにちは" -> ("山田", "こんにちは")
        - "[山田] こんにちは" -> ("山田", "こんにちは")
        - "こんにちは" -> ("システムメッセージ", "こんにちは")
        """
        # パターン1: "名前："の形式
        pattern1 = r'^([^：]+)：(.+)$'
        # パターン2: "[名前] メッセージ"の形式
        pattern2 = r'^\[([^\]]+)\]\s+(.+)$'
        
        match1 = re.match(pattern1, line.strip())
        if match1:
            return match1.group(1).strip(), match1.group(2).strip()
            
        match2 = re.match(pattern2, line.strip())
        if match2:
            return match2.group(1).strip(), match2.group(2).strip()
            
        return "システムメッセージ", line.strip()

    @staticmethod
    def _extract_meeting_info(text_content: str) -> tuple[str, datetime]:
        """
        会議のタイトルと日時を抽出する
        """
        # タイトルを探す（最初の非空行をタイトルとする）
        title = "会議"
        for line in text_content.split('\n'):
            if line.strip():
                title = line.strip()
                break

        # 日時を探す（YYYY/MM/DDやYYYY-MM-DDのパターン）
        date_pattern = r'(\d{4}[-/]\d{1,2}[-/]\d{1,2})'
        date_match = re.search(date_pattern, text_content)
        meeting_date = datetime.now(timezone.utc)
        if date_match:
            try:
                date_str = date_match.group(1)
                meeting_date = datetime.strptime(date_str, '%Y/%m/%d').replace(tzinfo=timezone.utc)
            except ValueError:
                pass

        return title, meeting_date

    @staticmethod
    async def convert_txt_to_csv_and_upload(company_id: str, file: UploadFile):
        """
        テキストファイルをCSVに変換してBlobに保存する
        """
        try:
            # テキストファイルを読み込む
            contents = await file.read()
            text_content = contents.decode('utf-8')

            # 会議のタイトルと日時を抽出
            meeting_title, meeting_date = MeetingService._extract_meeting_info(text_content)
            
            # テキストを行ごとに分割
            lines = text_content.split('\n')
            
            # CSVデータを作成
            csv_data = []
            for i, line in enumerate(lines, 1):
                if line.strip():  # 空行をスキップ
                    speaker, message = MeetingService._extract_speaker(line)
                    csv_data.append({
                        'Timestamp': meeting_date.strftime('%Y-%m-%d %H:%M:%S'),
                        'User ID': speaker,  # 発言者名をIDとして使用
                        'User Name': speaker,
                        'Channel': meeting_title,  # 会議のタイトルをチャンネル名として使用
                        'Message': message,
                        'Attachments': '',
                        'Parent Message Timestamp': '',
                        'ts': meeting_date.timestamp()
                    })

            # DataFrameを作成
            df = pd.DataFrame(csv_data)

            # CSVに変換
            csv_buffer = StringIO()
            df.to_csv(csv_buffer, index=False, encoding='utf-8-sig')
            csv_bytes = csv_buffer.getvalue().encode('utf-8')
            csv_buffer.close()

            # UploadFileオブジェクトを作成
            csv_file = UploadFile(
                filename=f"{file.filename.rsplit('.', 1)[0]}.csv",
                file=StringIO(csv_bytes.decode('utf-8')),
                headers={"content-type": "text/csv"}
            )

            # Blobにアップロード
            result = await CompanyService.upload_csv_to_blob(
                company_id=company_id,
                file=csv_file
            )

            return result

        except Exception as e:
            logger.error(
                "Error converting text to CSV and uploading",
                extra={"error": str(e)},
                exc_info=True
            )
            raise AppException(
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                message="テキストファイルの変換とアップロード中にエラーが発生しました",
                context={"error": str(e)}
            ) 