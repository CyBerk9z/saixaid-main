from pydantic import BaseModel
from typing import Dict, Any, List
from datetime import datetime

class BuildIndexRequest(BaseModel) :
    company_id: str
    file_id: str

class DeleteIndexRequest(BaseModel) :
    file_id: str
