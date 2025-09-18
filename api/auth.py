from fastapi import APIRouter, HTTPException
from datetime import datetime
from models.schemas import RegisterRequest, LoginRequest, GoogleLoginRequest
from core.db import external_users
from core.security import get_password_hash, verify_password, create_access_token
from google.oauth2 import id_token
from google.auth.transport import requests as greq
from core.config import GOOGLE_CLIENT_ID
    
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
    result = external_users.insert_one(user)
    user["_id"] = result.inserted_id
    
    token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return {
        "access_token": token, 
        "token_type": "bearer", 
        "user": {"name": user["name"], "email": user["email"], "phone": user["phone"], "access": "External"}, 
        "message": "User registered successfully."
    }

@router.post("/login")
async def login_user(data: LoginRequest):
    user = external_users.find_one({"email": data.email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign up first.")
    if not verify_password(data.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid password.")
    token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return {"access_token": token, "token_type": "bearer", "user": {"name": user["name"], "email": user["email"], "phone": user["phone"], "access": "External" }, "message": "User logged in successfully"}

@router.post("/google-login")
async def google_login(data: GoogleLoginRequest):
    try:
        idinfo = id_token.verify_oauth2_token(data.token, greq.Request(), GOOGLE_CLIENT_ID)
        email = idinfo["email"]
        name = idinfo.get("name", "")
        google_id = idinfo["sub"]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Google token: {str(e)}")
    
    user = external_users.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=404, detail="User not found. Please sign up first.")
    
    if not user.get("google_id"):
        external_users.update_one(
            {"_id": user["_id"]}, 
            {"$set": {"google_id": google_id}}
        )
    
    token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return {"access_token": token, "token_type": "bearer", "user": {"name": user["name"], "email": user["email"], "phone": user.get("phone", ""), "access": "External"}}

@router.post("/google-signup")
async def google_signup(data: GoogleLoginRequest):
    try:
        idinfo = id_token.verify_oauth2_token(data.token, greq.Request(), GOOGLE_CLIENT_ID)
        email = idinfo["email"]
        name = idinfo.get("name", "")
        google_id = idinfo["sub"]
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid Google token: {str(e)}")
    
    existing_user = external_users.find_one({"email": email})
    if existing_user:
        raise HTTPException(status_code=400, detail="Email already registered. Please use login instead.")
    
    user = {
        "name": name,
        "email": email,
        "phone": "",
        "password": None,
        "created_at": datetime.now(),
        "google_id": google_id
    }
    result = external_users.insert_one(user)
    user["_id"] = result.inserted_id
    
    token = create_access_token({"sub": str(user["_id"]), "email": user["email"]})
    return {"access_token": token, "token_type": "bearer", "user": {"name": user["name"], "email": user["email"], "phone": user.get("phone", ""), "access": "External"}, "message": "User registered successfully via Google."} 