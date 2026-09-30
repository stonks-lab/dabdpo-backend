import io
import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/proyecciones-pyc")
async def upload_proyecciones_pyc(file: UploadFile = File(...)):
    if not file.filename.endswith('.parquet'):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .parquet")

    content = await file.read()
    try:
        df = pd.read_parquet(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"No se pudo leer el parquet: {e}")

    try:
        from app.data.sql_server import load_proyecciones_pyc
        result = load_proyecciones_pyc(df)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar en SQL Server: {e}")

    return result


@router.post("/dimensiones")
async def upload_dimensiones(file: UploadFile = File(...)):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx o .xls")

    content = await file.read()
    try:
        from app.data.sql_server import load_dimensiones
        result = load_dimensiones(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar en SQL Server: {e}")

    return result


@router.post("/incidencias")
async def upload_incidencias(file: UploadFile = File(...)):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx o .xls")

    content = await file.read()
    try:
        from app.data.sql_server import load_incidencias
        result = load_incidencias(content)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar en SQL Server: {e}")

    return result
