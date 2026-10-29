from fastapi import APIRouter, Query
from typing import Optional
from app.metrics.calculations import alertas_contratos, alertas_boletas
from app.data.loader import load_boletas

router = APIRouter(prefix="/alertas", tags=["alertas"])


@router.get("/kpis")
def get_kpis():
    bol = load_boletas()
    return {
        "contratos_criticos":    len([r for r in alertas_contratos() if r["Estado_Saldo"] == "CRÍTICO"]),
        "contratos_negativos":   len([r for r in alertas_contratos() if r["Estado_Saldo"] == "NEGATIVO"]),
        "contratos_advertencia": len([r for r in alertas_contratos() if r["Estado_Saldo"] == "ADVERTENCIA"]),
        "boletas_criticas":      int((bol["Alerta_Vencimiento"] == "CRÍTICO").sum()) if "Alerta_Vencimiento" in bol.columns else 0,
        "boletas_advertencia":   int((bol["Alerta_Vencimiento"] == "ADVERTENCIA").sum()) if "Alerta_Vencimiento" in bol.columns else 0,
    }


@router.get("/filtros")
def get_filtros():
    rows = alertas_contratos()
    return {
        "areas": sorted({r["Area_contrato"] for r in rows if r.get("Area_contrato")}),
        "tipos": sorted({r["Tipo_contrato"]  for r in rows if r.get("Tipo_contrato")}),
    }


@router.get("/contratos")
def get_contratos(
    area:         str = Query(""),
    tipo:         str = Query(""),
    estado_saldo: str = Query(""),
):
    rows = alertas_contratos()
    if area:
        rows = [r for r in rows if area.lower() in (r.get("Area_contrato") or "").lower()]
    if tipo:
        rows = [r for r in rows if tipo.lower() in (r.get("Tipo_contrato") or "").lower()]
    if estado_saldo:
        rows = [r for r in rows if r.get("Estado_Saldo") == estado_saldo]
    return rows


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
