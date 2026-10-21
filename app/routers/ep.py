from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/ep", tags=["ep"])


@router.get("/kpis")
def get_kpis(
    area:           Optional[str] = Query(None),
    tipo:           Optional[str] = Query(None),
    contrato:       Optional[str] = Query(None),
    estado_ctt:     Optional[str] = Query(None),
    proyecto:       Optional[str] = Query(None),
    periodo_desde:  Optional[str] = Query(None),
    periodo_hasta:  Optional[str] = Query(None),
):
    from app.metrics.calculations import ep_kpis
    return ep_kpis(
        area=area or '', tipo=tipo or '', contrato=contrato or '',
        estado_ctt=estado_ctt or '', proyecto=proyecto or '',
        periodo_desde=periodo_desde or '', periodo_hasta=periodo_hasta or '',
    )


@router.get("/filtros")
def get_filtros():
    from app.metrics.calculations import ep_filtros
    return ep_filtros()


@router.get("/tabla")
def get_tabla(
    area:           Optional[str] = Query(None),
    tipo:           Optional[str] = Query(None),
    contrato:       Optional[str] = Query(None),
    estado_ctt:     Optional[str] = Query(None),
    proyecto:       Optional[str] = Query(None),
    periodo_desde:  Optional[str] = Query(None),
    periodo_hasta:  Optional[str] = Query(None),
):
    from app.metrics.calculations import ep_tabla
    return ep_tabla(
        area=area or '', tipo=tipo or '', contrato=contrato or '',
        estado_ctt=estado_ctt or '', proyecto=proyecto or '',
        periodo_desde=periodo_desde or '', periodo_hasta=periodo_hasta or '',
    )


@router.get("/por-mes")
def get_por_mes(
    area:           Optional[str] = Query(None),
    tipo:           Optional[str] = Query(None),
    contrato:       Optional[str] = Query(None),
    estado_ctt:     Optional[str] = Query(None),
    proyecto:       Optional[str] = Query(None),
    periodo_desde:  Optional[str] = Query(None),
    periodo_hasta:  Optional[str] = Query(None),
):
    from app.metrics.calculations import ep_por_mes
    return ep_por_mes(
        area=area or '', tipo=tipo or '', contrato=contrato or '',
        estado_ctt=estado_ctt or '', proyecto=proyecto or '',
        periodo_desde=periodo_desde or '', periodo_hasta=periodo_hasta or '',
    )


@router.get("/por-tipo")
def get_por_tipo(
    area:           Optional[str] = Query(None),
    contrato:       Optional[str] = Query(None),
    estado_ctt:     Optional[str] = Query(None),
    proyecto:       Optional[str] = Query(None),
    periodo_desde:  Optional[str] = Query(None),
    periodo_hasta:  Optional[str] = Query(None),
):
    from app.metrics.calculations import ep_por_tipo
    return ep_por_tipo(
        area=area or '', contrato=contrato or '',
        estado_ctt=estado_ctt or '', proyecto=proyecto or '',
        periodo_desde=periodo_desde or '', periodo_hasta=periodo_hasta or '',
    )


@router.get("/timeline")
def get_timeline(
    area:           Optional[str] = Query(None),
    tipo:           Optional[str] = Query(None),
    contrato:       Optional[str] = Query(None),
    estado_ctt:     Optional[str] = Query(None),
    proyecto:       Optional[str] = Query(None),
    periodo_desde:  Optional[str] = Query(None),
    periodo_hasta:  Optional[str] = Query(None),
    limite:         int           = Query(60),
):
    from app.metrics.calculations import ep_timeline
    return ep_timeline(
        area=area or '', tipo=tipo or '', contrato=contrato or '',
        estado_ctt=estado_ctt or '', proyecto=proyecto or '',
        periodo_desde=periodo_desde or '', periodo_hasta=periodo_hasta or '',
        limite=limite,
    )
