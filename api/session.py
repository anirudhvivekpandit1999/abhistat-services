from fastapi import Request, Cookie, Header, HTTPException
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from core.db import db
from core.config import TEMP_DIR
from pathlib import Path
import logging
import pickle
import base64
import pandas as pd
import numpy as np
import shutil

logger = logging.getLogger(__name__)

sessions_collection = db['sessions']
SESSION_EXPIRY_HOURS = 24

def save_dataframe_to_disk(session_id: str, key: str, df: pd.DataFrame) -> str:
    """Save dataframe to parquet file and return the path."""
    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    parquet_path = session_dir / f"{key}.parquet"
    df.to_parquet(parquet_path, compression='snappy', index=False)
    return str(parquet_path)

def load_dataframe_from_disk(file_path: str) -> Optional[pd.DataFrame]:
    """Load dataframe from parquet file."""
    try:
        path = Path(file_path)
        if path.exists():
            return pd.read_parquet(path)
        else:
            logger.warning(f"Dataframe file not found: {file_path}")
            return None
    except Exception as e:
        logger.error(f"Error loading dataframe from {file_path}: {str(e)}")
        return None

def serialize_dataframe(df, session_id: str = None, key: str = None):
    """Serialize dataframe - now saves to disk instead of pickling."""
    if isinstance(df, pd.DataFrame):
        if session_id and key:
            file_path = save_dataframe_to_disk(session_id, key, df)
            return {
                "_type": "dataframe_path",
                "_path": file_path
            }
        else:
            return {
                "_type": "dataframe",
                "_data": base64.b64encode(pickle.dumps(df)).decode('utf-8')
            }
    return df

def deserialize_dataframe(data):
    """Deserialize dataframe - loads from parquet or unpickles."""
    if isinstance(data, dict):
        if data.get("_type") == "dataframe_path":
            file_path = data.get("_path")
            if file_path:
                df = load_dataframe_from_disk(file_path)
                if df is not None:
                    return df
                else:
                    logger.error(f"Failed to load dataframe from {file_path}")
                    raise ValueError(f"Dataframe file not found: {file_path}")
        elif data.get("_type") == "dataframe":
            return pickle.loads(base64.b64decode(data["_data"].encode('utf-8')))
    return data

def serialize_session_data(data: Dict[str, Any], session_id: str = None) -> Dict[str, Any]:
    serialized = {}
    for key, value in data.items():
        if isinstance(value, pd.DataFrame):

            serialized[key] = serialize_dataframe(value, session_id, key)
        elif isinstance(value, dict):
            serialized[key] = serialize_session_data(value, session_id)
        elif isinstance(value, list):
            serialized[key] = [
                serialize_dataframe(item, session_id, f"{key}_{i}") if isinstance(item, pd.DataFrame) else item
                for i, item in enumerate(value)
            ]
        elif isinstance(value, (np.integer, np.floating)):
            serialized[key] = float(value.item()) if isinstance(value, np.floating) else int(value.item())
        elif isinstance(value, np.ndarray):
            serialized[key] = value.tolist()
        elif pd.isna(value):
            serialized[key] = None
        else:
            try:
                serialized[key] = value
            except Exception:
                serialized[key] = str(value)
    return serialized

def deserialize_session_data(data: Dict[str, Any]) -> Dict[str, Any]:
    deserialized = {}
    for key, value in data.items():
        if isinstance(value, dict):
            if value.get("_type") in ("dataframe", "dataframe_path"):
                deserialized[key] = deserialize_dataframe(value)
            else:
                deserialized[key] = deserialize_session_data(value)
        elif isinstance(value, list):
            deserialized[key] = [
                deserialize_dataframe(item) if isinstance(item, dict) and item.get("_type") in ("dataframe", "dataframe_path") else item
                for item in value
            ]
        else:
            deserialized[key] = value
    return deserialized

def create_session(session_id: str, data: Dict[str, Any]) -> bool:
    try:
        expiry = datetime.now() + timedelta(hours=SESSION_EXPIRY_HOURS)
        serialized_data = serialize_session_data(data, session_id)
        session_doc = {
            "_id": session_id,
            "data": serialized_data,
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
        
        serialized_data = session_doc.get("data", {})
        return deserialize_session_data(serialized_data)
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
        serialized_data = serialize_session_data(data, session_id)
        
        test_doc = {"data": serialized_data}
        test_size = len(str(test_doc))
        if test_size > 15 * 1024 * 1024:
            logger.warning(f"Session {session_id} data is very large ({test_size} bytes), but should be under 16MB with parquet storage")
        
        sessions_collection.update_one(
            {"_id": session_id},
            {
                "$set": {
                    "data": serialized_data,
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
        session_dir = TEMP_DIR / session_id
        if session_dir.exists():
            try:
                shutil.rmtree(session_dir)
            except Exception as e:
                logger.warning(f"Error deleting session directory {session_dir}: {str(e)}")
        return True
    except Exception as e:
        logger.error(f"Error deleting session {session_id}: {str(e)}")
        return False

def cleanup_expired_sessions():
    try:
        expired_sessions = sessions_collection.find({"expires_at": {"$lt": datetime.now()}})
        deleted_count = 0
        for session in expired_sessions:
            session_id = session.get("_id")
            if session_id:
                session_dir = TEMP_DIR / session_id
                if session_dir.exists():
                    try:
                        shutil.rmtree(session_dir)
                    except Exception as e:
                        logger.warning(f"Error deleting session directory {session_dir}: {str(e)}")
                deleted_count += 1
        
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
    session_data = None
    
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
    
    if not effective_session_id or not session_data:
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Session not found or expired. Please upload files first.",
                "code": "SESSION_NOT_FOUND"
            }
        )
    
    return session_data
