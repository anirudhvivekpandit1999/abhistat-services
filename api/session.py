from fastapi import Request, Cookie, Header, HTTPException
from typing import Optional

session_data_store = {}

async def get_session_data(
    request: Request,
    session_id: Optional[str] = Cookie(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
    body_session_id: Optional[str] = None,
):
    effective_session_id = None
    
    cookie_val = session_id
    header_val = x_session_id
    
    if not cookie_val:
        cookie_val = request.cookies.get("session_id")
    if not header_val:
        header_val = request.headers.get("X-Session-ID")
    
    if cookie_val and cookie_val in session_data_store:
        effective_session_id = cookie_val
    
    if not effective_session_id and header_val and header_val in session_data_store:
        effective_session_id = header_val
    
    if not effective_session_id:
        query_session = request.query_params.get("session_id")
        if query_session and query_session in session_data_store:
            effective_session_id = query_session
    
    if not effective_session_id and body_session_id and body_session_id in session_data_store:
        effective_session_id = body_session_id
    
    if not effective_session_id and request.method in {"POST", "PUT", "PATCH"}:
        try:
            body = await request.json()
            body_session = body.get("session_id")
            if body_session and body_session in session_data_store:
                effective_session_id = body_session
        except Exception:
            pass
    
    if not effective_session_id:
        available_sessions = list(session_data_store.keys())
        checking_ids = [s for s in [cookie_val, header_val, body_session_id] if s]
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Session not found or expired. Please upload files first.",
                "debug_info": {
                    "cookie_session": cookie_val,
                    "header_session": header_val,
                    "body_session": body_session_id,
                    "available_sessions": available_sessions[:5] if available_sessions else [],
                    "session_count": len(available_sessions),
                    "checking_for": checking_ids
                }
            }
        )
    
    return session_data_store[effective_session_id] 