from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import uvicorn
import asyncio
from api.file_processing import router as file_processing_router
from api.calculated import router as calculated_router
from api.dependency import router as dependency_router
from api.auth import router as auth_router
from utils import cleanup_expired_files_periodically


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

app.include_router(file_processing_router)
app.include_router(calculated_router)
app.include_router(dependency_router)
app.include_router(auth_router)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)