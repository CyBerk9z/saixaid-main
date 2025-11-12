import pandas as pd
from azure.storage.blob import BlobServiceClient
from io import BytesIO
from datetime import timedelta
import tiktoken

# Azure Blob StorageからCSVをダウンロードする関数
def download_csv_from_blob(blob_url: str, connection_string: str) -> pd.DataFrame:
    blob_service_client = BlobServiceClient.from_connection_string(connection_string)

    # Blob URLを解析
    def parse_blob_url(url):
        parts = url.split('/')
        container_name = parts[3]
        blob_name = '/'.join(parts[4:])
        return container_name, blob_name

    container_name, blob_name = parse_blob_url(blob_url)
    blob_client = blob_service_client.get_blob_client(container=container_name, blob=blob_name)
    stream = blob_client.download_blob().readall()

    df = pd.read_csv(BytesIO(stream))
    return df

# CSVデータをチャンクに分割する関数
# def preprocess_and_chunk_data(df: pd.DataFrame, chunk_row_size: int = 7) -> list:
#     rows = df.astype(str).agg(' '.join, axis=1).tolist()
#     chunks = [
#         "\n".join(rows[i:i + chunk_row_size])
#         for i in range(0, len(rows), chunk_row_size)
#     ]
#     return chunks

def preprocess_and_chunk_data(df: pd.DataFrame, time_window_minutes: int = 5, target_chunk_tokens: int = 1000) -> list:
    df = df.copy()

    # 後で変えるために一旦定義
    text_fields = ['Timestamp', 'User ID', 'User Name', 'Channel', 'Message', 'Attachments']
    timestamp_field = 'Timestamp'
    thread_field = 'Parent Message Timestamp'

    # TSをフォーマットする
    df[timestamp_field] = pd.to_datetime(df[timestamp_field], errors='coerce')

    # Timestampをdatetime型に変換
    df[thread_field] = pd.to_datetime(df[thread_field], errors='coerce').fillna(df[timestamp_field])


    # 時系列でソートをかける
    df = df.sort_values(timestamp_field)

    # トークン化してトークン量を把握する
    encoding = tiktoken.get_encoding("cl100k_base")

    chunks = []
    current_chunk = []
    current_chunk_tokens = 0
    last_ts = None

    thread_groups = df.groupby(thread_field)

    for _, group in thread_groups:
        first_ts = group.iloc[0][timestamp_field]

        # グループ用のテキストラインを用意する
        text_lines = group[text_fields].astype(str).agg(' '.join, axis=1).tolist()
        combined_text = "\n".join(text_lines)
        combined_tokens = len(encoding.encode(combined_text))

        # 現在のチャンク＋このスレッドが目標を超えた場合、現在のチャンクをフラッシュする。
        if current_chunk and (current_chunk_tokens + combined_tokens > target_chunk_tokens):
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_chunk_tokens = 0
            last_ts = None

        # Iこのスレッドだけで規定値を超える場合は分割する
        if combined_tokens > target_chunk_tokens:
            temp_chunk = []
            temp_tokens = 0
            for line in text_lines:
                line_tokens = len(encoding.encode(line))
                if temp_tokens + line_tokens > target_chunk_tokens:
                    chunks.append("\n".join(temp_chunk))
                    temp_chunk = [line]
                    temp_tokens = line_tokens
                else:
                    temp_chunk.append(line)
                    temp_tokens += line_tokens
            if temp_chunk:
                chunks.append("\n".join(temp_chunk))
            continue

        # 現在のチャンクが空でない場合の時間ベースのフラッシュ
        if last_ts is not None and (first_ts - last_ts > timedelta(minutes=time_window_minutes)):
            chunks.append("\n".join(current_chunk))
            current_chunk = []
            current_chunk_tokens = 0

        # 現在のチャンクに追加
        current_chunk.extend(text_lines)
        current_chunk_tokens += combined_tokens
        group_max_ts = group[timestamp_field].max()

        # group_max_ts が NaT の場合をスキップ
        if pd.isna(group_max_ts):
            continue

        # last_ts が None または NaT の場合は group_max_ts を設定
        if last_ts is None or pd.isna(last_ts):
            last_ts = group_max_ts
        else:
            last_ts = max(last_ts, group_max_ts)

    # 最後のチャンクを追加
    if current_chunk:
        chunks.append("\n".join(current_chunk))

    # ログ
    print(f"===チャンク量===: {len(chunks)}")
    token_counts = [len(encoding.encode(chunk)) for chunk in chunks]
    print(f"===チャンクあたりの平均トークン量===: {sum(token_counts) // len(token_counts)}")
    print(f"===最低トークン===: {min(token_counts)}, ===最高トークン===: {max(token_counts)}")

    return chunks