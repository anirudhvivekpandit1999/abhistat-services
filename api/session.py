from fastapi import Request, Cookie, Header, HTTPException
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from core.db import db
import logging

logger = logging.getLogger(__name__)

sessions_collection = db['sessions']
SESSION_EXPIRY_HOURS = 24

def create_session(session_id: str, data: Dict[str, Any]) -> bool:
    try:
        expiry = datetime.now() + timedelta(hours=SESSION_EXPIRY_HOURS)
        session_doc = {
            "_id": session_id,
            "data": data,
            "created_at": datetime.now(),
            "expires_at": expiry,
            "last_accessed": datetime.now()
        }
        sessions_collection.replace_one(
            {"_id": session_id},
            session_doc,
            upsert=True
        )
        return True
    except Exception as e:
        logger.error(f"Error creating session {session_id}: {str(e)}")
        return False

def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    try:
        session_doc = sessions_collection.find_one({"_id": session_id})
        if not session_doc:
            return None
        
        if datetime.now() > session_doc.get("expires_at", datetime.now()):
            sessions_collection.delete_one({"_id": session_id})
            return None
        
        sessions_collection.update_one(
            {"_id": session_id},
            {"$set": {"last_accessed": datetime.now()}}
        )
        
        return session_doc.get("data")
    except Exception as e:
        logger.error(f"Error getting session {session_id}: {str(e)}")
        return None

def update_session(session_id: str, data: Dict[str, Any]) -> bool:
    try:
        session_doc = sessions_collection.find_one({"_id": session_id})
        if not session_doc:
            return False
        
        if datetime.now() > session_doc.get("expires_at", datetime.now()):
            sessions_collection.delete_one({"_id": session_id})
            return False
        
        expiry = datetime.now() + timedelta(hours=SESSION_EXPIRY_HOURS)
        sessions_collection.update_one(
            {"_id": session_id},
            {
                "$set": {
                    "data": data,
                    "last_accessed": datetime.now(),
                    "expires_at": expiry
                }
            }
        )
        return True
    except Exception as e:
        logger.error(f"Error updating session {session_id}: {str(e)}")
        return False

def delete_session(session_id: str) -> bool:
    try:
        sessions_collection.delete_one({"_id": session_id})
        return True
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {str(e)}")
        return False

def cleanup_expired_sessions():
    try:
        result = sessions_collection.delete_many({"expires_at": {"$lt": datetime.now()}})
        return result.deleted_count
    except Exception as e:
        logger.error(f"Error cleaning up expired sessions: {str(e)}")
        return 0

async def get_session_data(
    request: Request,
    session_id: Optional[str] = Cookie(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    body_session_id: Optional[str] = None,
) -> Dict[str, Any]:
    effective_session_id = None
    
    cookie_val = session_id
    header_val = x_session_id
    
    if not cookie_val:
        cookie_val = request.cookies.get("session_id")
    if not header_val:
        header_val = request.headers.get("X-Session-ID")
    
    if cookie_val:
        session_data = get_session(cookie_val)
        if session_data:
            effective_session_id = cookie_val
    
    if not effective_session_id and header_val:
        session_data = get_session(header_val)
        if session_data:
            effective_session_id = header_val
    
    if not effective_session_id:
        query_session = request.query_params.get("session_id")
        if query_session:
            session_data = get_session(query_session)
            if session_data:
                effective_session_id = query_session
    
    if not effective_session_id and body_session_id:
        session_data = get_session(body_session_id)
        if session_data:
            effective_session_id = body_session_id
    
    if not effective_session_id and request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
            body_session = body.get("session_id")
            if body_session:
                session_data = get_session(body_session)
                if session_data:
                    effective_session_id = body_session
        except Exception:
            pass
    
    if not effective_session_id:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Session not found or expired. Please upload files first.",
                "code": "SESSION_NOT_FOUND"
            }
        )
    
    return get_session(effective_session_id)
