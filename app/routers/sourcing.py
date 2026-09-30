from fastapi import APIRouter, HTTPException
from app.config import SQL_SOURCING_ENABLED

router = APIRouter(prefix="/sourcing", tags=["sourcing"])


def _check():
    if not SQL_SOURCING_ENABLED:
        raise HTTPException(status_code=503, detail="Sourcing Plan no disponible. Ejecutá el ETL primero.")


@router.get("/kpis")
def get_kpis():
    _check()
    from app.data.sql_server import get_sourcing_kpis
    return get_sourcing_kpis()


@router.get("/por-mes")
def get_por_mes():
    _check()
    from app.data.sql_server import get_sourcing_por_mes
    return get_sourcing_por_mes()


@router.get("/por-estatus")
def get_por_estatus():
    _check()
    from app.data.sql_server import get_sourcing_por_estatus
    return get_sourcing_por_estatus()


@router.get("/por-proyecto")
def get_por_proyecto():
    _check()
    from app.data.sql_server import get_sourcing_por_proyecto
    return get_sourcing_por_proyecto()


@router.get("/tabla")
def get_tabla():
    _check()
    from app.data.sql_server import get_sourcing_tabla
    return get_sourcing_tabla()
