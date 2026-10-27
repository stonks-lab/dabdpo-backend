from fastapi import APIRouter
from pydantic import BaseModel

router = APIRouter(prefix="/dpo", tags=["dpo"])


@router.get("/costo-metro")
def get_costo_metro(campana: str = ''):
    from app.data.sql_server import get_costo_metro_historico, get_costo_metro_ultimo, get_config_dpo
    camp = campana or 'TOTAL'
    return {
        "historico": get_costo_metro_historico(camp),
        "ultimo":    get_costo_metro_ultimo(camp),
        "config":    get_config_dpo(),
    }


@router.get("/costo-metro/meses")
def get_meses():
    from app.data.sql_server import get_costo_metro_meses
    return get_costo_metro_meses()


@router.delete("/costo-metro/{anio}/{mes}")
def delete_mes(anio: int, mes: int):
    from app.data.sql_server import delete_costo_metro_mes
    return delete_costo_metro_mes(anio, mes)


@router.get("/proyectos-inicio")
def get_proyectos_inicio(fecha_desde: str | None = None, fecha_hasta: str | None = None):
    from app.data.sql_server import get_proyectos_inicio_resumen
    return get_proyectos_inicio_resumen(fecha_desde, fecha_hasta)


@router.get("/config")
def get_config():
    from app.data.sql_server import get_config_dpo
    return get_config_dpo()


class ConfigDPOBody(BaseModel):
    meta_costo_metro:       float
    meta_tolerancia_brecha: float


@router.post("/config")
def save_config(body: ConfigDPOBody, usuario: str = ""):
    from app.data.sql_server import upsert_config_dpo, log_carga
    result = upsert_config_dpo(body.meta_costo_metro, body.meta_tolerancia_brecha)
    log_carga("dbo.ConfigDPO", "Plataforma", "Meta Costo Metro", usuario or None)
    return result
