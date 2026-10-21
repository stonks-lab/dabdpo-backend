from fastapi import APIRouter, Query
from typing import Optional
from app.metrics.calculations import analisis_kpis, analisis_por_proyecto, analisis_contratos_por_proyecto

router = APIRouter(prefix="/analisis", tags=["analisis"])


@router.get("/kpis")
def get_kpis():
    return analisis_kpis()


@router.get("/por-proyecto")
def get_por_proyecto():
    return analisis_por_proyecto()


@router.get("/tabla")
def get_tabla(proyecto: Optional[str] = Query(None)):
    return analisis_contratos_por_proyecto(proyecto=proyecto)
