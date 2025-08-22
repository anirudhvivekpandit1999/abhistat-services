from fastapi import APIRouter, HTTPException
from datetime import datetime
from Abhitech_Statistical_Tool_Backend.models.schemas import RegisterRequest, LoginRequest, GoogleLoginRequest
from Abhitech_Statistical_Tool_Backend.core.db import external_users
from Abhitech_Statistical_Tool_Backend.core.security import get_password_hash, verify_password, create_access_token

router = APIRouter()

@router.post("/register")
async def register_user(data: RegisterRequest):
    if data.password != data.confirm_password:
        raise HTTPException(status_code=400, detail="Passwords do not match.")
    if external_users.find_one({"email": data.email}):
        raise HTTPException(status_code=400, detail="Email already registered.")
    user = {
        "name": data.name,
        "email": data.email,
        "phone": data.phone,
        "password": get_password_hash(data.password),
        "created_at": datetime.now(),
        "google_id": None
    }
    external_users.insert_one(user)
    return {"message": "User registered successfully."}

@router.post("/login")
async def login_user(data: LoginRequest):
    user = external_users.find_one({"email": data.email})
    if not user or not verify_password(data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return {"access_token": token, "token_type": "bearer", "user": {"name": user["name"], "email": user["email"], "phone": user["phone"], "access": "External" }, "message": "User logged in successfully"}

@router.post("/google-login")
async def google_login(data: GoogleLoginRequest):
    from google.oauth2 import id_token
    from google.auth.transport import requests as greq
    try:
        idinfo = id_token.verify_oauth2_token(data.token, greq.Request())
        email = idinfo["email"]
        name = idinfo.get("name", "")
        google_id = idinfo["sub"]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid Google token.")
    user = external_users.find_one({"email": email})
    if not user:
        user = {
            "name": name,
            "email": email,
            "phone": "",
            "password": None,
            "created_at": datetime.now(),
            "google_id": google_id
        }
        external_users.insert_one(user)
    token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return {"access_token": token, "token_type": "bearer", "user": {"name": user["name"], "email": user["email"], "phone": user.get("phone", "")}} 