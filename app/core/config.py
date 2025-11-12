from pydantic_settings import BaseSettings
from pydantic import Field
from functools import lru_cache
import os

class TenantResourceMapping(BaseSettings):
    tenant_id: str
    db_url: str
    blob_connection_string: str
    openai_endpoint: str
    openai_api_key: str
    search_endpoint: str
    search_api_key: str   

class Settings(BaseSettings):
    # Project
    PROJECT_NAME: str = Field(default="Inthub")

    # ========= Microsoft Entra ID =========
    AZURE_TENANT_ID: str = os.getenv("AZURE_TENANT_ID")
    AZURE_TENANT_NAME: str = os.getenv("AZURE_TENANT_NAME")
    AZURE_CLIENT_ID: str = os.getenv("AZURE_CLIENT_ID")
    AZURE_OBJECT_ID: str = os.getenv("AZURE_OBJECT_ID")
    AZURE_CLIENT_SECRET: str = os.getenv("AZURE_CLIENT_SECRET")
    AZURE_ISSUER: str = os.getenv("AZURE_ISSUER")
    AZURE_B2C_SIGNUP_POLICY_NAME: str = os.getenv("AZURE_B2C_SIGNUP_POLICY_NAME")
    AZURE_B2C_SIGNIN_POLICY_NAME: str = os.getenv("AZURE_B2C_SIGNIN_POLICY_NAME")
    AZURE_B2C_EXTENSION_ID: str = os.getenv("AZURE_B2C_EXTENSION_ID")
    B2C_BASIC_USER: str = os.getenv("B2C_BASIC_USER")
    B2C_BASIC_PW: str = os.getenv("B2C_BASIC_PW")

    # ========= Azure PostgreSQL =========
    POSTGRES_USER: str = os.getenv("POSTGRES_USER")
    POSTGRES_HOST: str = os.getenv("POSTGRES_HOST")
    POSTGRES_PORT: int = os.getenv("POSTGRES_PORT")
    POSTGRES_DB: str = os.getenv("POSTGRES_DB")
    POSTGRES_PW: str = os.getenv("POSTGRES_PW")
    MASTER_DB_URL: str = os.getenv("MASTER_DB_URL")

    # Azure Blob Storage
    AZURE_STORAGE_CONNECTION_STRING: str = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
    AZURE_STORAGE_CONTAINER_NAME: str = os.getenv("AZURE_STORAGE_CONTAINER_NAME")
    AZURE_STORAGE_ACCOUNT_NAME: str = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    AZURE_STORAGE_ACCOUNT_KEY: str = os.getenv("AZURE_STORAGE_ACCOUNT_KEY")

    # ========= Azure OpenAI =========
    AZURE_OPENAI_API_KEY: str = os.getenv("AZURE_OPENAI_API_KEY")
    AZURE_OPENAI_API_ENDPOINT: str = os.getenv("AZURE_OPENAI_API_ENDPOINT")
    AZURE_OPENAI_API_VERSION: str = os.getenv("AZURE_OPENAI_API_VERSION")
    AZURE_OPENAI_API_DEPLOYMENT_NAME: str = os.getenv("AZURE_OPENAI_API_DEPLOYMENT_NAME")

    # Azure AI Search
    AZURE_SEARCH_SERVICE_ENDPOINT: str = os.getenv("AZURE_SEARCH_SERVICE_ENDPOINT")
    AZURE_SEARCH_ADMIN_KEY: str = os.getenv("AZURE_SEARCH_ADMIN_KEY")
    AZURE_SEARCH_INDEX_NAME: str = os.getenv("AZURE_SEARCH_INDEX_NAME")
    AZURE_SEARCH_DEPLOYMENT_NAME: str = os.getenv("AZURE_SEARCH_DEPLOYMENT_NAME")

    # Slack
    SLACK_CLIENT_ID: str = os.getenv("SLACK_CLIENT_ID")
    SLACK_CLIENT_SECRET: str = os.getenv("SLACK_CLIENT_SECRET")
    SLACK_REDIRECT_URI : str = os.getenv("SLACK_REDIRECT_URI")
    SLACK_BOT_TOKEN: str = os.getenv("SLACK_BOT_TOKEN")
    SLACK_SIGNING_SECRET: str = os.getenv("SLACK_SIGNING_SECRET")

    # Microsoft Teams
    MICROSOFT_APP_ID: str = os.getenv("MICROSOFT_APP_ID")
    MICROSOFT_APP_PASSWORD: str = os.getenv("MICROSOFT_APP_PASSWORD")


    # Environment
    ENVIRONMENT: str = os.getenv("ENVIRONMENT")

    # Key Vault
    AZURE_KEY_VAULT_NAME: str = os.getenv("AZURE_KEY_VAULT_NAME")

    class Config:
        case_sensitive = True # 環境変数の大文字小文字を区別する
        env_file = ".env" # 環境変数のファイル名

@lru_cache # 関数の結果をキャッシュする
def get_settings():
    return Settings()

settings = get_settings()
