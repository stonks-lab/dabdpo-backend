from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from app.config import SQL_PROYECCIONES_ENABLED

router = APIRouter(prefix="/proyecciones", tags=["proyecciones"])

_NO_DATA = {"detail": "Proyecciones no disponibles. Ejecutá el ETL para cargar datos desde P&C."}


def _check():
    if not SQL_PROYECCIONES_ENABLED:
        raise HTTPException(status_code=503, detail=_NO_DATA["detail"])


@router.get("/status")
def get_status():
    """Indica si los datos de proyecciones están disponibles."""
    if not SQL_PROYECCIONES_ENABLED:
        return {"disponible": False, "mensaje": _NO_DATA["detail"]}

    try:
        from app.data.sql_server import get_proyecciones_kpis
        kpis = get_proyecciones_kpis()
        return {"disponible": True, "ultima_carga": kpis["ultima_carga"], "periodo_max": kpis["periodo_max"]}
    except Exception as e:
        return {"disponible": False, "mensaje": str(e)}


@router.get("/kpis")
def get_kpis():
    _check()
    from app.data.sql_server import get_proyecciones_kpis
    return get_proyecciones_kpis()


@router.get("/por-mes")
def get_por_mes():
    _check()
    from app.data.sql_server import get_proyecciones_por_mes
    return get_proyecciones_por_mes()


@router.get("/tabla")
def get_tabla(
    contrato:      Optional[str] = Query(None),
    periodo_desde: Optional[str] = Query(None, description="Formato YYYY-MM"),
    tipo:          Optional[str] = Query(None, description="P&C o AdC"),
):
    _check()
    from app.data.sql_server import get_proyecciones
    df = get_proyecciones(contrato=contrato, periodo_desde=periodo_desde, tipo=tipo)
    return df.fillna("").to_dict("records")


@router.get("/vs-ep")
def get_vs_ep(periodo_desde: Optional[str] = Query(None)):
    _check()
    from app.data.sql_server import get_proyecciones_vs_ep
    df = get_proyecciones_vs_ep(periodo_desde=periodo_desde)
    return df.fillna(0).to_dict("records")
