from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.exceptions import RequestValidationError
from contextlib import asynccontextmanager
import uvicorn
import asyncio
import traceback
import logging
from api.file_processing import router as file_processing_router
from api.calculated import router as calculated_router
from api.dependency import router as dependency_router
from api.auth import router as auth_router
from api.payments import router as payments_router
from utils import cleanup_expired_files_periodically

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(cleanup_expired_files_periodically())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass

app = FastAPI(title="File Processor API", lifespan=lifespan, root_path="/api")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:5174",
        "https://13.203.202.28",
        "https://abhistat.com",
        "https://www.abhistat.com",
        "http://abhistat.com",
        "http://www.abhistat.com"
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=[
        "Accept",
        "Accept-Language",
        "Content-Language",
        "Content-Type",
        "Authorization",
        "X-Session-ID",
        "Cookie",
        "X-Requested-With",
        "Origin",
        "Access-Control-Request-Method",
        "Access-Control-Request-Headers"
    ],
)

@app.get("/")
async def root():
    return {"message": "Welcome to the Abhitech Statistical Backend"}

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    import os
    is_debug = os.getenv("DEBUG", "false").lower() == "true"
    logger.error(f"Unhandled exception: {str(exc)}\n{traceback.format_exc()}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": str(exc),
            "detail": traceback.format_exc() if is_debug else "Internal server error"
        }
    )

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={"error": "Validation error", "detail": exc.errors()}
    )

app.include_router(file_processing_router)
app.include_router(calculated_router)
app.include_router(dependency_router)
app.include_router(auth_router)
app.include_router(payments_router)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)