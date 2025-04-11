from fastapi import UploadFile, HTTPException
import pandas as pd
import io
import shutil
from datetime import datetime, timedelta
import asyncio
from pathlib import Path
import numexpr as ne


TEMP_DIR = Path("./temp_files")

FILE_EXPIRATION = 3600


def read_file(file: UploadFile) -> pd.DataFrame:
    """
    Read a file into a pandas DataFrame based on its format.
    """
    content = file.file.read()
    file.file.seek(0)

    filename = file.filename.lower()

    if filename.endswith(".csv"):
        return pd.read_csv(io.BytesIO(content))
    elif filename.endswith((".xls", ".xlsx")):
        return pd.read_excel(io.BytesIO(content))
    elif filename.endswith(".parquet"):
        return pd.read_parquet(io.BytesIO(content))
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file format. Please upload CSV, Excel, or Parquet files.",
        )


def get_unnamed_columns(df: pd.DataFrame) -> list:
    """
    Get the indexes of unnamed columns in a DataFrame.
    """
    unnamed_cols = []
    for i, col in enumerate(df.columns):
        if isinstance(col, str) and col.startswith("Unnamed:"):
            unnamed_cols.append(i)
    return unnamed_cols


def get_mismatched_columns(df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
    """
    Get mismatched columns between two DataFrames.
    """
    cols_df1 = set(df1.columns)
    cols_df2 = set(df2.columns)

    return {
        "only_in_df1": list(cols_df1 - cols_df2),
        "only_in_df2": list(cols_df2 - cols_df1),
    }


async def cleanup_expired_files():
    """
    Clean up expired temporary files once.
    """
    current_time = datetime.now()

    for session_dir in TEMP_DIR.iterdir():
        if session_dir.is_dir():
            dir_modified_time = datetime.fromtimestamp(session_dir.stat().st_mtime)
            if current_time - dir_modified_time > timedelta(seconds=FILE_EXPIRATION):
                try:
                    shutil.rmtree(session_dir)
                except Exception as e:
                    print(f"Error cleaning up {session_dir}: {e}")


async def cleanup_expired_files_periodically():
    """
    Periodically clean up expired temporary files using asyncio.
    This avoids the GIL limitations of threading.
    """
    while True:
        await cleanup_expired_files()
        await asyncio.sleep(300)


def apply_formula(
    withProductdf: pd.DataFrame,
    withoutProductdf: pd.DataFrame,
    formula: str,
    newCol: str,
) -> list:
    """
    Apply a formula to DataFrames and return the column names used in the formula.

    Args:
        withProductdf: DataFrame with product data
        withoutProductdf: DataFrame without product data
        formula: Mathematical formula to apply (can use AVG, SUM, MIN, MAX or custom expressions)
        newCol: Name of the new column to create

    Returns:
        List of column names used in the formula
    """
    try:
        if any(func in formula.upper() for func in ["AVG(", "SUM(", "MIN(", "MAX("]):
            function_name = formula.split("(")[0].upper()
            column_names = formula.split("(")[1].split(")")[0].split(",")
            column_names = [col.strip() for col in column_names]
            column_names = [col.strip("[").strip("]") for col in column_names]

            if not all(col in withProductdf.columns for col in column_names):
                raise HTTPException(
                    status_code=400,
                    detail="Column names in the formula are not present in the withProductdf dataframe.",
                )

            if not all(col in withoutProductdf.columns for col in column_names):
                raise HTTPException(
                    status_code=400,
                    detail="Column names in the formula are not present in the withoutProductdf dataframe.",
                )

            if function_name == "AVG":
                withProductdf[newCol] = withProductdf[column_names].mean(axis=1)
                withoutProductdf[newCol] = withoutProductdf[column_names].mean(axis=1)

            elif function_name == "SUM":
                withProductdf[newCol] = withProductdf[column_names].sum(axis=1)
                withoutProductdf[newCol] = withoutProductdf[column_names].sum(axis=1)

            elif function_name == "MIN":
                withProductdf[newCol] = withProductdf[column_names].min(axis=1)
                withoutProductdf[newCol] = withoutProductdf[column_names].min(axis=1)

            elif function_name == "MAX":
                withProductdf[newCol] = withProductdf[column_names].max(axis=1)
                withoutProductdf[newCol] = withoutProductdf[column_names].max(axis=1)

            return column_names

        else:
            import re

            column_names = re.findall(r"\[(.*?)\]", formula)

            col_mapping = {}
            for col in column_names:
                safe_name = f"col_{len(col_mapping)}"
                col_mapping[col] = safe_name

            for original_col in col_mapping.keys():
                if original_col not in withProductdf.columns:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Column '{original_col}' from the formula is not present in the withProductdf dataframe.",
                    )
                if original_col not in withoutProductdf.columns:
                    raise HTTPException(
                        status_code=400,
                        detail=f"Column '{original_col}' from the formula is not present in the withoutProductdf dataframe.",
                    )

            working_formula = formula

            try:
                wp_col_values = {}
                for original_col, safe_name in col_mapping.items():
                    wp_col_values[safe_name] = withProductdf[original_col].values

                for original_col, safe_name in col_mapping.items():
                    working_formula = working_formula.replace(
                        f"[{original_col}]", safe_name
                    )

                wp_result = ne.evaluate(working_formula, local_dict=wp_col_values)
                withProductdf[newCol] = wp_result
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error evaluating expression for withProductdf: {e}. Please check your formula syntax.",
                )

            try:
                wo_col_values = {}
                for original_col, safe_name in col_mapping.items():
                    wo_col_values[safe_name] = withoutProductdf[original_col].values

                wo_result = ne.evaluate(working_formula, local_dict=wo_col_values)
                withoutProductdf[newCol] = wo_result
            except Exception as e:
                raise HTTPException(
                    status_code=400,
                    detail=f"Error evaluating expression for withoutProductdf: {e}. Please check your formula syntax.",
                )

            return list(col_mapping.keys())

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Error applying formula: {e}")
