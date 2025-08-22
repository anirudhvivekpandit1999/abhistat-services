from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse
from typing import Dict
import numpy as np
import pandas as pd
import re
from utils import validate_formula, process_formula
from api.session import get_session_data
from models.schemas import BatchCalculatedColumnsRequest

router = APIRouter()

@router.post("/save-calculated-columns")
async def save_calculated_columns(
    request: Request,
    data: BatchCalculatedColumnsRequest,
    session_data: Dict = Depends(get_session_data)
):
    try:
        df1 = session_data["df1"]
        df2 = session_data["df2"]
        new_columns = []
        errors = []
        for column_request in data.columns:
            column_name = column_request.column_name
            formula = column_request.formula
            formula_elements = column_request.formula_elements
            unique_columns = set(
                element["value"] for element in formula_elements if element["type"] == "column"
            )
            for column_value in unique_columns:
                if f'[{column_value}]' not in formula:
                    formula = formula.replace(column_value, f'[{column_value}]')
            if not column_name or not re.match(r"^[a-zA-Z0-9_]+$", column_name):
                errors.append(f"Column name '{column_name}' can only contain letters, numbers and underscores")
                continue
            if len(column_name) > 30:
                errors.append(f"Column name '{column_name}' is too long (max 30 characters)")
                continue
            original_columns = set(df1.columns).intersection(set(df2.columns))
            if column_name in original_columns:
                errors.append(f"Column name '{column_name}' already exists in the original dataset")
                continue
            validation_errors_df1 = validate_formula(formula, list(df1.columns))
            validation_errors_df2 = validate_formula(formula, list(df2.columns))
            if validation_errors_df1:
                for error in validation_errors_df1:
                    errors.append(f"Formula for '{column_name}' in first dataset: {error}")
                continue
            if validation_errors_df2:
                for error in validation_errors_df2:
                    errors.append(f"Formula for '{column_name}' in second dataset: {error}")
                continue  
        if errors:
            return JSONResponse(
                status_code=400,
                content={"errors": errors}
            )
        processed_columns = []
        for column_request in data.columns:
            column_name = column_request.column_name
            formula = column_request.formula
            formula_elements = column_request.formula_elements
            
            unique_columns = set(
                element["value"] for element in formula_elements if element["type"] == "column"
            )   
            for column_value in unique_columns:
                if f'[{column_value}]' not in formula:
                    formula = formula.replace(column_value, f'[{column_value}]')
            processed_formula = process_formula(formula)
            try:
                df1[column_name] = np.nan
                df2[column_name] = np.nan
                df1[column_name] = eval(processed_formula, {"__builtins__": None}, {"df": df1, "np": np})
                df2[column_name] = eval(processed_formula, {"__builtins__": None}, {"df": df2, "np": np})
                new_columns.append(column_name)
                processed_columns.append({"name": column_name, "formula": formula})
            except Exception as e:
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
        df1_preview = df1[new_columns].head(5).replace({np.nan: None, np.inf: None, -np.inf: None}) if new_columns else pd.DataFrame()
        df2_preview = df2[new_columns].head(5).replace({np.nan: None, np.inf: None, -np.inf: None}) if new_columns else pd.DataFrame()
        return {
            "message": f"Successfully added {len(new_columns)} calculated columns",
            "new_columns": new_columns,
            "preview": {
                "file1": df1_preview.to_dict(orient="records") if not df1_preview.empty else {},
                "file2": df2_preview.to_dict(orient="records") if not df2_preview.empty else {},
            }
        }
    except Exception as e:
        return JSONResponse(status_code=500, content={"errors": [str(e)]})