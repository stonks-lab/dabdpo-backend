from fastapi import APIRouter, Query
from typing import Optional
import pandas as pd
from app.data.loader import load_solpe
from app.metrics.calculations import licitaciones_kpis, solpes_por_estado, solpes_por_anio

router = APIRouter(prefix="/licitaciones", tags=["licitaciones"])


@router.get("/kpis")
def get_kpis():
    return licitaciones_kpis()


@router.get("/por-estado")
def get_por_estado():
    return solpes_por_estado()


@router.get("/por-anio")
def get_por_anio():
    return solpes_por_anio()


@router.get("/tabla")
def get_tabla(
    grupo_estado: Optional[str] = Query(None),
    anio: Optional[int] = Query(None),
):
    sol = load_solpe().copy()
    if grupo_estado:
        sol = sol[sol["Grupo_Estado"] == grupo_estado]
    if anio and "Fecha_Creacion_SOLPE" in sol.columns:
        sol["_yr"] = pd.to_datetime(sol["Fecha_Creacion_SOLPE"], errors="coerce").dt.year
        sol = sol[sol["_yr"] == anio]

    cols = [c for c in [
        "Código", "Nombre", "Tipo", "Forma", "Moneda", "Monto",
        "Contrato", "Estado_SOLPE", "Grupo_Estado",
        "Usuario Solicitante", "Ges. de Contratos y Compras",
        "Dias_Gestion", "Fecha_Creacion_SOLPE",
    ] if c in sol.columns]

    result = sol[cols].copy()
    for c in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[c]):
            result[c] = result[c].dt.strftime("%Y-%m-%d")
    return result.fillna("").to_dict("records")
