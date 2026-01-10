from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from typing import Dict
import numpy as np
import traceback
import logging
from utils import bootstrap_all_columns
from api.session import get_session_data, update_session
from models.schemas import DependencyModelRequest

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/save-dependency-model")
async def save_dependency_model(
    request: Request,
    data: DependencyModelRequest,
    session_data: Dict = Depends(get_session_data)
):
    try:
        df1 = session_data.get("df1")
        df2 = session_data.get("df2")
        
        if df1 is None or df2 is None:
            return JSONResponse(
                status_code=400,
                content={"error": "Session data is missing. Please upload files first."}
            )
        
        df1 = df1.copy()
        df2 = df2.copy()
        file1_name = session_data.get("file1_name", "file1")
        file2_name = session_data.get("file2_name", "file2")
        
        all_columns_df1 = set(df1.columns)
        all_columns_df2 = set(df2.columns)
        all_columns = all_columns_df1.union(all_columns_df2)
        
        calculated_columns = [col["name"] for col in session_data.get("calculated_columns", [])]
        
        requested_columns = set(data.dependent_variables + data.independent_variables)
        missing_in_df1 = requested_columns - all_columns_df1
        missing_in_df2 = requested_columns - all_columns_df2
        missing_in_both = missing_in_df1.intersection(missing_in_df2)
        missing_in_either = requested_columns - all_columns
        
        if missing_in_either:
            error_messages = []
            if missing_in_both:
                error_messages.append(f"The following columns were not found in either dataset: {', '.join(sorted(missing_in_both))}")
            if missing_in_df1 - missing_in_both:
                error_messages.append(f"The following columns were not found in the first dataset: {', '.join(sorted(missing_in_df1 - missing_in_both))}")
            if missing_in_df2 - missing_in_both:
                error_messages.append(f"The following columns were not found in the second dataset: {', '.join(sorted(missing_in_df2 - missing_in_both))}")
            
            return JSONResponse(
                status_code=400,
                content={
                    "error": " | ".join(error_messages),
                    "available_columns": sorted(list(all_columns)),
                    "calculated_columns": calculated_columns,
                    "df1_columns": sorted(list(all_columns_df1)),
                    "df2_columns": sorted(list(all_columns_df2)),
                    "requested_columns": sorted(list(requested_columns))
                }
            )
        session_data["dependency_model"] = {
            "dependent_variables": data.dependent_variables,
            "independent_variables": data.independent_variables
        }
        
        cookie_session_id = request.cookies.get("session_id")
        header_session_id = request.headers.get("X-Session-ID")
        effective_session_id = cookie_session_id or header_session_id or data.session_id
        
        if effective_session_id:
            update_session(effective_session_id, session_data)
        without_product_df = df1
        with_product_df = df2
        for col in with_product_df.columns:
            if with_product_df[col].dtype == 'object':
                try:
                    with_product_df[col] = with_product_df[col].astype(float)
                except Exception:
                    pass
        for col in without_product_df.columns:
            if without_product_df[col].dtype == 'object':
                try:
                    without_product_df[col] = without_product_df[col].astype(float)
                except Exception:
                    pass
        bootstrap_results = bootstrap_all_columns(without_product_df, with_product_df, 100)
        with_product_df = with_product_df.replace({np.nan: None, np.inf: None, -np.inf: None})
        without_product_df = without_product_df.replace({np.nan: None, np.inf: None, -np.inf: None})
        
        preview_with_product = with_product_df.head(100).to_dict(orient="records")
        preview_without_product = without_product_df.head(100).to_dict(orient="records")
        
        with_product_size = len(str(preview_with_product))
        without_product_size = len(str(preview_without_product))
        
        if with_product_size > 5 * 1024 * 1024:
            preview_with_product = with_product_df.head(50).to_dict(orient="records")
        if without_product_size > 5 * 1024 * 1024:
            preview_without_product = without_product_df.head(50).to_dict(orient="records")
        
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
                    "data": preview_with_product,
                },
                "without_product": {
                    "name": file2_name,
                    "shape": without_product_df.shape,
                    "data": preview_without_product,
                },
                "available_columns": list(all_columns),
                "calculated_columns": [column["name"] for column in session_data.get("calculated_columns", [])],
            },
            "bootstrap_analysis": bootstrap_results
        }
    except Exception as e:
        logger.error(f"Error saving dependency model: {str(e)}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500, 
            content={"error": str(e), "detail": traceback.format_exc()}
        ) 