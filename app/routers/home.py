from fastapi import APIRouter
from app.metrics.calculations import home_kpis, ep_por_mes, ep_por_tipo_contrato

router = APIRouter(prefix="/home", tags=["home"])


@router.get("/kpis")
def get_kpis():
    return home_kpis()


@router.get("/ep-por-mes")
def get_ep_por_mes():
    return ep_por_mes()


@router.get("/ep-por-tipo")
def get_ep_por_tipo():
    return ep_por_tipo_contrato()
