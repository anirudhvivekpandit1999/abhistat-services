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
import logging
import sys
from .utils import (
    read_file,
    get_unnamed_columns,
    get_mismatched_columns,
    cleanup_expired_files_periodically,
    process_formula,
    validate_formula
)

TEMP_DIR = Path("./temp_files")
TEMP_DIR.mkdir(exist_ok=True)

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

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


app = FastAPI(title="File Processor API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:3000", "http://127.0.0.1:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def get_session_data(
    request: Request,
    session_id: Optional[str] = Cookie(None),
    x_session_id: Optional[str] = Header(None, alias="X-Session-ID"),
):
    """
    Enhanced session data retrieval that accepts session ID from multiple sources:
    1. Cookie
    2. Custom header
    3. Query parameter
    4. Request body (for POST/PUT requests)
    
    Returns session data or raises HTTPException if no valid session found.
    """
    logging.info("Starting session data retrieval process")
    effective_session_id = None
    
    if session_id:
        logging.info(f"Checking session ID from cookie: {session_id}")
        if session_id in session_data_store:
            effective_session_id = session_id
            logging.info(f"Session ID from cookie is valid: {effective_session_id}")
    
    if not effective_session_id and x_session_id:
        logging.info(f"Checking session ID from header: {x_session_id}")
        if x_session_id in session_data_store:
            effective_session_id = x_session_id
            logging.info(f"Session ID from header is valid: {effective_session_id}")
    
    if not effective_session_id:
        query_session = request.query_params.get("session_id")
        logging.info(f"Checking session ID from query parameters: {query_session}")
        if query_session and query_session in session_data_store:
            effective_session_id = query_session
            logging.info(f"Session ID from query parameters is valid: {effective_session_id}")
    
    if not effective_session_id and request.method in ["POST", "PUT"]:
        logging.info("Checking session ID from request body")
        try:
            body = await request.json()
            body_session = body.get("session_id")
            if body_session and body_session in session_data_store:
                effective_session_id = body_session
                logging.info(f"Session ID from request body is valid: {effective_session_id}")
        except Exception as e:
            logging.warning(f"Failed to parse session ID from request body: {str(e)}")
    
    if not effective_session_id:
        logging.warning("No valid session ID found in any source")
        available_sessions = list(session_data_store.keys())
        logging.info(f"Available sessions: {available_sessions[:5] if available_sessions else []}")
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
    
    logging.info(f"Successfully retrieved session data for session ID: {effective_session_id}")
    return session_data_store[effective_session_id]


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
    logging.info("Starting file processing")
    
    if not session_id:
        session_id = str(uuid.uuid4())
        logging.info(f"Generated new session ID: {session_id}")
        
        # Set cookie with more permissive settings for development
        response.set_cookie(
            key="session_id", 
            value=session_id,
            httponly=False,  # Allow JavaScript access
            samesite="lax",  # Less restrictive SameSite policy
            max_age=3600     # 1 hour expiration
        )
        logging.info("Session ID cookie set successfully")

    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    logging.info(f"Session directory created: {session_dir}")

    temp_file1_path = session_dir / f"file1_{uuid.uuid4()}_{file1.filename}"
    temp_file2_path = session_dir / f"file2_{uuid.uuid4()}_{file2.filename}"
    logging.info(f"Temporary file paths created: {temp_file1_path}, {temp_file2_path}")

    try:
        logging.info(f"Saving uploaded files to temporary paths")
        with open(temp_file1_path, "wb") as buffer:
            shutil.copyfileobj(file1.file, buffer)
        logging.info(f"File 1 saved: {temp_file1_path}")

        with open(temp_file2_path, "wb") as buffer:
            shutil.copyfileobj(file2.file, buffer)
        logging.info(f"File 2 saved: {temp_file2_path}")

        file1.file.seek(0)
        file2.file.seek(0)

        logging.info("Reading files into DataFrames")
        df1 = read_file(file1)
        df2 = read_file(file2)
        logging.info(f"Files read successfully: {file1.filename}, {file2.filename}")

        logging.info("Checking for unnamed columns")
        unnamed_cols_df1 = get_unnamed_columns(df1)
        unnamed_cols_df2 = get_unnamed_columns(df2)

        if (unnamed_cols_df1 or unnamed_cols_df2) and not remove_unnamed:
            error_message = "Unnamed columns detected: "
            if unnamed_cols_df1:
                error_message += f"File '{file1.filename}' has unnamed columns at indexes {unnamed_cols_df1}. "
            if unnamed_cols_df2:
                error_message += f"File '{file2.filename}' has unnamed columns at indexes {unnamed_cols_df2}."
            logging.warning(error_message)
            return JSONResponse(status_code=400, content={"error": error_message})

        if remove_unnamed:
            logging.info("Removing unnamed columns from DataFrames")
            df1 = df1.loc[:, ~df1.columns.str.contains("^Unnamed")]
            df2 = df2.loc[:, ~df2.columns.str.contains("^Unnamed")]
            logging.info("Unnamed columns removed successfully")

        logging.info("Checking for mismatched columns")
        mismatched_cols = get_mismatched_columns(df1, df2)

        if (
            mismatched_cols["only_in_df1"] or mismatched_cols["only_in_df2"]
        ) and not remove_mismatched:
            error_message = "Mismatched columns detected: "
            if mismatched_cols["only_in_df1"]:
                error_message += f"Columns only in '{file1.filename}': {mismatched_cols['only_in_df1']}. "
            if mismatched_cols["only_in_df2"]:
                error_message += f"Columns only in '{file2.filename}': {mismatched_cols['only_in_df2']}."
            logging.warning(error_message)
            return JSONResponse(status_code=400, content={"error": error_message})

        if remove_mismatched:
            logging.info("Removing mismatched columns from DataFrames")
            common_columns = list(set(df1.columns) & set(df2.columns))
            df1 = df1[common_columns]
            df2 = df2[common_columns]
            logging.info("Mismatched columns removed successfully")

        logging.info("Rounding numeric columns to 3 decimal places")
        for col in df1.select_dtypes(include=[np.number]).columns:
            df1[col] = df1[col].round(3)
        for col in df2.select_dtypes(include=[np.number]).columns:
            df2[col] = df2[col].round(3)
        logging.info("Numeric columns rounded successfully")

        logging.info("Storing session data")
        session_data_store[session_id] = {
            "df1": df1,
            "df2": df2,
            "file1_name": file1.filename,
            "file2_name": file2.filename,
            "calculated_columns": [],
        }
        logging.info(f"Session data stored successfully for session ID: {session_id}")

        logging.info("Returning processed file information")
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
        logging.error(f"An error occurred during file processing: {str(e)}")
        if os.path.exists(temp_file1_path):
            os.remove(temp_file1_path)
            logging.info(f"Temporary file 1 deleted: {temp_file1_path}")
        if os.path.exists(temp_file2_path):
            os.remove(temp_file2_path)
            logging.info(f"Temporary file 2 deleted: {temp_file2_path}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.post("/save-calculated-columns/")
async def save_calculated_columns(
    request: Request,
    data: BatchCalculatedColumnsRequest,
    session_data: Dict = Depends(get_session_data)
):
    """
    Process multiple calculated columns at once and add them to both DataFrames.
    Returns the newly added column names or validation errors.
    """
    try:
        logging.info("Processing calculated columns request")
        df1 = session_data["df1"]
        df2 = session_data["df2"]
        new_columns = []
        errors = []

        for column_request in data.columns:
            column_name = column_request.column_name
            formula = column_request.formula
            formula_elements = column_request.formula_elements
            
            # Replace column names in the formula using formula elements
            for element in formula_elements:
                if element["type"] == "column":
                    column_value = element["value"]
                    formula = formula.replace(column_value, f'[{column_value}]')
                    
            logging.info(f"Processing column: {column_name} with formula: {formula} and elements: {formula_elements}")
                  
            if not column_name or not re.match(r"^[a-zA-Z0-9_]+$", column_name):
                error_message = f"Column name '{column_name}' can only contain letters, numbers and underscores"
                logging.warning(error_message)
                errors.append(error_message)
                continue

            if len(column_name) > 30:
                error_message = f"Column name '{column_name}' is too long (max 30 characters)"
                logging.warning(error_message)
                errors.append(error_message)
                continue

            original_columns = set(df1.columns).intersection(set(df2.columns))
            if column_name in original_columns:
                error_message = f"Column name '{column_name}' already exists in the original dataset"
                logging.warning(error_message)
                errors.append(error_message)
                continue
            
            validation_errors = validate_formula(formula, list(df1.columns))
            if validation_errors:
                for error in validation_errors:
                    logging.warning(f"Validation error for '{column_name}': {error}")
                    errors.append(f"Formula for '{column_name}': {error}")
                continue

        if errors:
            logging.error(f"Validation errors encountered: {errors}")
            return JSONResponse(
                status_code=400,
                content={"errors": errors}
            )
            
        current_columns = list(df1.columns)
        processed_columns = []

        for column_request in data.columns:
            processed_formula = process_formula(formula, current_columns)
            logging.info(f"Processed formula for '{column_name}': {processed_formula}")

            try:    
                # Initialize the column with NaN values before assigning the formula result
                df1[column_name] = np.nan
                df2[column_name] = np.nan

                # Assign the evaluated formula result to the new column
                df1[column_name] = eval(processed_formula, {"__builtins__": None}, {"df": df1, "np": np})

                df2[column_name] = eval(processed_formula, {"__builtins__": None}, {"df": df2, "np": np})

                print("HERE")
                
                new_columns.append(column_name)
                processed_columns.append({"name": column_name, "formula": formula})
                
                print(new_columns)
                
                current_columns = list(df1.columns)
                logging.info(f"Successfully added column: {column_name}")
                
            except Exception as e:
                logging.error(f"Error executing formula for '{column_name}': {str(e)}")
                logging.exception("Traceback for the error:")
                exc_type, exc_value, exc_traceback = sys.exc_info()
                logging.error("Exception type: %s", exc_type)
                logging.error("Exception value: %s", exc_value)
                logging.error("Traceback details:", exc_info=(exc_type, exc_value, exc_traceback))
                for col in new_columns:
                    if col in df1.columns:
                        df1.drop(columns=[col], inplace=True)
                    if col in df2.columns:
                        df2.drop(columns=[col], inplace=True)
                
                return JSONResponse(
                    status_code=400, 
                    content={"errors": [f"Error executing formula for '{column_name}': {str(e)}"]}
                )

        session_data["df1"] = df1
        session_data["df2"] = df2
        session_data["calculated_columns"] = processed_columns

        logging.info(f"Successfully added {len(new_columns)} calculated columns")
        return {
            "message": f"Successfully added {len(new_columns)} calculated columns",
            "new_columns": new_columns,
            "preview": {
                "file1": df1[new_columns].head(5).to_dict(orient="records") if new_columns else {},
                "file2": df2[new_columns].head(5).to_dict(orient="records") if new_columns else {},
            }
        }

    except Exception as e:
        logging.exception("An unexpected error occurred while processing calculated columns")
        return JSONResponse(status_code=500, content={"errors": [str(e)]})

@app.get("/session-status/")
async def session_status(
    session_data: Optional[Dict] = Depends(get_session_data),
    response: Response = None,
):
    """Diagnostic endpoint to check session status and data."""
    if session_data:
        return {
            "status": "active",
            "session_info": {
                "file1_name": session_data.get("file1_name"),
                "file2_name": session_data.get("file2_name"),
                "columns_count": len(session_data.get("df1", {}).columns) if "df1" in session_data else 0,
                "calculated_columns": len(session_data.get("calculated_columns", [])),
            }
        }
    return {"status": "no_active_session"}


@app.post("/save-dependency-model/")
async def save_dependency_model(
    request: Request,
    data: DependencyModelRequest,
    session_data: Dict = Depends(get_session_data)
):
    """
    Save the dependency model (dependent and independent variables) to the session data
    and return complete DataFrames for the next step of analysis.
    """
    try:
        logging.info(f"Processing dependency model with dependent vars: {data.dependent_variables} and independent vars: {data.independent_variables}")
        
        df1 = session_data["df1"].copy()
        df2 = session_data["df2"].copy()
        file1_name = session_data["file1_name"]
        file2_name = session_data["file2_name"]
        
        # Validate that all requested columns exist in the DataFrames
        all_columns = set(df1.columns)
        requested_columns = set(data.dependent_variables + data.independent_variables)
        
        missing_columns = requested_columns - all_columns
        if missing_columns:
            return JSONResponse(
                status_code=400,
                content={"error": f"The following columns were not found in the dataset: {', '.join(missing_columns)}"}
            )
        
        # Store the dependency model in the session data
        session_data["dependency_model"] = {
            "dependent_variables": data.dependent_variables,
            "independent_variables": data.independent_variables
        }
        
        # Create aliases for better readability in the frontend
        with_product_df = df1
        without_product_df = df2
        
        # Handle NaN values in both DataFrames by replacing them with None (which becomes null in JSON)
        # This prevents "Out of range float values are not JSON compliant" errors
        with_product_df = with_product_df.replace({np.nan: None})
        without_product_df = without_product_df.replace({np.nan: None})
        
        # Return full DataFrames info and model configuration
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
            }
        }
    except Exception as e:
        logging.exception("An unexpected error occurred while processing dependency model")
        return JSONResponse(status_code=500, content={"error": str(e)})


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
