from fastapi import APIRouter, Query
from typing import Optional
import pandas as pd

router = APIRouter(prefix="/estado-proyectos", tags=["estado-proyectos"])


@router.get("/incidencias")
def get_incidencias():
    from app.data.sql_server import get_incidencias
    return get_incidencias()


@router.get("/resumen")
def get_resumen(
    proyecto: Optional[str] = Query(None),
    contrato: Optional[str] = Query(None),
    campana:  Optional[str] = Query(None),
):
    from app.data.sql_server import get_incidencias, get_proyectos_por_campanas
    rows = get_incidencias()

    effective_proy: set | None = None
    if campana:
        camp_list = [c.strip() for c in campana.split(",") if c.strip()]
        effective_proy = set(get_proyectos_por_campanas(camp_list))
    if proyecto:
        proy_list = {p.strip() for p in proyecto.split(",") if p.strip()}
        effective_proy = proy_list if effective_proy is None else effective_proy & proy_list
    if effective_proy is not None:
        rows = [r for r in rows if r.get("proyecto") in effective_proy]

    if contrato:
        ctt_list = {c.strip().lower() for c in contrato.split(",") if c.strip()}
        rows = [r for r in rows if r.get("contrato", "").strip().lower() in ctt_list]
    if not rows:
        return {"activas": 0, "pendientes": 0, "alto_impacto": 0,
                "compromisos_pendientes": 0, "total": 0,
                "por_impacto": [], "por_tipo": []}

    df = pd.DataFrame(rows)
    estado_norm = df["estado"].astype(str).str.strip().str.lower() if "estado" in df.columns else pd.Series(dtype=str)

    activas    = int(estado_norm.eq("activa").sum())
    pendientes = int(estado_norm.eq("pendiente").sum())

    alto_impacto = 0
    if "impacto" in df.columns:
        alto_impacto = int(df["impacto"].astype(str).str.strip().str.lower().eq("alto").sum())

    compromisos_pendientes = 0
    if "estado_compromiso" in df.columns:
        compromisos_pendientes = int(
            df["estado_compromiso"].astype(str).str.strip().str.lower().eq("pendiente").sum()
        )

    por_impacto = []
    if "impacto" in df.columns:
        gi = (df[df["impacto"].astype(str).str.strip() != ""]
              .groupby("impacto").size().reset_index(name="count"))
        # orden fijo: Alto > Medio > Bajo
        orden = {"Alto": 0, "Medio": 1, "Bajo": 2}
        gi["_ord"] = gi["impacto"].map(orden).fillna(99)
        por_impacto = gi.sort_values("_ord").drop(columns="_ord").to_dict("records")

    por_tipo = []
    if "tipo" in df.columns:
        gt = (df[df["tipo"].astype(str).str.strip() != ""]
              .groupby("tipo").size().reset_index(name="count"))
        por_tipo = gt.sort_values("count", ascending=False).head(6).to_dict("records")

    return {
        "activas":               activas,
        "pendientes":            pendientes,
        "alto_impacto":          alto_impacto,
        "compromisos_pendientes": compromisos_pendientes,
        "total":                 len(df),
        "por_impacto":           por_impacto,
        "por_tipo":              por_tipo,
    }
