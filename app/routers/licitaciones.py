from fastapi import APIRouter, Query
from typing import Optional
import pandas as pd
import numpy as np
from app.data.loader import load_solpe
from app.metrics.calculations import licitaciones_kpis, solpes_por_estado, solpes_por_anio

router = APIRouter(prefix="/licitaciones", tags=["licitaciones"])


@router.get("/kpis")
def get_kpis():
    return licitaciones_kpis()


@router.get("/por-estado")
def get_por_estado():
    return solpes_por_estado()


@router.get("/por-anio")
def get_por_anio():
    return solpes_por_anio()


@router.get("/tabla")
def get_tabla(
    grupo_estado: Optional[str] = Query(None),
    anio: Optional[int] = Query(None),
):
    sol = load_solpe().copy()
    if grupo_estado:
        sol = sol[sol["Grupo_Estado"] == grupo_estado]
    if anio and "Fecha_Creacion_SOLPE" in sol.columns:
        sol["_yr"] = pd.to_datetime(sol["Fecha_Creacion_SOLPE"], errors="coerce").dt.year
        sol = sol[sol["_yr"] == anio]

    cols = [c for c in [
        "Código", "Nombre", "Tipo", "Forma", "Moneda", "Monto",
        "Contrato", "Estado_SOLPE", "Grupo_Estado",
        "Usuario Solicitante", "Ges. de Contratos y Compras",
        "Dias_Gestion", "Fecha_Creacion_SOLPE",
    ] if c in sol.columns]

    result = sol[cols].copy()
    for c in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[c]):
            result[c] = result[c].dt.strftime("%Y-%m-%d")
    return result.fillna("").to_dict("records")


@router.get("/tiempos")
def get_tiempos():
    import json
    sol = load_solpe().copy()

    date_cols = ["Fecha_Creacion_SOLPE", "Fecha_Adjudicacion", "Fecha_Suscripcion"]
    for c in date_cols:
        if c in sol.columns:
            sol[c] = pd.to_datetime(sol[c], errors="coerce")

    if "Fecha_Creacion_SOLPE" in sol.columns and "Fecha_Adjudicacion" in sol.columns:
        mask1 = sol["Fecha_Creacion_SOLPE"].notna() & sol["Fecha_Adjudicacion"].notna()
        if mask1.any():
            sol.loc[mask1, "dias_etapa1"] = (
                sol.loc[mask1, "Fecha_Adjudicacion"] - sol.loc[mask1, "Fecha_Creacion_SOLPE"]
            ).dt.days

    if "Fecha_Adjudicacion" in sol.columns and "Fecha_Suscripcion" in sol.columns:
        mask2 = sol["Fecha_Adjudicacion"].notna() & sol["Fecha_Suscripcion"].notna()
        if mask2.any():
            sol.loc[mask2, "dias_etapa2"] = (
                sol.loc[mask2, "Fecha_Suscripcion"] - sol.loc[mask2, "Fecha_Adjudicacion"]
            ).dt.days

    if "Fecha_Creacion_SOLPE" in sol.columns and "Fecha_Suscripcion" in sol.columns:
        mask_t = sol["Fecha_Creacion_SOLPE"].notna() & sol["Fecha_Suscripcion"].notna()
        if mask_t.any():
            sol.loc[mask_t, "dias_total"] = (
                sol.loc[mask_t, "Fecha_Suscripcion"] - sol.loc[mask_t, "Fecha_Creacion_SOLPE"]
            ).dt.days

    def _col(name):
        return sol[name].dropna() if name in sol.columns else pd.Series(dtype=float)

    def _mean(name):
        s = _col(name)
        return round(float(s.mean()), 1) if len(s) else None

    def _med(name):
        s = _col(name)
        return round(float(s.median()), 1) if len(s) else None

    completadas = int(sol["dias_total"].notna().sum()) if "dias_total" in sol.columns else 0

    kpis = {
        "total":         int(len(sol)),
        "completadas":   completadas,
        "prom_total":    _mean("dias_total"),
        "mediana_total": _med("dias_total"),
        "prom_etapa1":   _mean("dias_etapa1"),
        "prom_etapa2":   _mean("dias_etapa2"),
    }

    def _dias_agg():
        agg = {}
        for dc in ["dias_etapa1", "dias_etapa2", "dias_total"]:
            if dc in sol.columns:
                agg[dc] = (dc, "mean")
        return agg

    def _row_stats(r):
        def _v(k): return round(float(r[k]), 1) if k in r.index and pd.notna(r.get(k)) else None
        return {"prom_etapa1": _v("dias_etapa1"), "prom_etapa2": _v("dias_etapa2"), "prom_total": _v("dias_total")}

    # By month of creation
    por_mes = []
    if "Fecha_Creacion_SOLPE" in sol.columns:
        sol["_anio_mes"] = sol["Fecha_Creacion_SOLPE"].dt.to_period("M")
        grp = (
            sol.dropna(subset=["_anio_mes"])
               .groupby("_anio_mes")
               .agg(cantidad=("Grupo_Estado", "count"), **_dias_agg())
               .reset_index()
               .sort_values("_anio_mes")
        )
        for _, r in grp.iterrows():
            por_mes.append({"mes": str(r["_anio_mes"]), "cantidad": int(r["cantidad"]), **_row_stats(r)})

    # By Grupo_Estado
    por_estado = []
    if "Grupo_Estado" in sol.columns:
        cnt_col = "Código" if "Código" in sol.columns else "Fecha_Creacion_SOLPE"
        grp2 = (
            sol.groupby("Grupo_Estado")
               .agg(cantidad=(cnt_col, "count"), **_dias_agg())
               .reset_index()
        )
        for _, r in grp2.iterrows():
            por_estado.append({"estado": r["Grupo_Estado"], "cantidad": int(r["cantidad"]), **_row_stats(r)})

    # Individual table — use to_json for reliable NaN→null conversion
    tabla_cols = [c for c in ["Código", "Nombre", "Grupo_Estado",
                               "Fecha_Creacion_SOLPE", "Fecha_Adjudicacion", "Fecha_Suscripcion",
                               "dias_etapa1", "dias_etapa2", "dias_total"] if c in sol.columns]
    tabla = sol[tabla_cols].copy()
    for c in tabla.columns:
        if pd.api.types.is_datetime64_any_dtype(tabla[c]):
            tabla[c] = tabla[c].dt.strftime("%Y-%m-%d")

    return {"kpis": kpis, "por_mes": por_mes, "por_estado": por_estado,
            "tabla": json.loads(tabla.to_json(orient="records"))}
