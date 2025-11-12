from pydantic import BaseModel

class SignUpRequest(BaseModel):
    email: str
    password: str
    confirm_password: str
    name: str
    company_id: str
    role: str

class SignUpResponse(BaseModel):
    name: str
    company_id: str
    email: str
    azure_user_id: str
    role: str

class SignInResponse(BaseModel):
    name: str
    company_id: str
    access_token: str
    azure_user_id: str
    role: str
    refresh_token: str

class AzureUser(BaseModel):
    azure_user_id: str
    name: str
    company_id: str
    role: str

class CurrentUserResponse(BaseModel):
    name: str
    company_id: str
    user_id: str
    role: str

class RefreshTokenRequest(BaseModel):
    refresh_token: str

class RefreshTokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    id_token: str