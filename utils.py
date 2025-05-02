from fastapi import UploadFile, HTTPException
import pandas as pd
import io
import shutil
from datetime import datetime, timedelta
import asyncio
from pathlib import Path
import numexpr as ne
import re
import logging


TEMP_DIR = Path("./temp_files")
FILE_EXPIRATION = 7200

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")


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


def validate_formula(formula, available_columns):
    """
    Enhanced validation function for formula string.
    Synchronized with frontend validation rules.
    """
    errors = []

    if not formula:
        errors.append("Formula cannot be empty")
        logging.warning("Formula validation failed: Formula cannot be empty")
        return errors

    # Check for balanced parentheses
    stack = []
    for char in formula:
        if char == "(":
            stack.append("(")
        elif char == ")":
            if len(stack) == 0:
                error_message = "Unbalanced parentheses - too many closing parentheses"
                logging.warning(error_message)
                errors.append(error_message)
                break
            stack.pop()

    if stack:
        error_message = "Unbalanced parentheses - missing closing parentheses"
        logging.warning(error_message)
        errors.append(error_message)

    # Check for empty functions
    functions_regex = r"AVERAGE\(\s*\)|SUM\(\s*\)|MIN\(\s*\)|MAX\(\s*\)"
    if re.search(functions_regex, formula):
        error_message = "Functions cannot be empty"
        logging.warning(error_message)
        errors.append(error_message)

    # Check for division by zero
    division_by_zero_regex = r"/\s*0+(?!\d)"
    if re.search(division_by_zero_regex, formula):
        error_message = "Division by zero is not allowed"
        logging.warning(error_message)
        errors.append(error_message)

    # Check for invalid column references
    column_refs = re.findall(r"\[([^\]]+)\]", formula)
    for col in column_refs:
        if col not in available_columns:
            error_message = f"Column '{col}' not found in dataset"
            logging.warning(error_message)
            errors.append(error_message)

    # Additional validations synchronized with frontend
    if re.match(r'^\s*[+\-*/]\s*', formula):
        error_message = "Formula should not start with an operator"
        logging.warning(error_message)
        errors.append(error_message)

    if re.search(r'[+\-*/]\s*$', formula):
        error_message = "Formula should not end with an operator"
        logging.warning(error_message)
        errors.append(error_message)

    consecutive_operators_regex = r'[+\-*/]\s*[+\-*/]'
    if re.search(consecutive_operators_regex, formula):
        error_message = "Cannot have two consecutive operators"
        logging.warning(error_message)
        errors.append(error_message)

    if re.search(r'\(\s*[+\-*/]', formula):
        error_message = "An opening bracket cannot be followed directly by an operator"
        logging.warning(error_message)
        errors.append(error_message)

    if re.search(r'[+\-*/]\s*\)', formula):
        error_message = "A closing bracket cannot be preceded directly by an operator"
        logging.warning(error_message)
        errors.append(error_message)

    return errors


def process_formula(formula, available_columns):
    """
    Process the formula to make it executable in Python/Pandas context.
    Converts UI formula syntax to executable Python code.
    """
    processed = formula
    logging.info(f"Original formula: {formula}")
    
    # First replace column references that are already in the dataframe
    processed = re.sub(r'\[([^\[\]]+)\]', lambda match: f'df["{match.group(1)}"]', formula)

    # Replace functions
    # processed = processed.replace("AVERAGE(", "np.mean([")
    # processed = processed.replace("SUM(", "np.sum([")
    # processed = processed.replace("MIN(", "np.min([")
    # processed = processed.replace("MAX(", "np.max([")

    # Close function parentheses
    processed = re.sub(r'(\])(\s*,\s*\[)', r'\1,\2', processed)
    processed = re.sub(r'\)(?!\]|\,|\))', "])", processed)
    
    logging.info(f"Processed formula: {processed}")
    return processed  # Return the processed formula
