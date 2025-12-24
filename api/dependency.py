from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from typing import Dict
import numpy as np
import traceback
import logging
from utils import bootstrap_all_columns
from api.session import get_session_data
from models.schemas import DependencyModelRequest

router = APIRouter()
logger = logging.getLogger(__name__)

@router.post("/save-dependency-model")
async def save_dependency_model(
    data: DependencyModelRequest,
    session_data: Dict = Depends(get_session_data)
):
    try:
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
        logger.error(f"Error saving dependency model: {str(e)}\n{traceback.format_exc()}")
        return JSONResponse(
            status_code=500, 
            content={"error": str(e), "detail": traceback.format_exc()}
        ) 