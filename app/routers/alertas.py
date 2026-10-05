from fastapi import APIRouter, Query
from typing import Optional
from app.metrics.calculations import alertas_contratos, alertas_boletas
from app.data.loader import load_boletas

router = APIRouter(prefix="/alertas", tags=["alertas"])


@router.get("/kpis")
def get_kpis():
    bol = load_boletas()
    av = bol.get("Alerta_Vencimiento") if "Alerta_Vencimiento" in bol.columns else None
    return {
        "contratos_criticos":    len([r for r in alertas_contratos() if r["Estado_Saldo"] == "CRÍTICO"]),
        "contratos_negativos":   len([r for r in alertas_contratos() if r["Estado_Saldo"] == "NEGATIVO"]),
        "contratos_advertencia": len([r for r in alertas_contratos() if r["Estado_Saldo"] == "ADVERTENCIA"]),
        "boletas_criticas":      int((bol["Alerta_Vencimiento"] == "CRÍTICO").sum()) if "Alerta_Vencimiento" in bol.columns else 0,
        "boletas_advertencia":   int((bol["Alerta_Vencimiento"] == "ADVERTENCIA").sum()) if "Alerta_Vencimiento" in bol.columns else 0,
    }


@router.get("/contratos")
def get_contratos():
    return alertas_contratos()


@router.get("/boletas")
def get_boletas(
    estado_ctt: Optional[str] = Query(None),
    area:       Optional[str] = Query(None),
    tipo:       Optional[str] = Query(None),
    proyecto:   Optional[str] = Query(None),
    contrato:   Optional[str] = Query(None),
    campana:    Optional[str] = Query(None),
):
    return alertas_boletas(estado_ctt or '', area or '', tipo or '', proyecto or '', contrato or '', campana or '')
