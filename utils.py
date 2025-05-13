from fastapi import UploadFile, HTTPException
import pandas as pd
import io
import shutil
from datetime import datetime, timedelta
import asyncio
from pathlib import Path
import re
import logging


TEMP_DIR = Path("./temp_files")
FILE_EXPIRATION = 86400

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


def read_file(file: UploadFile) -> pd.DataFrame:
    content = file.file.read()
    file.file.seek(0)

    filename = file.filename.lower()

    try:
        if filename.endswith(".csv"):
            return pd.read_csv(io.BytesIO(content), low_memory=False)
        elif filename.endswith((".xls", ".xlsx")):
            return pd.read_excel(io.BytesIO(content))
        elif filename.endswith(".parquet"):
            return pd.read_parquet(io.BytesIO(content))
        else:
            raise HTTPException(
                status_code=400,
                detail="Unsupported file format. Please upload CSV, Excel, or Parquet files.",
            )
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Error reading file: {str(e)}",
        )


def get_unnamed_columns(df: pd.DataFrame) -> list:
    unnamed_cols = []
    for i, col in enumerate(df.columns):
        if isinstance(col, str) and col.startswith("Unnamed:"):
            unnamed_cols.append(i)
    return unnamed_cols


def get_mismatched_columns(df1: pd.DataFrame, df2: pd.DataFrame) -> dict:
    cols_df1 = set(df1.columns)
    cols_df2 = set(df2.columns)

    return {
        "only_in_df1": list(cols_df1 - cols_df2),
        "only_in_df2": list(cols_df2 - cols_df1),
    }


async def cleanup_expired_files():
    current_time = datetime.now()

    for session_dir in TEMP_DIR.iterdir():
        if session_dir.is_dir():
            try:
                dir_modified_time = datetime.fromtimestamp(session_dir.stat().st_mtime)
                if current_time - dir_modified_time > timedelta(seconds=FILE_EXPIRATION):
                    try:
                        shutil.rmtree(session_dir)
                        logging.info(f"Cleaned up expired session directory: {session_dir}")
                    except Exception as e:
                        logging.error(f"Error cleaning up {session_dir}: {e}")
            except Exception as e:
                logging.error(f"Error checking modified time for {session_dir}: {e}")


async def cleanup_expired_files_periodically():
    while True:
        try:
            await cleanup_expired_files()
        except Exception as e:
            logging.error(f"Error in cleanup routine: {e}")
        
        await asyncio.sleep(3600)


def validate_formula(formula, available_columns):
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

    if re.match(r'^\s*[+\-*/]\s*', formula):
        errors.append("Formula should not start with an operator")

    if re.search(r'[+\-*/]\s*$', formula):
        errors.append("Formula should not end with an operator")

    consecutive_operators_regex = r'[+\-*/]\s*[+\-*/]'
    if re.search(consecutive_operators_regex, formula):
        errors.append("Cannot have two consecutive operators")

    if re.search(r'\(\s*[+\-*/]', formula):
        errors.append("An opening bracket cannot be followed directly by an operator")

    if re.search(r'[+\-*/]\s*\)', formula):
        errors.append("A closing bracket cannot be preceded directly by an operator")

    return errors


def process_formula(formula):
    processed = formula
    
    processed = re.sub(r'\[([^\[\]]+)\]', lambda match: f'df["{match.group(1)}"]', processed)
    
    processed = re.sub(r'(\])(\s*,\s*\[)', r'\1,\2', processed)
    
    return processed