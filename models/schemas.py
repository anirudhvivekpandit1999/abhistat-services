from pydantic import BaseModel, EmailStr
from typing import Optional, List

class DependencyModelRequest(BaseModel):
    dependent_variables: List[str]
    independent_variables: List[str]
    session_id: Optional[str] = None

class CalculatedColumnRequest(BaseModel):
    formula: str
    column_name: str
    formula_elements: List

class BatchCalculatedColumnsRequest(BaseModel):
    columns: List[CalculatedColumnRequest]
    session_id: Optional[str] = None

class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    phone: str
    password: str
    confirm_password: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class GoogleLoginRequest(BaseModel):
    token: str 