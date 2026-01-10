from fastapi import APIRouter, File, UploadFile, Form, Cookie, Response
from fastapi.responses import JSONResponse
from typing import Optional
import uuid
import shutil
import numpy as np
import os
from core.config import TEMP_DIR
from utils import read_file, get_unnamed_columns, get_mismatched_columns
from api.session import create_session, update_session

router = APIRouter()

@router.post("/process-files")
async def process_files(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    remove_unnamed: bool = Form(False),
    remove_mismatched: bool = Form(False),
    sheet1: Optional[str] = Form(None),
    sheet2: Optional[str] = Form(None),
    session_id: Optional[str] = Cookie(None),
    response: Response = None,
):
    if not session_id:
        session_id = str(uuid.uuid4())
        is_production = os.getenv('ENVIRONMENT', 'production').lower() == 'production'
        response.set_cookie(
            key="session_id",
            value=session_id,
            httponly=True,
            samesite="lax",
            secure=is_production,
            max_age=86400,
            path="/"
        )
    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    temp_file1_path = session_dir / f"file1_{uuid.uuid4()}_{file1.filename}"
    temp_file2_path = session_dir / f"file2_{uuid.uuid4()}_{file2.filename}"
    try:
        with open(temp_file1_path, "wb") as buffer:
            shutil.copyfileobj(file1.file, buffer)
        with open(temp_file2_path, "wb") as buffer:
            shutil.copyfileobj(file2.file, buffer)
        file1.file.seek(0)
        file2.file.seek(0)
        df1 = read_file(file1, sheet_name=sheet1)
        df2 = read_file(file2, sheet_name=sheet2)
        unnamed_cols_df1 = get_unnamed_columns(df1)
        unnamed_cols_df2 = get_unnamed_columns(df2)
        if (unnamed_cols_df1 or unnamed_cols_df2) and not remove_unnamed:
            error_message = "Unnamed columns detected: "
            if unnamed_cols_df1:
                error_message += f"File '{file1.filename}' has unnamed columns at indexes {unnamed_cols_df1}. "
            if unnamed_cols_df2:
                error_message += f"File '{file2.filename}' has unnamed columns at indexes {unnamed_cols_df2}."
            return JSONResponse(status_code=400, content={"error": error_message})
        if remove_unnamed:
            df1 = df1.loc[:, ~df1.columns.str.contains("^Unnamed")]
            df2 = df2.loc[:, ~df2.columns.str.contains("^Unnamed")]
        mismatched_cols = get_mismatched_columns(df1, df2)
        if (
            mismatched_cols["only_in_df1"] or mismatched_cols["only_in_df2"]
        ) and not remove_mismatched:
            error_message = "Mismatched columns detected: "
            if mismatched_cols["only_in_df1"]:
                error_message += f"Columns only in '{file1.filename}': {mismatched_cols['only_in_df1']}. "
            if mismatched_cols["only_in_df2"]:
                error_message += f"Columns only in '{file2.filename}': {mismatched_cols['only_in_df2']}."
            return JSONResponse(status_code=400, content={"error": error_message})
        if remove_mismatched:
            common_columns = list(set(df1.columns) & set(df2.columns))
            df1 = df1[common_columns]
            df2 = df2[common_columns]
        numeric_cols_df1 = df1.select_dtypes(include=[np.number]).columns
        numeric_cols_df2 = df2.select_dtypes(include=[np.number]).columns
        if not numeric_cols_df1.empty:
            df1[numeric_cols_df1] = df1[numeric_cols_df1].round(3)
        if not numeric_cols_df2.empty:
            df2[numeric_cols_df2] = df2[numeric_cols_df2].round(3)
        df1_clean = df1.replace({np.nan: None, np.inf: None, -np.inf: None})
        df2_clean = df2.replace({np.nan: None, np.inf: None, -np.inf: None})
        
        session_data = {
            "df1": df1,
            "df2": df2,
            "file1_name": file1.filename,
            "file2_name": file2.filename,
            "calculated_columns": [],
        }
        
        if not create_session(session_id, session_data):
            return JSONResponse(
                status_code=500,
                content={"error": "Failed to create session. Please try again."}
            )
        
        preview1 = df1_clean.head(10).to_dict(orient="records")
        preview2 = df2_clean.head(10).to_dict(orient="records")
        
        total_size_estimate = len(str(preview1)) + len(str(preview2))
        if total_size_estimate > 5 * 1024 * 1024:
            preview1 = df1_clean.head(5).to_dict(orient="records")
            preview2 = df2_clean.head(5).to_dict(orient="records")
        
        return {
            "message": "Files processed successfully",
            "session_id": session_id,
            "file1_info": {
                "filename": file1.filename,
                "shape": df1.shape,
                "columns": list(df1.columns),
                "preview": preview1,
                "data": preview1,
            },
            "file2_info": {
                "filename": file2.filename,
                "shape": df2.shape,
                "columns": list(df2.columns),
                "preview": preview2,
                "data": preview2,
            },
        }
    except Exception as e:
        if temp_file1_path.exists():
            temp_file1_path.unlink()
        if temp_file2_path.exists():
            temp_file2_path.unlink()
        return JSONResponse(status_code=500, content={"error": str(e)})
