from fastapi import UploadFile, HTTPException
import pandas as pd
import io
import shutil
from datetime import datetime, timedelta
import asyncio
from pathlib import Path
import re
import logging
import numpy as np
from sklearn.utils import resample
from collections import defaultdict

TEMP_DIR = Path("./temp_files")
FILE_EXPIRATION = 86400

# logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

_compiled_patterns = {
    'functions_empty': re.compile(r"AVERAGE\(\s*\)|SUM\(\s*\)|MIN\(\s*\)|MAX\(\s*\)"),
    'division_by_zero': re.compile(r"/\s*0+(?!\d)"),
    'column_refs': re.compile(r"\[([^\]]+)\]"),
    'starts_with_operator': re.compile(r'^\s*[+\-*/]\s*'),
    'ends_with_operator': re.compile(r'[+\-*/]\s*$'),
    'consecutive_operators': re.compile(r'[+\-*/]\s*[+\-*/]'),
    'bracket_operator_after': re.compile(r'\(\s*[+\-*/]'),
    'bracket_operator_before': re.compile(r'[+\-*/]\s*\)'),
    'unnamed_columns': re.compile(r'^Unnamed:'),
    'column_bracket_replace': re.compile(r'\[([^\[\]]+)\]'),
    'nested_bracket_fix': re.compile(r'\[df\["([^\[\]]+)"\]\]')
}

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
    pattern = _compiled_patterns['unnamed_columns']
    
    for i, col in enumerate(df.columns):
        if isinstance(col, str) and pattern.match(col):
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
    expiration_threshold = timedelta(seconds=FILE_EXPIRATION)

    cleanup_tasks = []
    for session_dir in TEMP_DIR.iterdir():
        if session_dir.is_dir():
            try:
                dir_modified_time = datetime.fromtimestamp(session_dir.stat().st_mtime)
                if current_time - dir_modified_time > expiration_threshold:
                    cleanup_tasks.append(asyncio.to_thread(shutil.rmtree, session_dir))
            except Exception as e:
                # logging.error(f"Error checking modified time for {session_dir}: {e}")
                pass
    
    if cleanup_tasks:
        await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        # logging.info(f"Cleaned up {len(cleanup_tasks)} expired session directories")


async def cleanup_expired_files_periodically():
    while True:
        try:
            await cleanup_expired_files()
        except Exception as e:
            # logging.error(f"Error in cleanup routine: {e}")
            pass
        
        await asyncio.sleep(3600)


def validate_formula(formula, available_columns):
    errors = []

    if not formula:
        errors.append("Formula cannot be empty")
        return errors

    parentheses_count = 0
    for char in formula:
        if char == "(":
            parentheses_count += 1
        elif char == ")":
            parentheses_count -= 1
            if parentheses_count < 0:
                errors.append("Unbalanced parentheses - too many closing parentheses")
                break

    if parentheses_count > 0:
        errors.append("Unbalanced parentheses - missing closing parentheses")

    if _compiled_patterns['functions_empty'].search(formula):
        errors.append("Functions cannot be empty")

    if _compiled_patterns['division_by_zero'].search(formula):
        errors.append("Division by zero is not allowed")

    column_refs = _compiled_patterns['column_refs'].findall(formula)
    available_columns_set = set(available_columns)
    for col in column_refs:
        if col not in available_columns_set:
            errors.append(f"Column '{col}' not found in dataset")

    if _compiled_patterns['starts_with_operator'].match(formula):
        errors.append("Formula should not start with an operator")

    if _compiled_patterns['ends_with_operator'].search(formula):
        errors.append("Formula should not end with an operator")

    if _compiled_patterns['consecutive_operators'].search(formula):
        errors.append("Cannot have two consecutive operators")

    if _compiled_patterns['bracket_operator_after'].search(formula):
        errors.append("An opening bracket cannot be followed directly by an operator")

    if _compiled_patterns['bracket_operator_before'].search(formula):
        errors.append("A closing bracket cannot be preceded directly by an operator")

    return errors


def process_formula(formula):
    processed = _compiled_patterns['column_bracket_replace'].sub(
        lambda match: f'df["{match.group(1)}"]', 
        formula
    )
    processed = _compiled_patterns['nested_bracket_fix'].sub(r'df["\1"]', processed)
    return processed

def bootstrap_all_columns(df_before, df_after, n_bootstraps=10000):
    common_columns = df_before.columns.intersection(df_after.columns)
    significant_impact = []
    no_significant_impact = []

    numeric_columns = []
    for column in common_columns:
        try:
            data_before = df_before[column].dropna()
            data_after = df_after[column].dropna()

            if np.issubdtype(data_before.dtype, np.number):
                numeric_columns.append((column, data_before.values, data_after.values))
        except Exception as e:
            # logging.error(f"Error processing column '{column}': {e}")
            continue

    for column, data_before, data_after in numeric_columns:
        try:
            bootstrapped_differences = np.zeros(n_bootstraps)
            
            for i in range(n_bootstraps):
                sample_before = resample(data_before)
                sample_after = resample(data_after)
                bootstrapped_differences[i] = np.mean(sample_after) - np.mean(sample_before)

            lower_bound = np.percentile(bootstrapped_differences, 2.5)
            upper_bound = np.percentile(bootstrapped_differences, 97.5)
            mean_difference = np.mean(bootstrapped_differences)
            std_difference = np.std(bootstrapped_differences, ddof=1)
            
            column_result = {
                "column": column,
                "mean_difference": round(float(mean_difference), 3),
                "standard_deviation": round(float(std_difference), 3),
                "confidence_interval": {
                    "lower_bound": round(float(lower_bound), 3),
                    "upper_bound": round(float(upper_bound), 3)
                },
                "is_significant": bool(lower_bound > 0 or upper_bound < 0)
            }

            if lower_bound > 0 or upper_bound < 0:
                significant_impact.append(column_result)
            else:
                no_significant_impact.append(column_result)
                        
        except Exception as e:
            # logging.error(f"Error processing column '{column}': {e}")
            continue
            
    return {
        "significant_impact": significant_impact,
        "no_significant_impact": no_significant_impact,
        "total_columns_analyzed": len(significant_impact) + len(no_significant_impact)
    }