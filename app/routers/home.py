from fastapi import APIRouter, Query
from typing import Optional
from app.metrics.calculations import home_kpis, home_filtros, ep_por_mes, ep_por_tipo_contrato

router = APIRouter(prefix="/home", tags=["home"])


@router.get("/kpis")
def get_kpis(
    estado_ctt: Optional[str] = Query(None),
    area:       Optional[str] = Query(None),
    tipo:       Optional[str] = Query(None),
    desde:      Optional[str] = Query(None),
    hasta:      Optional[str] = Query(None),
    proyecto:   Optional[str] = Query(None),
    contrato:   Optional[str] = Query(None),
    campana:    Optional[str] = Query(None),
):
    return home_kpis(estado_ctt or '', area or '', tipo or '', desde or '', hasta or '', proyecto or '', contrato or '', campana or '')


@router.get("/filtros")
def get_filtros():
    return home_filtros()


@router.get("/ep-por-mes")
def get_ep_por_mes(
    estado_ctt: Optional[str] = Query(None),
    area:       Optional[str] = Query(None),
    tipo:       Optional[str] = Query(None),
    desde:      Optional[str] = Query(None),
    hasta:      Optional[str] = Query(None),
    proyecto:   Optional[str] = Query(None),
    contrato:   Optional[str] = Query(None),
    campana:    Optional[str] = Query(None),
):
    return ep_por_mes(estado_ctt or '', area or '', tipo or '', desde or '', hasta or '', proyecto or '', contrato or '', campana or '')


@router.get("/ep-por-tipo")
def get_ep_por_tipo(
    estado_ctt: Optional[str] = Query(None),
    area:       Optional[str] = Query(None),
    tipo:       Optional[str] = Query(None),
    desde:      Optional[str] = Query(None),
    hasta:      Optional[str] = Query(None),
    proyecto:   Optional[str] = Query(None),
    contrato:   Optional[str] = Query(None),
    campana:    Optional[str] = Query(None),
):
    return ep_por_tipo_contrato(estado_ctt or '', area or '', tipo or '', desde or '', hasta or '', proyecto or '', contrato or '', campana or '')


@router.get("/campana-proyectos")
def get_campana_proyectos(campana: Optional[str] = Query(None)):
    if not campana:
        return []
    from app.data.sql_server import get_proyectos_por_campanas
    camp_list = [c.strip() for c in campana.split(",") if c.strip()]
    return get_proyectos_por_campanas(camp_list)
