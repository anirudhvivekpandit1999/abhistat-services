from fastapi import APIRouter, File, UploadFile
from fastapi.responses import JSONResponse
import uuid
import shutil
import numpy as np
import os
import traceback
import pandas as pd

from core.config import TEMP_DIR
from utils import get_unnamed_columns

storage = {}

router = APIRouter()

MAX_ROWS = 100000  # 🔥 safety limit


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

            for sheet_name in sheets:
                try:
                    # 🔥 READ RAW (NO TYPE GUESSING, NO HEADER ASSUMPTIONS)
                    df = pd.read_excel(
                        excel_file,
                        sheet_name=sheet_name,
                        header=None,
                        dtype=object
                    )

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
                    records = df_clean.to_dict(orient="records")
                    job_id = str(uuid.uuid4())
                    storage[job_id] = records  # save ALL rows

                    all_sheets_data[sheet_name] = {
                        "columns": list(df.columns),
                        "shape": df.shape,
                        "total_rows": len(records),
                        "job_id": job_id,
                        "data": records[:100],        # ✅ first 100 only
                        "next_offset": 100 if len(records) > 100 else None,
                        "done": len(records) <= 100
                    }

                except Exception as sheet_error:
                    print(f"⚠️ Error in sheet {sheet_name}:", str(sheet_error))

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
                "data": records[:100],
                "next_offset": 100 if len(records) > 100 else None,
                "done": len(records) <= 100
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
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except:
                pass

@router.get("/process-file")
async def get_next_batch(job_id: str, offset: int = 0, limit: int = 100):
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