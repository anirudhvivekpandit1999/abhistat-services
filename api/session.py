from fastapi import Request, Cookie, Header, HTTPException
from typing import Optional

session_data_store = {}

async def get_session_data(
    request: Request,
    session_id: Optional[str] = Cookie(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
):
    effective_session_id = None
    if session_id and session_id in session_data_store:
        effective_session_id = session_id
    if not effective_session_id and x_session_id and x_session_id in session_data_store:
        effective_session_id = x_session_id
    query_session = request.query_params.get("session_id")
    if not effective_session_id and query_session and query_session in session_data_store:
        effective_session_id = query_session
    if not effective_session_id and request.method in {"POST", "PUT"}:
        try:
            body = await request.json()
            body_session = body.get("session_id")
            if body_session and body_session in session_data_store:
                effective_session_id = body_session
        except Exception:
            pass
    if not effective_session_id:
        available_sessions = list(session_data_store.keys())
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Session not found or expired. Please upload files first.",
                "debug_info": {
                    "cookie_session": session_id,
                    "header_session": x_session_id,
                    "available_sessions": available_sessions[:5] if available_sessions else [],
                    "session_count": len(available_sessions)
                }
            }
        )
    return session_data_store[effective_session_id] 