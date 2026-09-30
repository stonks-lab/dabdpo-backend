from fastapi import APIRouter, Query
from typing import Optional
from app.metrics.calculations import saldo_por_contrato, home_kpis

router = APIRouter(prefix="/saldo", tags=["saldo"])


@router.get("/kpis")
def get_saldo_kpis():
    k = home_kpis()
    return {
        "presupuesto_total_clp": k["presupuesto_total_clp"],
        "ep_consumido_clp":      k["ep_consumido_clp"],
        "saldo_real_clp":        k["saldo_real_clp"],
        "pct_consumido":         k["pct_consumido"],
        "contratos_criticos":    k["contratos_criticos"],
        "contratos_advertencia": k["contratos_advertencia"],
    }


@router.get("/contratos")
def get_contratos(
    estado_saldo: Optional[str] = Query(None),
    area: Optional[str] = Query(None),
    tipo: Optional[str] = Query(None),
    estado_ctt: Optional[str] = Query(None),
):
    df = saldo_por_contrato()
    if estado_saldo:
        df = df[df["Estado_Saldo"] == estado_saldo]
    if area:
        df = df[df["Area_contrato"] == area]
    if tipo:
        df = df[df["Tipo_contrato"] == tipo]
    if estado_ctt:
        df = df[df["Estado_ctt"] == estado_ctt]

    return df.fillna("").sort_values("Pct_Saldo").to_dict("records")


@router.get("/filtros")
def get_filtros():
    df = saldo_por_contrato()
    return {
        "areas":         sorted(df["Area_contrato"].dropna().unique().tolist()),
        "tipos":         sorted(df["Tipo_contrato"].dropna().unique().tolist()),
        "estados_ctt":   sorted(df["Estado_ctt"].dropna().unique().tolist()),
        "estados_saldo": ["NEGATIVO", "CRÍTICO", "ADVERTENCIA", "ATENCIÓN", "OK"],
    }
