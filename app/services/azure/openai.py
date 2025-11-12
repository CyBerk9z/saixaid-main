# app/services/openai.py
from openai import AzureOpenAI
from app.core.config import settings

# Azure OpenAIクライアントの初期化関数
def get_azure_openai_client():
    return AzureOpenAI(
        api_key=settings.AZURE_OPENAI_API_KEY,
        api_version="2024-10-21",
        azure_endpoint=settings.AZURE_OPENAI_API_ENDPOINT
    )