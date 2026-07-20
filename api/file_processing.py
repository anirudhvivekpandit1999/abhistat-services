from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
import uuid
import shutil
import numpy as np
import os
import traceback
import pandas as pd
import datetime

from core.config import TEMP_DIR
from utils import get_unnamed_columns

from core.db import db


router = APIRouter()

MAX_ROWS = 100000  # 🔥 safety limit

storage = {}

@router.post("/process-file")
async def process_file(file: UploadFile = File(...)):
    temp_file_path = None

    try:
        print("📂 File received:", file.filename)

        # ✅ Create temp directory
        session_dir = TEMP_DIR / str(uuid.uuid4())
        session_dir.mkdir(parents=True, exist_ok=True)

        temp_file_path = session_dir / f"{uuid.uuid4()}_{file.filename}"

        # ✅ Save file
        with open(temp_file_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        ext = file.filename.split(".")[-1].lower()
        all_sheets_data = {}

        # =========================
        # ✅ HANDLE EXCEL FILES
        # =========================
        if ext in ["xlsx", "xls", "xlsm"]:
            excel_file = pd.ExcelFile(temp_file_path)
            sheets = excel_file.sheet_names

            if not sheets:
                return JSONResponse(
                    status_code=400,
                    content={"error": "No sheets found"}
                )
            # read ALL sheets into memory first, then close
            sheet_dataframes = {}
            for sheet_name in sheets:
                sheet_dataframes[sheet_name] = pd.read_excel(
                    excel_file,
                    sheet_name=sheet_name,
                    header=None,
                    dtype=object
                )
            # NOW close the file so finally block can delete it
            excel_file.close()
            for sheet_name, df in sheet_dataframes.items():
                try:
                    if df is None or df.empty:
                        continue

                    # 🔥 SAFETY LIMIT
                    if len(df) > MAX_ROWS:
                        return JSONResponse(
                            status_code=400,
                            content={
                                "error": f"Sheet '{sheet_name}' too large ({len(df)} rows). Limit is {MAX_ROWS}."
                            }
                        )

                    # =========================
                    # 🔥 SAFE HEADER CREATION
                    # =========================
                    raw_header = df.iloc[0].tolist()

                    clean_header = []
                    for i, col in enumerate(raw_header):
                        if pd.isna(col) or str(col).strip() == "":
                            clean_header.append(f"Column_{i}")  # ✅ NEVER DROP
                        else:
                            clean_header.append(str(col).strip())

                    df.columns = clean_header
                    df = df[1:]
                    df.reset_index(drop=True, inplace=True)

                    # =========================
                    # 🔥 REMOVE ONLY TRUE "Unnamed"
                    # =========================
                    unnamed_cols = get_unnamed_columns(df)
                    if unnamed_cols:
                        df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed")]

                    # =========================
                    # 🔥 HANDLE DATES PROPERLY
                    # =========================
                    for col in df.columns:
                        try:
                            # only convert if the column name suggests it's a date
                            if any(word in col.lower() for word in ["date", "time", "dt"]):
                                df[col] = pd.to_datetime(df[col], errors="ignore")
                                if pd.api.types.is_datetime64_any_dtype(df[col]):
                                    df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
                        except:
                            pass

                    # =========================
                    # 🔥 ROUND NUMERIC VALUES
                    # =========================
                    numeric_cols = df.select_dtypes(include=[np.number]).columns
                    if len(numeric_cols) > 0:
                        df[numeric_cols] = df[numeric_cols].round(3)

                    # =========================
                    # 🔥 CLEAN NaN / INF
                    # =========================
                    df_clean = df.replace({
                        np.nan: None,
                        np.inf: None,
                        -np.inf: None
                    })

                    # 🔍 DEBUG (keep for now)
                    print(f"✅ {sheet_name} columns:", df.columns.tolist())

                    # =========================
                    # ✅ STORE DATA
                    # =========================
                    def make_serializable(val):
                        if isinstance(val, datetime.time):
                            return val.strftime("%H:%M:%S")
                        if isinstance(val, datetime.datetime):
                            return val.isoformat()
                        if isinstance(val, datetime.date):
                            return val.isoformat()
                        return val
                    # only convert columns that actually contain datetime values
                    # instead of checking every single cell (much faster)
                    for col in df_clean.columns:
                        sample = df_clean[col].dropna()
                        if not sample.empty and isinstance(sample.iloc[0], (datetime.time, datetime.datetime, datetime.date)):
                            df_clean[col] = df_clean[col].apply(
                                lambda v: make_serializable(v) if v is not None else v
                            )
                    records = df_clean.to_dict(orient="records")
                    # records = [
                    #     {k: make_serializable(v) for k, v in row.items()}
                    #     for row in records
                    # ]
                    job_id = str(uuid.uuid4())
                    storage[job_id] = records

                    all_sheets_data[sheet_name] = {
                        "columns": list(df.columns),
                        "shape": df.shape,
                        "total_rows": len(records),
                        "job_id": job_id,
                        "data": records[:10000],        # ✅ first 10000 only
                        "next_offset": 10000 if len(records) > 10000 else None,
                        "done": len(records) <= 10000
                    }

                except Exception as sheet_error:
                    import traceback
                    print(f"⚠️ Error in sheet {sheet_name}:", str(sheet_error))
                    traceback.print_exc()

        # =========================
        # ✅ HANDLE CSV FILES
        # =========================
        elif ext == "csv":
            df = pd.read_csv(
                temp_file_path,
                header=None,
                dtype=object
            )

            if df is None or df.empty:
                return JSONResponse(
                    status_code=400,
                    content={"error": "CSV is empty"}
                )

            if len(df) > MAX_ROWS:
                return JSONResponse(
                    status_code=400,
                    content={"error": f"CSV too large ({len(df)} rows). Limit is {MAX_ROWS}."}
                )

            # 🔥 SAFE HEADER
            raw_header = df.iloc[0].tolist()

            clean_header = []
            for i, col in enumerate(raw_header):
                if pd.isna(col) or str(col).strip() == "":
                    clean_header.append(f"Column_{i}")
                else:
                    clean_header.append(str(col).strip())

            df.columns = clean_header
            df = df[1:]
            df.reset_index(drop=True, inplace=True)

            # 🔥 REMOVE ONLY TRUE "Unnamed"
            unnamed_cols = get_unnamed_columns(df)
            if unnamed_cols:
                df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed")]

            # 🔥 HANDLE DATES
            for col in df.columns:
                try:
                    df[col] = pd.to_datetime(df[col], errors="ignore")
                    if pd.api.types.is_datetime64_any_dtype(df[col]):
                        df[col] = df[col].dt.strftime("%Y-%m-%d %H:%M:%S")
                except:
                    pass

            numeric_cols = df.select_dtypes(include=[np.number]).columns
            if len(numeric_cols) > 0:
                df[numeric_cols] = df[numeric_cols].round(3)

            df_clean = df.replace({
                np.nan: None,
                np.inf: None,
                -np.inf: None
            })

            records = df_clean.to_dict(orient="records")
            job_id = str(uuid.uuid4())

            storage[job_id] = records

            all_sheets_data["csv"] = {
                "columns": list(df.columns),
                "shape": df.shape,
                "total_rows": len(records),
                "job_id": job_id,
                "data": records[:10000],
                "next_offset": 10000 if len(records) > 10000 else None,
                "done": len(records) <= 10000
            }

        # =========================
        # ❌ UNSUPPORTED FILE
        # =========================
        else:
            return JSONResponse(
                status_code=400,
                content={"error": f"Unsupported file type: {ext}"}
            )

        # =========================
        # ✅ FINAL RESPONSE
        # =========================
        return {
            "message": "File processed successfully",
            "file_info": {
                "filename": file.filename,
                "file_type": ext,
                "sheets": list(all_sheets_data.keys()),
                "sheets_data": all_sheets_data
            },
        }

    except Exception as e:
        print("🔥 FULL ERROR:")
        traceback.print_exc()

        return JSONResponse(
            status_code=500,
            content={"error": str(e)}
        )

    finally:
        print(f"🧹 Cleanup starting for: {temp_file_path}")
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                import gc
                gc.collect()
                os.remove(temp_file_path)
                session_folder = temp_file_path.parent
                if session_folder.exists():
                    shutil.rmtree(session_folder)
                print("🧹 Cleanup successful")
            except Exception as cleanup_error:
                print(f"⚠️ Cleanup failed: {cleanup_error}")
        else:
            print("🧹 Nothing to clean up")

@router.get("/process-file")
async def get_next_batch(job_id: str, offset: int = 0, limit: int = 10000):
    if job_id not in storage:
        return JSONResponse(status_code=404, content={"error": "job_id not found"})

    records = storage[job_id]
    page = records[offset:offset + limit]
    next_offset = offset + limit

    return {
        "job_id": job_id,
        "offset": offset,
        "limit": limit,
        "count": len(page),
        "total_rows": len(records),
        "next_offset": next_offset if next_offset < len(records) else None,
        "done": next_offset >= len(records),
        "data": page
    }

#"773d0481-be1a-4787-a2ba-2c637cb953dc"