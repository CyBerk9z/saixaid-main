from app.services.azure.blob import download_csv_from_blob, preprocess_and_chunk_data
from app.core.config import settings

# Azure Blob Storageの接続文字列（Azureポータルで確認）
connection_string = settings.AZURE_STORAGE_CONNECTION_STRING

# ダウンロードしたいBlobのURLを指定
blob_url = "https://inthubstoragemytest.blob.core.windows.net/mynewcontainer/RAG Index Creation Messages.csv"

# CSVファイルのダウンロード
df = download_csv_from_blob(blob_url, connection_string)
print("ダウンロードしたCSVの内容:")
print(df.head())

# チャンク化のテスト
chunks = preprocess_and_chunk_data(df, chunk_size=500)
print("\n生成されたチャンクの例（先頭1つ）:")
print(chunks[0])
