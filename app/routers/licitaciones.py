import re
import time
import unicodedata
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


# ── Tipo Servicio (similitud de tokens contra dbo.sourcing_plan.tipo) ────────

_RE_PROCESO_PREFIX = re.compile(r'^CO-\S+\s*', re.IGNORECASE)
_STOP_ES = {'de', 'la', 'el', 'los', 'las', 'en', 'y', 'o', 'a', 'del', 'con', 'para',
             'por', 'un', 'una', 'se', 'al', 'e', 'u', 'su', 'que', 'no', 'si'}

def _norm_text(s: str) -> str:
    nfkd = unicodedata.normalize('NFD', s.lower())
    ascii_str = ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
    return re.sub(r'[^a-z0-9]+', ' ', ascii_str).strip()

def _tokens(s: str) -> set[str]:
    return {t for t in _norm_text(s).split() if t not in _STOP_ES and len(t) > 1}

_SOURCING_TIPOS_CACHE: dict = {}

def _get_sourcing_tipos() -> list[str]:
    now = time.time()
    if _SOURCING_TIPOS_CACHE.get('ts', 0) + 3600 > now and _SOURCING_TIPOS_CACHE.get('data'):
        return _SOURCING_TIPOS_CACHE['data']
    try:
        from app.data.sql_server import query_df
        df = query_df("SELECT DISTINCT alcance FROM dbo.sourcing_plan WHERE alcance IS NOT NULL AND LTRIM(RTRIM(alcance)) != ''")
        tipos = sorted({str(r).strip() for r in df['alcance'] if r and str(r).strip()})
        _SOURCING_TIPOS_CACHE.update({'ts': now, 'data': tipos})
        return tipos
    except Exception:
        return _SOURCING_TIPOS_CACHE.get('data', [])

def _tipo_servicio(nombre: str) -> str:
    tipos = _get_sourcing_tipos()
    if not tipos:
        return 'Sin clasificar'
    nombre_limpio = _RE_PROCESO_PREFIX.sub('', nombre)
    name_tok = _tokens(nombre_limpio)
    if not name_tok:
        return 'Sin clasificar'
    best, best_score = '', 0.0
    for tipo in tipos:
        tipo_tok = _tokens(tipo)
        if not tipo_tok:
            continue
        overlap = len(tipo_tok & name_tok)
        if overlap == 0:
            continue
        score = overlap / len(tipo_tok)
        if score > best_score or (score == best_score and len(tipo_tok) > len(_tokens(best))):
            best_score, best = score, tipo
    return best if best_score >= 0.5 else 'Sin clasificar'


# ── Fin estimado por Tipo Servicio ────────────────────────────────────────────

def _add_fin_estimado(procesos: list[dict]) -> None:
    """Agrega fin_estimado a procesos no finalizados basado en ciclo promedio de finalizados del mismo tipo."""
    from datetime import timedelta
    ciclos: dict[str, list[float]] = {}
    for p in procesos:
        if p.get('Etapa Actual') == 'Finalizado' and p.get('Ciclo real') is not None:
            ts = p.get('Tipo Servicio', '')
            if ts and ts != 'Sin clasificar':
                ciclos.setdefault(ts, []).append(float(p['Ciclo real']))
    avgs = {ts: sum(v) / len(v) for ts, v in ciclos.items() if v}

    for p in procesos:
        p['fin_estimado'] = None
        if p.get('Etapa Actual') == 'Finalizado':
            continue
        ts  = p.get('Tipo Servicio', '')
        avg = avgs.get(ts)
        inicio = p.get('Fecha inicio real') or p.get('Fecha inicio programado')
        if avg and inicio:
            try:
                p['fin_estimado'] = (
                    datetime.fromisoformat(str(inicio)) + timedelta(days=round(avg))
                ).strftime('%Y-%m-%d')
            except Exception:
                pass


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
            "Tipo Servicio":                    _tipo_servicio(item["name"]),
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
    _add_fin_estimado(result)
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

        ciclo_prog_str = cv.get("n_meros__1", "")
        ciclo_prog_val = float(ciclo_prog_str) if ciclo_prog_str else None
        ciclo_real_val = _days_between(ini_real, fin_real)
        delta_sub = round(ciclo_real_val - ciclo_prog_val) if (ciclo_real_val is not None and ciclo_prog_val is not None) else None

        result.append({
            "Subelemento":            cv.get("etapa__1", "") or item["name"],
            "N° PROCESO":             n_proceso,
            "Fecha Inicio Acta inicio": ini_prog,
            "Fecha Fin Acta Inicio":  fin_prog,
            "Inicio real":            ini_real,
            "Fin real":               fin_real,
            "Ciclo programado":       ciclo_prog_val,
            "Ciclo real":             ciclo_real_val,
            "delta_sub":              delta_sub,
            "Área Responsable":       cv.get("color_mkxwnv9b", "") or cv.get("text_mkxwnv9b", "") or cv.get("dropdown_mkxwnv9b", ""),
            "Estado_Inicio-Fin":      cv.get("prueba_estado__1", "") or cv.get("estado__1", ""),
            "Avance Etapas":          cv.get("prueba_avance__1", ""),
            "Justificación":          cv.get("observaci_n__1", ""),
        })
    return result


# ── Endpoints: Procesos de Licitación (Monday) ────────────────────────────────

@router.get("/procesos/subelementos")
def get_all_subelementos():
    """Todos los subelementos con delta = Ciclo Real - Ciclo Prog."""
    raw = _fetch_board_items(MONDAY_PROCESOS_SUB_BOARD_ID)
    result = []
    for item in raw:
        cv = {c["id"]: (c.get("text") or "") for c in item["column_values"]}
        n_proceso = cv.get("n__proceso__1", "").strip()
        if not n_proceso:
            continue
        ini_prog, fin_prog = _parse_timeline(cv.get("timerange__1", ""))
        ini_real, fin_real = _parse_timeline(cv.get("real_real__1", ""))
        ciclo_prog_str = cv.get("n_meros__1", "")
        ciclo_prog = float(ciclo_prog_str) if ciclo_prog_str else None
        ciclo_real = _days_between(ini_real, fin_real)
        delta_sub = round(ciclo_real - ciclo_prog) if (ciclo_real is not None and ciclo_prog is not None) else None
        result.append({
            "Subelemento":        cv.get("etapa__1", "") or item["name"],
            "N°PROCESO":          n_proceso,
            "Fecha Inicio Prog":  ini_prog,
            "Fecha Fin Prog":     fin_prog,
            "Fecha Inicio Real":  ini_real,
            "Fecha Fin Real":     fin_real,
            "Ciclo programado":   ciclo_prog,
            "Ciclo real":         ciclo_real,
            "delta_sub":          delta_sub,
            "Estado_Inicio-Fin":  cv.get("prueba_estado__1", "") or cv.get("estado__1", ""),
            "Área Responsable":   cv.get("color_mkxwnv9b", "") or cv.get("text_mkxwnv9b", "") or cv.get("dropdown_mkxwnv9b", ""),
        })
    return result


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


@router.get("/gestion-proveedores")
def get_gestion_proveedores(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
):
    """KPIs de gestión de proveedores y control de costos operativos."""
    META_OF  = 15
    META_DEV = 0.0
    META_CC  = 0

    def _mod_in_range(m: dict) -> bool:
        """Filtra por fecha de finalización (firma P&C) o solicitud como fallback."""
        if not fecha_desde and not fecha_hasta:
            return True
        fecha = (m.get("fecha_firma_pyc") or m.get("fecha_solicitud") or "")[:7]
        if not fecha:
            return False
        if fecha_desde and fecha < fecha_desde:
            return False
        if fecha_hasta and fecha > fecha_hasta:
            return False
        return True

    # ── Base oferentes: distinct contractors en SOLPEs cerradas ──────────────
    base_oferentes = 0
    try:
        sol = load_solpe()
        cerradas = sol[sol["Grupo_Estado"] == "Cerrada"] if "Grupo_Estado" in sol.columns else sol
        base_oferentes = int(cerradas["Contrato"].dropna().nunique()) if "Contrato" in cerradas.columns else 0
    except Exception:
        pass

    # ── Datos de modificaciones (Monday) ──────────────────────────────────────
    desvio_costos = 0.0
    fuera_cc      = 0
    try:
        from app.routers.monday import _fetch_all_items
        mods = [m for m in _fetch_all_items() if _mod_in_range(m)]

        total_saldo   = sum(m.get("saldo_vigente", 0) or 0 for m in mods)
        total_impacto = sum(
            m.get("impacto_monto_clp", 0) or 0
            for m in mods
            if "aprobad" in (m.get("estado_mod") or m.get("estado_solicitud") or "").lower()
        )
        if total_saldo > 0:
            desvio_costos = round(total_impacto / total_saldo * 100, 1)

        # Fuera de CC: razón de modificación no es coyuntura, plan de perforación ni PAI
        _razones_cc = ("coyuntura", "perforaci", "pai")
        def _es_cc(razon: str) -> bool:
            r = razon.lower()
            return any(k in r for k in _razones_cc)
        fuera_cc = sum(1 for m in mods if not _es_cc(m.get("razon") or ""))
    except Exception:
        pass

    # ── Estado de sección ─────────────────────────────────────────────────────
    ok_of  = base_oferentes >= META_OF
    ok_dev = desvio_costos  <= META_DEV
    ok_cc  = fuera_cc       <= META_CC

    if ok_of and ok_dev and ok_cc:
        estado = "ok"
    elif fuera_cc > 5 or desvio_costos > 10.0 or base_oferentes < 5:
        estado = "critico"
    else:
        estado = "proximo"

    return {
        "base_oferentes":          base_oferentes,
        "meta_oferentes":          META_OF,
        "desvio_costos_pct":       desvio_costos,
        "meta_desvio_pct":         META_DEV,
        "modificaciones_fuera_cc": fuera_cc,
        "meta_fuera_cc":           META_CC,
        "estado_seccion":          estado,
    }


@router.get("/adjudicacion-kpis")
def get_adjudicacion_kpis(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
):
    """KPIs de cumplimiento de fecha de adjudicación para el Dashboard DAB."""
    procesos = _load_procesos()
    if not procesos:
        return {
            "total": 0, "finalizados": 0, "a_tiempo": 0,
            "pct_a_tiempo": 0, "desviacion_promedio": None,
            "criticos_pendientes": 0, "activos": 0, "meta_pct": 90,
        }

    def _proc_in_range(p: dict) -> bool:
        if not fecha_desde and not fecha_hasta:
            return True
        fecha = (p.get("Fecha fin real") or "")[:7]
        if not fecha:
            return False
        if fecha_desde and fecha < fecha_desde:
            return False
        if fecha_hasta and fecha > fecha_hasta:
            return False
        return True

    todos_finalizados = [p for p in procesos if p.get("Etapa Actual") == "Finalizado"]
    finalizados = [p for p in todos_finalizados if _proc_in_range(p)]
    a_tiempo    = [p for p in finalizados if (p.get("delta_dias") or 1) <= 0]

    # Activos/críticos: siempre en tiempo real (no se filtran por fecha)
    activos   = [p for p in procesos  if p.get("Etapa Actual") != "Finalizado"]
    atrasados = [p for p in activos   if p.get("Avance Gral Proceso") == "ATRASADO"]

    deltas    = [p["delta_dias"] for p in finalizados if p.get("delta_dias") is not None]
    desv_prom = round(sum(deltas) / len(deltas)) if deltas else None

    pct = round(len(a_tiempo) / len(finalizados) * 100) if finalizados else 0

    return {
        "total":               len(procesos),
        "finalizados":         len(finalizados),
        "a_tiempo":            len(a_tiempo),
        "pct_a_tiempo":        pct,
        "desviacion_promedio": desv_prom,
        "criticos_pendientes": len(atrasados),
        "activos":             len(activos),
        "meta_pct":            90,
    }


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
    import unicodedata
    from app.data.sql_server import query_df
    from fastapi import HTTPException as _HTTPException

    try:
        ep = query_df("SELECT * FROM dbo.SIC_EPs")
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=str(exc))

    ep.columns = [c.strip() for c in ep.columns]

    # ── Normalización de nombres de columna ───────────────────────────────────
    def _norm(s: str) -> str:
        nfkd = unicodedata.normalize("NFD", s.lower())
        return "".join(c for c in nfkd if unicodedata.category(c) != "Mn").strip()

    def _find(keywords: list, exclude: list | None = None) -> str | None:
        for c in ep.columns:
            cn = _norm(c)
            if all(k in cn for k in keywords):
                if exclude and any(k in cn for k in exclude):
                    continue
                return c
        return None

    hes_col  = _find(["hes", "fecha"])
    fac_col  = _find(["factura", "fecha"], exclude=["pago"])
    pago_col = _find(["fecha", "pago"]) or _find(["factura", "pago"])

    # ── Parseo de fechas ──────────────────────────────────────────────────────
    for col in [c for c in [hes_col, fac_col, pago_col] if c]:
        ep[col] = pd.to_datetime(ep[col], errors="coerce")

    # ── Días por etapa ────────────────────────────────────────────────────────
    ep["dias_etapa1"] = pd.NA   # HES Fecha → Factura Fecha
    ep["dias_etapa2"] = pd.NA   # Factura Fecha → Factura Fecha de Pago
    ep["dias_total"]  = pd.NA   # HES Fecha → Factura Fecha de Pago

    if hes_col and fac_col:
        m = ep[hes_col].notna() & ep[fac_col].notna()
        ep.loc[m, "dias_etapa1"] = (ep.loc[m, fac_col] - ep.loc[m, hes_col]).dt.days

    if fac_col and pago_col:
        m = ep[fac_col].notna() & ep[pago_col].notna()
        ep.loc[m, "dias_etapa2"] = (ep.loc[m, pago_col] - ep.loc[m, fac_col]).dt.days

    if hes_col and pago_col:
        m = ep[hes_col].notna() & ep[pago_col].notna()
        ep.loc[m, "dias_total"] = (ep.loc[m, pago_col] - ep.loc[m, hes_col]).dt.days

    # ── Estado derivado ───────────────────────────────────────────────────────
    def _estado(row) -> str:
        if pago_col and pd.notna(row.get(pago_col)):  return "Pagado"
        if fac_col  and pd.notna(row.get(fac_col)):   return "Por pagar"
        if hes_col  and pd.notna(row.get(hes_col)):   return "Facturando"
        return "Pendiente"

    ep["Grupo_Estado"] = ep.apply(_estado, axis=1)

    # ── Helpers estadísticos ──────────────────────────────────────────────────
    def _mean(col: str) -> float | None:
        s = ep[col].dropna()
        return round(float(s.mean()), 1) if len(s) else None

    def _med(col: str) -> float | None:
        s = ep[col].dropna()
        return round(float(s.median()), 1) if len(s) else None

    def _row_stats(r) -> dict:
        def v(k): return round(float(r[k]), 1) if k in r.index and pd.notna(r.get(k)) else None
        return {"prom_etapa1": v("dias_etapa1"), "prom_etapa2": v("dias_etapa2"), "prom_total": v("dias_total")}

    # ── KPIs ──────────────────────────────────────────────────────────────────
    kpis = {
        "total":         int(len(ep)),
        "completadas":   int(ep["dias_total"].notna().sum()),
        "prom_total":    _mean("dias_total"),
        "mediana_total": _med("dias_total"),
        "prom_etapa1":   _mean("dias_etapa1"),
        "prom_etapa2":   _mean("dias_etapa2"),
    }

    # ── Por mes (período basado en HES Fecha) ─────────────────────────────────
    por_mes: list = []
    if hes_col:
        ep["_mes"] = ep[hes_col].dt.to_period("M")
        grp = (
            ep.dropna(subset=["_mes"])
              .groupby("_mes")
              .agg(
                  cantidad=("Grupo_Estado", "count"),
                  dias_etapa1=("dias_etapa1", "mean"),
                  dias_etapa2=("dias_etapa2", "mean"),
                  dias_total=("dias_total", "mean"),
              )
              .reset_index()
              .sort_values("_mes")
        )
        for _, r in grp.iterrows():
            por_mes.append({"mes": str(r["_mes"]), "cantidad": int(r["cantidad"]), **_row_stats(r)})

    # ── Por estado ────────────────────────────────────────────────────────────
    por_estado: list = []
    grp2 = (
        ep.groupby("Grupo_Estado")
          .agg(
              cantidad=("Grupo_Estado", "count"),
              dias_etapa1=("dias_etapa1", "mean"),
              dias_etapa2=("dias_etapa2", "mean"),
              dias_total=("dias_total", "mean"),
          )
          .reset_index()
    )
    for _, r in grp2.iterrows():
        por_estado.append({"estado": r["Grupo_Estado"], "cantidad": int(r["cantidad"]), **_row_stats(r)})

    # ── Tabla ─────────────────────────────────────────────────────────────────
    rename: dict = {}
    if hes_col:  rename[hes_col]  = "fecha_hes"
    if fac_col:  rename[fac_col]  = "fecha_factura"
    if pago_col: rename[pago_col] = "fecha_pago"

    def _find_word(*words) -> str | None:
        """Match sólo si TODAS las palabras aparecen como tokens completos."""
        for c in ep.columns:
            parts = set(_norm(c).replace("_", " ").split())
            if all(w in parts for w in words):
                return c
        return None

    id_col  = _find_word("ep") or _find_word("n°", "ep") or _find_word("numero", "ep") or _find_word("codigo", "ep")
    ctr_col = _find(["contrato"])
    prj_col = _find(["proyecto"])
    prv_col = _find(["proveedor"]) or _find(["vendor"])

    if id_col  and id_col  not in rename: rename[id_col]  = "Código"
    if ctr_col and ctr_col not in rename: rename[ctr_col] = "Contrato"
    if prj_col and prj_col not in rename: rename[prj_col] = "Proyecto"
    if prv_col and prv_col not in rename: rename[prv_col] = "Nombre"

    tabla = ep.rename(columns=rename).copy()

    want = ["Código", "Nombre", "Contrato", "Proyecto", "Grupo_Estado",
            "fecha_hes", "fecha_factura", "fecha_pago",
            "dias_etapa1", "dias_etapa2", "dias_total"]
    tabla = tabla[[c for c in want if c in tabla.columns]].copy()

    for c in tabla.columns:
        if pd.api.types.is_datetime64_any_dtype(tabla[c]):
            tabla[c] = tabla[c].dt.strftime("%Y-%m-%d")

    return {
        "kpis":       kpis,
        "por_mes":    por_mes,
        "por_estado": por_estado,
        "tabla":      json.loads(tabla.fillna("").to_json(orient="records")),
    }
