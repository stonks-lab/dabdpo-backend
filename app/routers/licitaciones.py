import time
import httpx
from datetime import datetime
from fastapi import APIRouter, Query
from typing import Optional
import pandas as pd
from app.data.loader import load_solpe
from app.metrics.calculations import licitaciones_kpis, solpes_por_estado, solpes_por_anio

router = APIRouter(prefix="/licitaciones", tags=["licitaciones"])

MONDAY_PROCESOS_BOARD_ID     = 6515875846
MONDAY_PROCESOS_SUB_BOARD_ID = 6515876193

_CACHE: dict = {}
_CACHE_TTL   = 300  # 5 minutos


# ── Monday helpers ────────────────────────────────────────────────────────────

def _monday_headers() -> dict:
    from app.config import MONDAY_API_KEY
    return {
        "Authorization": MONDAY_API_KEY,
        "Content-Type":  "application/json",
        "API-Version":   "2024-01",
    }


_GQL_ITEMS = """
query($board: [ID!]!, $cursor: String, $limit: Int!) {
  boards(ids: $board) {
    items_page(limit: $limit, cursor: $cursor) {
      cursor
      items {
        id
        name
        group { title }
        column_values { id text }
      }
    }
  }
}
"""


def _fetch_board_items(board_id: int) -> list[dict]:
    cache_key = f"board_{board_id}"
    now = time.time()
    if cache_key in _CACHE and now - _CACHE[cache_key]["ts"] < _CACHE_TTL:
        return _CACHE[cache_key]["data"]

    items: list[dict] = []
    cursor = None
    try:
        with httpx.Client(timeout=30, verify=False) as client:
            while True:
                variables: dict = {"board": [str(board_id)], "limit": 200}
                if cursor:
                    variables["cursor"] = cursor
                resp = client.post(
                    "https://api.monday.com/v2",
                    headers=_monday_headers(),
                    json={"query": _GQL_ITEMS, "variables": variables},
                )
                data = resp.json()
                if "errors" in data:
                    break
                page = data["data"]["boards"][0]["items_page"]
                items.extend(page["items"])
                cursor = page.get("cursor")
                if not cursor or len(page["items"]) < 200:
                    break
    except Exception:
        pass

    _CACHE[cache_key] = {"ts": now, "data": items}
    return items


def _parse_timeline(text: str) -> tuple[str | None, str | None]:
    """Parsea 'YYYY-MM-DD - YYYY-MM-DD' → (start, end)."""
    if not text:
        return None, None
    parts = [p.strip() for p in text.split(" - ")]
    if len(parts) == 2:
        return parts[0] or None, parts[1] or None
    return None, None


def _days_between(start: str | None, end: str | None) -> float | None:
    if not start or not end:
        return None
    try:
        return (datetime.fromisoformat(end) - datetime.fromisoformat(start)).days
    except Exception:
        return None


# ── Loaders ───────────────────────────────────────────────────────────────────

def _load_procesos() -> list[dict]:
    raw = _fetch_board_items(MONDAY_PROCESOS_BOARD_ID)
    result = []
    for item in raw:
        cv = {c["id"]: (c.get("text") or "") for c in item["column_values"]}

        num_proceso = cv.get("texto__1", "").strip()
        if not num_proceso:
            continue

        ini_prog, fin_prog = _parse_timeline(cv.get("timerange7__1", ""))
        ini_real, fin_real = _parse_timeline(cv.get("cronograma12__1", ""))
        ciclo_est  = _days_between(ini_prog, fin_prog)
        ciclo_real = _days_between(ini_real, fin_real)
        delta_dias = (ciclo_real - ciclo_est) if (ciclo_est is not None and ciclo_real is not None) else None

        result.append({
            "N°PROCESO":                        num_proceso,
            "Proceso":                          item["name"],
            "Avance Gral Proceso":              cv.get("color7__1", ""),
            "Etapa Actual":                     cv.get("estado_16", ""),
            "Tipo Proceso":                     cv.get("label9__1", ""),
            "Estado Inicio-Fin":                cv.get("estado__1", ""),
            "Estado Adjudicación":              cv.get("estado0__1", ""),
            "Fecha Inicio Servicio (Estimada)": cv.get("date_mkwpvwmh") or None,
            "Fecha inicio programado":          ini_prog,
            "Fecha fin programado":             fin_prog,
            "Fecha inicio real":                ini_real,
            "Fecha fin real":                   fin_real,
            "Ciclo estimado":                   ciclo_est,
            "Ciclo real":                       ciclo_real,
            "delta_dias":                       delta_dias,
            "Responsable":                      cv.get("personas5", ""),
            "N° Solpe":                         cv.get("texto9", ""),
            "Año":                              cv.get("text_mkrv1410", ""),
            "grupo":                            item.get("group", {}).get("title", ""),
        })
    return result


def _load_detalles(proceso: str) -> list[dict]:
    raw = _fetch_board_items(MONDAY_PROCESOS_SUB_BOARD_ID)
    result = []
    for item in raw:
        cv = {c["id"]: (c.get("text") or "") for c in item["column_values"]}
        n_proceso = cv.get("n__proceso__1", "").strip()
        if not n_proceso or proceso.lower() not in n_proceso.lower():
            continue

        ini_prog, fin_prog = _parse_timeline(cv.get("timerange__1", ""))
        ini_real, fin_real = _parse_timeline(cv.get("real_real__1", ""))

        ciclo_prog = cv.get("n_meros__1", "")

        result.append({
            "Subelemento":            cv.get("etapa__1", "") or item["name"],
            "N° PROCESO":             n_proceso,
            "Fecha Inicio Acta inicio": ini_prog,
            "Fecha Fin Acta Inicio":  fin_prog,
            "Inicio real":            ini_real,
            "Fin real":               fin_real,
            "Ciclo programado":       float(ciclo_prog) if ciclo_prog else None,
            "Ciclo real":             _days_between(ini_real, fin_real),
            "Área Responsable":       cv.get("color_mkxwnv9b", "") or cv.get("text_mkxwnv9b", "") or cv.get("dropdown_mkxwnv9b", ""),
            "Estado_Inicio-Fin":      cv.get("prueba_estado__1", "") or cv.get("estado__1", ""),
            "Avance Etapas":          cv.get("prueba_avance__1", ""),
            "Justificación":          cv.get("observaci_n__1", ""),
        })
    return result


# ── Endpoints: Procesos de Licitación (Monday) ────────────────────────────────

@router.get("/procesos/kpis")
def get_procesos_kpis():
    procesos = _load_procesos()
    if not procesos:
        return {}
    return {
        "total":      len(procesos),
        "a_tiempo":   sum(1 for p in procesos if p["Avance Gral Proceso"] == "A TIEMPO"),
        "riesgo":     sum(1 for p in procesos if p["Avance Gral Proceso"] == "RIESGO"),
        "atrasado":   sum(1 for p in procesos if p["Avance Gral Proceso"] == "ATRASADO"),
        "programado": sum(1 for p in procesos if p["Avance Gral Proceso"] == "PROGRAMADO"),
        "finalizado": sum(1 for p in procesos if p["Etapa Actual"] == "Finalizado"),
    }


@router.get("/procesos")
def get_procesos(avance: Optional[str] = Query(None)):
    procesos = _load_procesos()
    if avance:
        procesos = [p for p in procesos if p["Avance Gral Proceso"] == avance]
    return procesos


@router.get("/procesos/detalle")
def get_proceso_detalle(proceso: str = Query(...)):
    return _load_detalles(proceso)


@router.get("/procesos/detalle/debug")
def get_proceso_detalle_debug(proceso: str = Query(...)):
    """Devuelve los column_values crudos del primer ítem que coincida con el proceso."""
    raw = _fetch_board_items(MONDAY_PROCESOS_SUB_BOARD_ID)
    for item in raw:
        cv = {c["id"]: (c.get("text") or "") for c in item["column_values"]}
        n_proceso = cv.get("n__proceso__1", "").strip()
        if n_proceso and proceso.lower() in n_proceso.lower():
            return {"item_name": item["name"], "column_values": cv}
    return {"error": "No se encontró ítem para ese proceso"}


# ── Endpoints: Pipeline SOLPE ─────────────────────────────────────────────────

@router.get("/debug-estados")
def get_debug_estados():
    """Muestra los valores únicos de Estado_SOLPE y a qué grupo caen."""
    try:
        from app.data.loader import load_solpe
        load_solpe.cache_clear()
        sol = load_solpe()
        columnas = list(sol.columns)
        if "Estado_SOLPE" not in columnas:
            return {"error": "Sin columna Estado_SOLPE", "columnas": columnas, "filas": len(sol)}
        conteo = (
            sol.groupby(["Estado_SOLPE", "Grupo_Estado"], dropna=False)
            .size()
            .reset_index(name="cantidad")
            .sort_values("Grupo_Estado")
        )
        return {
            "filas_total": len(sol),
            "estados": conteo.fillna("(vacío)").to_dict("records"),
        }
    except Exception as e:
        return {"error": str(e)}


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

    tabla_cols = [c for c in ["Código", "Nombre", "Grupo_Estado",
                               "Fecha_Creacion_SOLPE", "Fecha_Adjudicacion", "Fecha_Suscripcion",
                               "dias_etapa1", "dias_etapa2", "dias_total"] if c in sol.columns]
    tabla = sol[tabla_cols].copy()
    for c in tabla.columns:
        if pd.api.types.is_datetime64_any_dtype(tabla[c]):
            tabla[c] = tabla[c].dt.strftime("%Y-%m-%d")

    return {"kpis": kpis, "por_mes": por_mes, "por_estado": por_estado,
            "tabla": json.loads(tabla.to_json(orient="records"))}
