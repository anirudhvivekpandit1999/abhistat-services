from fastapi import (
    FastAPI,
    File,
    UploadFile,
    Form,
    Cookie,
    Response,
    Depends,
    HTTPException,
    Header,
    Request,
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
from typing import Optional, Dict, List
import asyncio
from pathlib import Path
from pydantic import BaseModel
# import logging
from .utils import (
    read_file,
    get_unnamed_columns,
    get_mismatched_columns,
    cleanup_expired_files_periodically,
    process_formula,
    validate_formula,
    bootstrap_all_columns
)

TEMP_DIR = Path("./temp_files")
TEMP_DIR.mkdir(exist_ok=True)

# logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

session_data_store = {}

class DependencyModelRequest(BaseModel):
    dependent_variables: List[str]
    independent_variables: List[str]
    session_id: Optional[str] = None

class CalculatedColumnRequest(BaseModel):
    formula: str
    column_name: str
    formula_elements: List

class BatchCalculatedColumnsRequest(BaseModel):
    columns: List[CalculatedColumnRequest]
    session_id: Optional[str] = None


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

async def get_session_data(
    request: Request,
    session_id: Optional[str] = Cookie(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
):
    # logging.info("Starting session data retrieval process")
    effective_session_id = None
    
    session_sources = [
        session_id,
        x_session_id,
        request.query_params.get("session_id")
    ]
    
    for sid in session_sources:
        if sid and sid in session_data_store:
            effective_session_id = sid
            # logging.info(f"Session ID from source is valid: {effective_session_id}")
            break
    
    if not effective_session_id and request.method in {"POST", "PUT"}:
        try:
            body = await request.json()
            body_session = body.get("session_id")
            if body_session and body_session in session_data_store:
                effective_session_id = body_session
                # logging.info(f"Session ID from request body is valid: {effective_session_id}")
        except Exception:
            # logging.warning(f"Failed to parse session ID from request body: {str(e)}")
            pass
    
    if not effective_session_id:
        available_sessions = list(session_data_store.keys())
        # logging.warning("No valid session ID found in any source")
        raise HTTPException(
            status_code=401,
            detail={
                "error": "Session not found or expired. Please upload files first.",
                "debug_info": {
                    "cookie_session": session_id,
                    "header_session": x_session_id,
                    "available_sessions": available_sessions[:5],
                    "session_count": len(available_sessions)
                }
            }
        )
    
    # logging.info(f"Successfully retrieved session data for session ID: {effective_session_id}")
    return session_data_store[effective_session_id]


@app.get("/")
async def root():
    return {"message": "Welcome to the Abhitech Statistical Backend"}


@app.post("/process-files")
async def process_files(
    file1: UploadFile = File(...),
    file2: UploadFile = File(...),
    remove_unnamed: bool = Form(False),
    remove_mismatched: bool = Form(False),
    session_id: Optional[str] = Cookie(None),
    response: Response = None,
):
    # logging.info("Starting file processing")
    
    if not session_id:
        session_id = str(uuid.uuid4())
        # logging.info(f"Generated new session ID: {session_id}")
        
        response.set_cookie(
            key="session_id", 
            value=session_id,
            httponly=False,
            samesite="lax",
            max_age=86400
        )
        # logging.info("Session ID cookie set successfully")

    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    # logging.info(f"Session directory created: {session_dir}")

    temp_file1_path = session_dir / f"file1_{uuid.uuid4()}_{file1.filename}"
    temp_file2_path = session_dir / f"file2_{uuid.uuid4()}_{file2.filename}"
    # logging.info(f"Temporary file paths created: {temp_file1_path}, {temp_file2_path}")

    try:
        for file_obj, temp_path in [(file1, temp_file1_path), (file2, temp_file2_path)]:
            with open(temp_path, "wb") as buffer:
                shutil.copyfileobj(file_obj.file, buffer)
            file_obj.file.seek(0)
        # logging.info(f"Files saved: {temp_file1_path}, {temp_file2_path}")

        # logging.info("Reading files into DataFrames")
        df1 = read_file(file1)
        df2 = read_file(file2)
        # logging.info(f"Files read successfully: {file1.filename}, {file2.filename}")

        unnamed_cols_df1 = get_unnamed_columns(df1)
        unnamed_cols_df2 = get_unnamed_columns(df2)

        if (unnamed_cols_df1 or unnamed_cols_df2) and not remove_unnamed:
            error_parts = []
            if unnamed_cols_df1:
                error_parts.append(f"File '{file1.filename}' has unnamed columns at indexes {unnamed_cols_df1}.")
            if unnamed_cols_df2:
                error_parts.append(f"File '{file2.filename}' has unnamed columns at indexes {unnamed_cols_df2}.")
            error_message = "Unnamed columns detected: " + " ".join(error_parts)
            # logging.warning(error_message)
            return JSONResponse(status_code=400, content={"error": error_message})

        if remove_unnamed:
            unnamed_mask1 = ~df1.columns.str.contains("^Unnamed", regex=True)
            unnamed_mask2 = ~df2.columns.str.contains("^Unnamed", regex=True)
            df1 = df1.loc[:, unnamed_mask1]
            df2 = df2.loc[:, unnamed_mask2]
            # logging.info("Unnamed columns removed successfully")

        mismatched_cols = get_mismatched_columns(df1, df2)

        if (mismatched_cols["only_in_df1"] or mismatched_cols["only_in_df2"]) and not remove_mismatched:
            error_parts = []
            if mismatched_cols["only_in_df1"]:
                error_parts.append(f"Columns only in '{file1.filename}': {mismatched_cols['only_in_df1']}.")
            if mismatched_cols["only_in_df2"]:
                error_parts.append(f"Columns only in '{file2.filename}': {mismatched_cols['only_in_df2']}.")
            error_message = "Mismatched columns detected: " + " ".join(error_parts)
            # logging.warning(error_message)
            return JSONResponse(status_code=400, content={"error": error_message})

        if remove_mismatched:
            common_columns = list(set(df1.columns).intersection(set(df2.columns)))
            df1 = df1[common_columns]
            df2 = df2[common_columns]
            # logging.info("Mismatched columns removed successfully")

        numeric_cols_df1 = df1.select_dtypes(include=[np.number]).columns
        numeric_cols_df2 = df2.select_dtypes(include=[np.number]).columns
        
        if len(numeric_cols_df1) > 0:
            df1.loc[:, numeric_cols_df1] = df1[numeric_cols_df1].round(3)
        if len(numeric_cols_df2) > 0:
            df2.loc[:, numeric_cols_df2] = df2[numeric_cols_df2].round(3)
        # logging.info("Numeric columns rounded successfully")

        replacement_dict = {np.nan: None, np.inf: None, -np.inf: None}
        df1_clean = df1.replace(replacement_dict)
        df2_clean = df2.replace(replacement_dict)

        session_data_store[session_id] = {
            "df1": df1,
            "df2": df2,
            "file1_name": file1.filename,
            "file2_name": file2.filename,
            "calculated_columns": [],
        }
        # logging.info(f"Session data stored successfully for session ID: {session_id}")

        return {
            "message": "Files processed successfully",
            "session_id": session_id,
            "file1_info": {
                "filename": file1.filename,
                "shape": df1.shape,
                "columns": list(df1.columns),
                "preview": df1_clean.head(10).to_dict(orient="records"),
            },
            "file2_info": {
                "filename": file2.filename,
                "shape": df2.shape,
                "columns": list(df2.columns),
                "preview": df2_clean.head(10).to_dict(orient="records"),
            },
        }

    except Exception as e:
        # logging.error(f"An error occurred during file processing: {str(e)}")
        for temp_path in [temp_file1_path, temp_file2_path]:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/save-calculated-columns")
async def save_calculated_columns(
    request: Request,
    data: BatchCalculatedColumnsRequest,
    session_data: Dict = Depends(get_session_data)
):
    try:
        # logging.info("Processing calculated columns request")
        df1 = session_data["df1"]
        df2 = session_data["df2"]
        new_columns = []
        errors = []
        
        original_columns = set(df1.columns).intersection(set(df2.columns))
        column_name_pattern = re.compile(r"^[a-zA-Z0-9_]+$")

        for column_request in data.columns:
            column_name = column_request.column_name
            formula = column_request.formula
            formula_elements = column_request.formula_elements
            
            unique_columns = {
                element["value"] for element in formula_elements 
                if element["type"] == "column"
            }
            
            for column_value in unique_columns:
                pattern = rf'(?<!\[)\b{re.escape(column_value)}\b(?!\])'
                formula = re.sub(pattern, f'[{column_value}]', formula)
                    
            # logging.info(f"Processing column: {column_name} with formula: {formula}")
                  
            if not column_name or not column_name_pattern.match(column_name):
                errors.append(f"Column name '{column_name}' can only contain letters, numbers and underscores")
                continue

            if len(column_name) > 30:
                errors.append(f"Column name '{column_name}' is too long (max 30 characters)")
                continue

            if column_name in original_columns:
                errors.append(f"Column name '{column_name}' already exists in the original dataset")
                continue
            
            validation_errors_df1 = validate_formula(formula, list(df1.columns))
            validation_errors_df2 = validate_formula(formula, list(df2.columns))
            
            if validation_errors_df1:
                errors.extend(f"Formula for '{column_name}' in first dataset: {error}" for error in validation_errors_df1)
                continue
                
            if validation_errors_df2:
                errors.extend(f"Formula for '{column_name}' in second dataset: {error}" for error in validation_errors_df2)
                continue

        if errors:
            # logging.error(f"Validation errors encountered: {errors}")
            return JSONResponse(
                status_code=400,
                content={"errors": errors}
            )
            
        processed_columns = []

        for column_request in data.columns:
            column_name = column_request.column_name
            formula = column_request.formula
            formula_elements = column_request.formula_elements
            
            for element in formula_elements:
                if element["type"] == "column":
                    column_value = element["value"]
                    formula = formula.replace(column_value, f'[{column_value}]')
            
            processed_formula = process_formula(formula)
            # logging.info(f"Processed formula for '{column_name}': {processed_formula}")

            try:    
                df1[column_name] = np.nan
                df2[column_name] = np.nan

                eval_globals = {"__builtins__": None}
                eval_locals = {"df": df1, "np": np}
                df1[column_name] = eval(processed_formula, eval_globals, eval_locals)
                
                eval_locals["df"] = df2
                df2[column_name] = eval(processed_formula, eval_globals, eval_locals)
                
                new_columns.append(column_name)
                processed_columns.append({"name": column_name, "formula": formula})

                # logging.info(f"Successfully added column: {column_name}")
                
            except Exception as e:
                # logging.error(f"Error executing formula for '{column_name}': {str(e)}")
                # logging.exception("Traceback for the error:")
                
                for col in new_columns:
                    df1.drop(columns=[col], inplace=True, errors='ignore')
                    df2.drop(columns=[col], inplace=True, errors='ignore')
                
                return JSONResponse(
                    status_code=400, 
                    content={"errors": [f"Error executing formula for '{column_name}': {str(e)}"]}
                )

        session_data["df1"] = df1
        session_data["df2"] = df2
        session_data["calculated_columns"] = processed_columns

        replacement_dict = {np.nan: None, np.inf: None, -np.inf: None}
        if new_columns:
            df1_preview = df1[new_columns].head(5).replace(replacement_dict)
            df2_preview = df2[new_columns].head(5).replace(replacement_dict)
            preview_data = {
                "file1": df1_preview.to_dict(orient="records"),
                "file2": df2_preview.to_dict(orient="records"),
            }
        else:
            preview_data = {"file1": {}, "file2": {}}

        # logging.info(f"Successfully added {len(new_columns)} calculated columns")
        return {
            "message": f"Successfully added {len(new_columns)} calculated columns",
            "new_columns": new_columns,
            "preview": preview_data
        }

    except Exception as e:
        # logging.exception("An unexpected error occurred while processing calculated columns")
        return JSONResponse(status_code=500, content={"errors": [str(e)]})

@app.get("/session-status")
async def session_status(
    session_data: Optional[Dict] = Depends(get_session_data),
):
    if session_data:
        return {
            "status": "active",
            "session_info": {
                "file1_name": session_data.get("file1_name"),
                "file2_name": session_data.get("file2_name"),
                "columns_count": len(session_data["df1"].columns) if "df1" in session_data else 0,
                "calculated_columns": len(session_data.get("calculated_columns", [])),
            }
        }
    return {"status": "no_active_session"}

@app.post("/save-dependency-model")
async def save_dependency_model(
    data: DependencyModelRequest,
    session_data: Dict = Depends(get_session_data)
):
    try:
        # logging.info(f"Processing dependency model with dependent vars: {data.dependent_variables} and independent vars: {data.independent_variables}")
        
        df1 = session_data["df1"].copy()
        df2 = session_data["df2"].copy()
        file1_name = session_data["file1_name"]
        file2_name = session_data["file2_name"]
        
        all_columns = set(df1.columns)
        requested_columns = set(data.dependent_variables + data.independent_variables)
        
        missing_columns = requested_columns - all_columns
        if missing_columns:
            return JSONResponse(
                status_code=400,
                content={"error": f"The following columns were not found in the dataset: {', '.join(missing_columns)}"}
            )
        
        session_data["dependency_model"] = {
            "dependent_variables": data.dependent_variables,
            "independent_variables": data.independent_variables
        }
        
        without_product_df = df1
        with_product_df = df2

        for df in [with_product_df, without_product_df]:
            for col in df.columns:
                if df[col].dtype == 'object':
                    try:
                        df[col] = df[col].astype(float)
                    except Exception:
                        pass

        bootstrap_results = bootstrap_all_columns(without_product_df, with_product_df, 1000)    

        replacement_dict = {np.nan: None, np.inf: None, -np.inf: None}
        with_product_df = with_product_df.replace(replacement_dict)
        without_product_df = without_product_df.replace(replacement_dict)
        
        return {
            "message": "Dependency model saved successfully",
            "model_info": {
                "dependent_variables": data.dependent_variables,
                "independent_variables": data.independent_variables,
            },
            "data_info": {
                "with_product": {
                    "name": file1_name,
                    "shape": with_product_df.shape,
                    "data": with_product_df.to_dict(orient="records"),
                },
                "without_product": {
                    "name": file2_name,
                    "shape": without_product_df.shape,
                    "data": without_product_df.to_dict(orient="records"),
                },
                "available_columns": list(all_columns),
                "calculated_columns": [column["name"] for column in session_data.get("calculated_columns", [])],
            },
            "bootstrap_analysis": bootstrap_results
        }
    except Exception as e:
        # logging.exception("An unexpected error occurred while processing dependency model")
        return JSONResponse(status_code=500, content={"error": str(e)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)