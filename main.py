from fastapi import (
    FastAPI,
    File,
    UploadFile,
    Form,
    Cookie,
    Response,
    Depends,
    HTTPException,
)
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import uuid
import uvicorn
import shutil
import re
import numpy as np
from typing import Optional, Dict
import asyncio
from pathlib import Path
from pydantic import BaseModel
from utils import (
    read_file,
    get_unnamed_columns,
    get_mismatched_columns,
    cleanup_expired_files_periodically,
)


TEMP_DIR = Path("./temp_files")
TEMP_DIR.mkdir(exist_ok=True)

session_data_store = {}


class CalculatedColumnRequest(BaseModel):
    formula: str
    column_name: str


@asynccontextmanager
async def lifespan(app: FastAPI):
    cleanup_task = asyncio.create_task(cleanup_expired_files_periodically())
    yield
    cleanup_task.cancel()
    try:
        await cleanup_task
    except asyncio.CancelledError:
        pass


app = FastAPI(title="File Processor API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_session_data(session_id: Optional[str] = Cookie(None)):
    if not session_id or session_id not in session_data_store:
        raise HTTPException(
            status_code=401,
            detail="Session not found or expired. Please upload files first.",
        )
    return session_data_store[session_id]


@app.get("/")
async def root():
    """Base route endpoint that returns a welcome message."""
    return {"message": "Welcome to the Abhitech Statistical Backend"}


@app.post("/process-files/")
async def process_files(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    remove_unnamed: bool = Form(False),
    remove_mismatched: bool = Form(False),
    session_id: Optional[str] = Cookie(None),
    response: Response = None,
):
    """
    Process two files (CSV, Excel, or Parquet) and handle unnamed and mismatched columns.
    Uses session cookies to manage multiple users without authentication.
    """
    if not session_id:
        session_id = str(uuid.uuid4())
        response.set_cookie(key="session_id", value=session_id)

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

        df1 = read_file(file1)
        df2 = read_file(file2)

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

        for col in df1.select_dtypes(include=[np.number]).columns:
            df1[col] = df1[col].round(3)
        for col in df2.select_dtypes(include=[np.number]).columns:
            df2[col] = df2[col].round(3)

        session_data_store[session_id] = {
            "df1": df1,
            "df2": df2,
            "file1_name": file1.filename,
            "file2_name": file2.filename,
            "calculated_columns": [],
        }

        return {
            "message": "Files processed successfully",
            "session_id": session_id,
            "file1_info": {
                "filename": file1.filename,
                "shape": df1.shape,
                "columns": list(df1.columns),
                "preview": df1.head(10).to_dict(orient="records"),
            },
            "file2_info": {
                "filename": file2.filename,
                "shape": df2.shape,
                "columns": list(df2.columns),
                "preview": df2.head(10).to_dict(orient="records"),
            },
        }

    except Exception as e:
        if os.path.exists(temp_file1_path):
            os.remove(temp_file1_path)
        if os.path.exists(temp_file2_path):
            os.remove(temp_file2_path)
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/add-calculated-column/")
async def add_calculated_column(
    request: CalculatedColumnRequest, session_data: Dict = Depends(get_session_data)
):
    """
    Add a calculated column to both DataFrames using the provided formula.
    Uses session data from previous file processing.
    """
    try:
        df1 = session_data["df1"]
        df2 = session_data["df2"]
        column_name = request.column_name
        formula = request.formula

        if not column_name or not re.match(r"^[a-zA-Z0-9_]+$", column_name):
            return JSONResponse(
                status_code=400,
                content={
                    "error": "Column name can only contain letters, numbers and underscores"
                },
            )

        if len(column_name) > 30:
            return JSONResponse(
                status_code=400,
                content={"error": "Column name too long (max 30 characters)"},
            )

        if column_name in df1.columns or column_name in df2.columns:
            return JSONResponse(
                status_code=400, content={"error": "Column name already exists"}
            )

        validation_errors = validate_formula(formula, list(df1.columns))
        if validation_errors:
            return JSONResponse(
                status_code=400,
                content={
                    "error": f"Formula validation failed: {', '.join(validation_errors)}"
                },
            )

        processed_formula = process_formula(formula, df1.columns)

        try:
            df = df1
            df1[column_name] = eval(processed_formula)

            df = df2
            df2[column_name] = eval(processed_formula)

            session_data["df1"] = df1
            session_data["df2"] = df2

            session_data["calculated_columns"].append(
                {"name": column_name, "formula": formula}
            )

            return {
                "message": f"Column '{column_name}' added successfully",
                "preview": {
                    "file1": df1[column_name].head(5).tolist(),
                    "file2": df2[column_name].head(5).tolist(),
                },
                "calculated_columns": session_data["calculated_columns"],
            }
        except Exception as e:
            return JSONResponse(
                status_code=400, content={"error": f"Error executing formula: {str(e)}"}
            )

    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


def validate_formula(formula, available_columns):
    """
    Validate the formula string for common errors.
    Similar to the validation in the React component.
    """
    errors = []

    if not formula:
        errors.append("Formula cannot be empty")
        return errors

    stack = []
    for char in formula:
        if char == "(":
            stack.append("(")
        elif char == ")":
            if len(stack) == 0:
                errors.append("Unbalanced parentheses - too many closing parentheses")
                break
            stack.pop()

    if stack:
        errors.append("Unbalanced parentheses - missing closing parentheses")

    functions_regex = r"AVERAGE\(\s*\)|SUM\(\s*\)|MIN\(\s*\)|MAX\(\s*\)"
    if re.search(functions_regex, formula):
        errors.append("Functions cannot be empty")

    division_by_zero_regex = r"/\s*0+(?!\d)"
    if re.search(division_by_zero_regex, formula):
        errors.append("Division by zero is not allowed")

    column_refs = re.findall(r"\[([^\]]+)\]", formula)
    for col in column_refs:
        if col not in available_columns:
            errors.append(f"Column '{col}' not found in dataset")

    return errors


def process_formula(formula, available_columns):
    """
    Process the formula to make it executable in Python/Pandas context.
    Converts UI formula syntax to executable Python code.
    """
    processed = formula
    for col in available_columns:
        processed = processed.replace(f"[{col}]", f'df["{col}"]')

    processed = processed.replace("AVERAGE(", "np.mean([")
    processed = processed.replace("SUM(", "np.sum([")
    processed = processed.replace("MIN(", "np.min([")
    processed = processed.replace("MAX(", "np.max([")

    processed = processed.replace(")", "])")
    return processed


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
