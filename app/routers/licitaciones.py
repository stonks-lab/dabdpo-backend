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
    nombre_limpio = _RE_PROCESO_PREFIX.sub('', nombre)
    name_tok      = _tokens(nombre_limpio)
    nombre_norm   = _norm_text(nombre_limpio)

    # 1. Token overlap contra sourcing plan (threshold 0.5)
    best, best_score = '', 0.0
    if tipos and name_tok:
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
    if best_score >= 0.5:
        return best

    # 2. Fallback: keyword directo en nombre → busca en sourcing plan o usa nombre canónico
    #    Cada entrada: (keyword_en_nombre, keyword_a_buscar_en_tipo, nombre_canónico_fallback)
    _KEYWORD_RULES = [
        ("sondaje",            "sondaje",      "Sondaje"),
        ("perforac",           "sondaje",      "Sondaje"),
        ("geolog",             "geolog",       "Apoyo Geológico"),
        ("servicios geol",     "geolog",       "Apoyo Geológico"),
        ("apoyo geol",         "geolog",       "Apoyo Geológico"),
        ("campamento",         "campamento",   "Campamento"),
        ("movimiento",         "movimiento",   "Movimiento de Tierra"),
        ("primeros auxilios",  "auxilios",     "Primeros Auxilios"),
        ("topograf",           "topograf",     "Topografía"),
        ("transporte",         "transporte",   "Transporte"),
        ("mantenci",           "mantenci",     "Mantención"),
        ("aseo",               "aseo",         "Aseo Industrial"),
        ("seguridad",          "seguridad",    "Seguridad"),
        ("alimentaci",         "alimentaci",   "Alimentación"),
        ("arriendo",           "arriendo",     "Arriendo"),
        ("suministro",         "suministro",   "Suministro"),
        ("informatica",        "informatica",  "Informática"),
        ("ti ",                "ti",           "Informática"),
    ]

    for kw_nombre, kw_tipo, canonical in _KEYWORD_RULES:
        if kw_nombre in nombre_norm:
            # Buscar en sourcing plan el tipo más corto que contenga la keyword
            # (evita devolver "Campamento (Incluido en sondajes)" en vez de "Sondaje")
            if tipos:
                matches = [t for t in tipos if kw_tipo in _norm_text(t)]
                if matches:
                    return min(matches, key=len)
            return canonical

    return 'Sin clasificar'


# ── Fin estimado por Tipo Servicio ────────────────────────────────────────────

def _add_fin_estimado(procesos: list[dict]) -> None:
    """Agrega fin_estimado, brecha_dias y recalcula Avance Gral Proceso para activos.

    Promedio: agrupa por (Tipo Proceso × Tipo Servicio) para distinguir licitación vs
    adjudicación directa dentro de cada tipo de servicio. Fallbacks: solo Tipo Proceso →
    solo Tipo Servicio → global.

    fin_estimado = hoy + (avg_ciclo - días_transcurridos_desde_inicio_real).
    Representa el tiempo restante estimado desde la etapa actual hasta el final.

    Clasificación propia (sobreescribe Monday):
      - hoy > fin_programado                       → ATRASADO  (ya superó el plazo)
      - fin_estimado > fin_programado               → RIESGO    (llegará tarde según estimación)
      - fin_estimado <= fin_programado              → A TIEMPO
    """
    from datetime import timedelta

    today = datetime.today().replace(hour=0, minute=0, second=0, microsecond=0)

    # Ciclos reales agrupados: clave combinada (Tipo Proceso|Tipo Servicio) + individuales
    ciclos: dict[str, list[float]] = {}
    all_ciclos: list[float] = []

    for p in procesos:
        if p.get('Etapa Actual') != 'Finalizado' or p.get('Ciclo real') is None:
            continue
        cr        = float(p['Ciclo real'])
        tipo_proc = (p.get('Tipo Proceso') or '').strip()
        tipo_serv = (p.get('Tipo Servicio') or '').strip()
        if tipo_serv in ('Sin clasificar', ''):
            tipo_serv = ''

        all_ciclos.append(cr)
        if tipo_proc and tipo_serv:
            ciclos.setdefault(f"{tipo_proc}|{tipo_serv}", []).append(cr)
        if tipo_proc:
            ciclos.setdefault(tipo_proc, []).append(cr)
        if tipo_serv:
            ciclos.setdefault(tipo_serv, []).append(cr)

    avg_ciclo  = {k: sum(v) / len(v) for k, v in ciclos.items() if v}
    avg_global = sum(all_ciclos) / len(all_ciclos) if all_ciclos else None

    def _avg(p: dict) -> float | None:
        tipo_proc = (p.get('Tipo Proceso') or '').strip()
        tipo_serv = (p.get('Tipo Servicio') or '').strip()
        if tipo_serv in ('Sin clasificar', ''):
            tipo_serv = ''
        for key in (f"{tipo_proc}|{tipo_serv}", tipo_proc, tipo_serv):
            if key and key in avg_ciclo:
                return avg_ciclo[key]
        return avg_global

    for p in procesos:
        p['fin_estimado'] = None
        p['brecha_dias']  = None
        if p.get('Etapa Actual') == 'Finalizado':
            continue

        avg      = _avg(p)
        inicio   = p.get('Fecha inicio real') or p.get('Fecha inicio programado')
        fin_prog = p.get('Fecha fin programado')

        fin_est = None
        if avg is not None and inicio:
            try:
                inicio_dt = datetime.fromisoformat(str(inicio))
                elapsed   = (today - inicio_dt).days
                fin_est   = (today + timedelta(days=round(avg - elapsed))).strftime('%Y-%m-%d')
            except Exception:
                pass

        if not fin_est:
            fin_est = fin_prog

        p['fin_estimado'] = fin_est

        brecha = None
        if fin_est and fin_prog:
            try:
                brecha = (
                    datetime.fromisoformat(fin_est) - datetime.fromisoformat(str(fin_prog))
                ).days
            except Exception:
                pass
        p['brecha_dias'] = brecha

        # Reclasificar Avance Gral Proceso basado en brecha estimada:
        #   brecha > +3 días  → ATRASADO
        #   -3 ≤ brecha ≤ +3  → RIESGO  (margen de ±3 días)
        #   brecha < -3 días  → A TIEMPO
        #   sin fin planificado o sin brecha → A TIEMPO
        if brecha is None or not fin_prog:
            p['Avance Gral Proceso'] = 'A TIEMPO'
        elif brecha > 3:
            p['Avance Gral Proceso'] = 'ATRASADO'
        elif brecha >= -3:
            p['Avance Gral Proceso'] = 'RIESGO'
        else:
            p['Avance Gral Proceso'] = 'A TIEMPO'


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


# ── Tipos de contrato críticos ────────────────────────────────────────────────

_TIPOS_CRITICOS = (
    "sondaje", "perforaci", "movimiento de tierra",
    "campamento", "primeros auxilios", "servicios geol",
)


def _load_montos_contratos() -> dict[str, float]:
    """Retorna {codigo_ctt (normalizado): monto_actualizado} desde dbo.SIC_Contratos."""
    import pandas as _pd
    import unicodedata as _uc, re as _re

    def _norm(s: str) -> str:
        nfkd = _uc.normalize("NFD", s.lower())
        return _re.sub(r'\s+', ' ', ''.join(c for c in nfkd if _uc.category(c) != 'Mn')).strip()

    try:
        from app.data.sql_server import query_df
        df = query_df("SELECT * FROM dbo.SIC_Contratos ORDER BY _fecha_carga DESC")
        df.columns = [c.strip() for c in df.columns]

        if len(df.columns) > 1:
            df.rename(columns={df.columns[1]: "codigo_ctt"}, inplace=True)

        # Detectar columna de monto: primero "monto" + modificador, luego "monto" sólo
        monto_col = None
        for priority in [
            lambda cn: 'monto' in cn and any(k in cn for k in ('actual', 'vigente', 'total', 'contrato')),
            lambda cn: cn in ('monto', 'valor', 'monto contrato', 'monto actualizado', 'valor contrato'),
            lambda cn: 'monto' in cn,
            lambda cn: 'valor' in cn,
        ]:
            for c in df.columns:
                if priority(_norm(c)):
                    monto_col = c
                    break
            if monto_col:
                break

        if not monto_col or "codigo_ctt" not in df.columns:
            return {}

        df["codigo_ctt"] = df["codigo_ctt"].astype(str).str.strip().str.upper()
        df.loc[df["codigo_ctt"].isin(["NAN", "NONE", "0", ""]), "codigo_ctt"] = ""
        df = df[df["codigo_ctt"] != ""].drop_duplicates(subset="codigo_ctt", keep="first")
        df[monto_col] = _pd.to_numeric(df[monto_col], errors="coerce").fillna(0.0)

        # Indexar con y sin prefijo "CO-" para tolerar ambos formatos
        result: dict[str, float] = {}
        for _, row in df[["codigo_ctt", monto_col]].iterrows():
            code  = row["codigo_ctt"]
            monto = float(row[monto_col])
            result[code] = monto
            if not code.startswith("CO-"):
                result["CO-" + code] = monto
            else:
                result[code[3:]] = monto
        return result
    except Exception:
        return {}


def _es_aprobado(m: dict) -> bool:
    """Modificación aprobada/finalizada (todas las firmas obtenidas)."""
    e = (m.get("estado_mod") or m.get("estado_solicitud") or "").lower()
    return "aprobad" in e or "finaliz" in e


def _fecha_aprobacion(m: dict) -> str:
    """Fecha en que la modificación quedó aprobada (la última firma registrada)."""
    fechas = [
        m.get("fecha_firma_pyc") or "",
        m.get("fecha_firma_dpo") or "",
        m.get("fecha_firma_sup") or "",
    ]
    return max((f for f in fechas if f), default="")


def _find_fecha_inicio_col(df: "pd.DataFrame") -> str | None:
    """Detecta la columna de fecha de inicio del contrato por nombre normalizado."""
    import unicodedata as _uc, re as _re
    def _norm(s: str) -> str:
        nfkd = _uc.normalize("NFD", s.lower())
        return _re.sub(r'\s+', ' ', ''.join(c for c in nfkd if _uc.category(c) != 'Mn')).strip()
    # Prioridad: "inicio vigencia" > "fecha inicio" > cualquier cosa con "inicio"
    for c in df.columns:
        cn = _norm(c)
        if 'inicio' in cn and 'vigencia' in cn:
            return c
    for c in df.columns:
        cn = _norm(c)
        if 'fecha' in cn and 'inicio' in cn:
            return c
    for c in df.columns:
        cn = _norm(c)
        if cn in ('inicio vigencia', 'inicio del contrato', 'fecha inicio', 'inicio'):
            return c
    return None


def _load_contratos_proveedor(
    solo_criticos: bool = True,
    fecha_desde: str | None = None,
    fecha_hasta: str | None = None,
) -> "pd.DataFrame":
    """Carga SIC_Contratos con las columnas relevantes para el KPI base oferentes.
    Columnas conocidas (índices 0-based): 1=Código, 2=Nombre, 3=Estado, 4=Contrato Crítico,
    13=Proveedor, 20=Área, 21=Tipo Contrato.
    Filtra por fecha de inicio del contrato cuando se especifica fecha_desde/hasta."""
    import pandas as _pd
    try:
        from app.data.sql_server import query_df
        df = query_df("SELECT * FROM dbo.SIC_Contratos ORDER BY _fecha_carga DESC")
        df.columns = [c.strip() for c in df.columns]

        # Rename por posición (tolerante si el esquema tiene columnas extra)
        pos = {1: "codigo_ctt", 2: "Descripcion_ctt", 3: "Estado_ctt",
               4: "es_critico", 20: "Area_contrato", 21: "Tipo_contrato"}
        df.rename(columns={df.columns[i]: n for i, n in pos.items() if i < len(df.columns)},
                  inplace=True)

        # Si la columna Proveedor tiene otro nombre por encoding, buscar por posición 13
        if "Proveedor" not in df.columns and len(df.columns) > 13:
            df.rename(columns={df.columns[13]: "Proveedor"}, inplace=True)

        # Detectar columna de fecha inicio antes de cualquier filtro
        fi_col = _find_fecha_inicio_col(df)

        if "codigo_ctt" in df.columns:
            df["codigo_ctt"] = df["codigo_ctt"].astype(str).str.strip()
            # astype(str) convierte NaN → "nan"; revertir a vacío para no colapsar
            # contratos de distintas empresas que simplemente no tienen código
            df.loc[df["codigo_ctt"].isin(["nan", "None", "0"]), "codigo_ctt"] = ""
            # Dedup sólo en filas con código real; preservar todas las sin código
            coded   = df[df["codigo_ctt"] != ""].drop_duplicates(subset="codigo_ctt", keep="first")
            uncoded = df[df["codigo_ctt"] == ""]
            df = _pd.concat([coded, uncoded], ignore_index=True)

        for col in ("Tipo_contrato", "Proveedor", "es_critico"):
            if col not in df.columns:
                df[col] = ""
            df[col] = df[col].fillna("").astype(str).str.strip()

        if solo_criticos:
            flag_critico = df["es_critico"].str.strip().str.lower().isin(["sí", "si", "yes", "1", "true"])
            tipo_critico = df["Tipo_contrato"].str.lower().apply(
                lambda t: any(k in t for k in _TIPOS_CRITICOS)
            )
            df = df[flag_critico | tipo_critico]

        # Filtro por fecha de inicio del contrato
        if fi_col and fi_col in df.columns and (fecha_desde or fecha_hasta):
            fi_ym = _pd.to_datetime(df[fi_col], errors="coerce").dt.strftime("%Y-%m").fillna("")
            if fecha_desde:
                df = df[fi_ym >= fecha_desde]
            if fecha_hasta:
                df = df[fi_ym <= fecha_hasta]

        keep = [c for c in ("codigo_ctt", "Descripcion_ctt", "Tipo_contrato", "es_critico", "Proveedor")
                if c in df.columns]
        if fi_col and fi_col in df.columns:
            keep.append(fi_col)
        df = df[keep].copy()

        # Normalizar fecha inicio para JSON
        if fi_col and fi_col in df.columns:
            df[fi_col] = _pd.to_datetime(df[fi_col], errors="coerce").dt.strftime("%Y-%m-%d").fillna("")
            df.rename(columns={fi_col: "fecha_inicio"}, inplace=True)

        return df
    except Exception:
        return _pd.DataFrame(columns=["codigo_ctt", "Descripcion_ctt", "Tipo_contrato", "es_critico", "Proveedor"])


@router.get("/gestion-proveedores")
def get_gestion_proveedores(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    criticos_only: bool = Query(default=True),
    tipo_contrato: Optional[str] = Query(None),
):
    """KPIs de gestión de proveedores y control de costos operativos."""
    META_OF  = 15
    META_DEV = 0.0
    META_CC  = 0

    def _in_range(ym: str) -> bool:
        if not ym:
            return not fecha_desde and not fecha_hasta
        if fecha_desde and ym < fecha_desde:
            return False
        if fecha_hasta and ym > fecha_hasta:
            return False
        return True

    def _mod_in_range(m: dict) -> bool:
        fecha = (_fecha_aprobacion(m) or m.get("fecha_solicitud") or "")[:7]
        return _in_range(fecha)

    # ── Base oferentes: empresas distintas en SIC_Contratos ──────────────────
    base_oferentes   = 0
    tipos_disponibles: list[str] = []
    try:
        df_all = _load_contratos_proveedor(
            solo_criticos=criticos_only,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        tipos_disponibles = sorted(
            t for t in df_all["Tipo_contrato"].replace("", None).dropna().unique().tolist() if t
        )
        df_ctts = df_all[df_all["Tipo_contrato"] == tipo_contrato] if tipo_contrato else df_all
        base_oferentes = int(df_ctts["Proveedor"].str.strip().replace("", None).dropna().nunique())
    except Exception:
        pass

    # ── Datos de modificaciones (Monday) ──────────────────────────────────────
    desvio_costos = 0.0
    fuera_cc      = 0
    try:
        from app.routers.monday import _fetch_all_items
        mods = [m for m in _fetch_all_items() if _mod_in_range(m)]

        montos_ctt    = _load_montos_contratos()
        mods_aprobados = [m for m in mods if _es_aprobado(m)]

        def _monto_ctt(m: dict) -> float:
            codigo = (m.get("codigo_contrato") or "").strip().upper()
            return montos_ctt.get(codigo, 0.0)

        total_monto   = sum(_monto_ctt(m) for m in mods_aprobados)
        total_impacto = sum(m.get("impacto_monto_clp", 0) or 0 for m in mods_aprobados)
        if total_monto > 0:
            desvio_costos = round(total_impacto / total_monto * 100, 2)

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
        "criticos_only":           criticos_only,
        "tipos_disponibles":       tipos_disponibles,
    }


def _es_critico_proceso(p: dict) -> bool:
    """Proceso es crítico si su Tipo Servicio o nombre contiene alguna keyword crítica."""
    ts = (p.get("Tipo Servicio") or "").lower()
    nm = (p.get("Proceso") or "").lower()
    return any(k in ts or k in nm for k in _TIPOS_CRITICOS)


@router.get("/adjudicacion-kpis")
def get_adjudicacion_kpis(
    fecha_desde:   Optional[str] = Query(None),
    fecha_hasta:   Optional[str] = Query(None),
    criticos_only: int           = Query(0),
    tipo_proceso:  str           = Query(""),
):
    """KPIs de cumplimiento de fecha de adjudicación para el Dashboard DAB."""
    procesos = _load_procesos()
    if not procesos:
        return {
            "total": 0, "finalizados": 0, "a_tiempo": 0,
            "pct_a_tiempo": 0, "desviacion_promedio": None,
            "criticos_pendientes": 0, "riesgo_pendientes": 0,
            "activos_a_tiempo": 0, "activos": 0, "meta_pct": 90,
            "tipos_disponibles": [],
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

    # Activos: en tiempo real; tipos disponibles antes de filtrar
    todos_activos     = [p for p in procesos if p.get("Etapa Actual") != "Finalizado"]
    tipos_disponibles = sorted({p.get("Tipo Proceso") or "" for p in todos_activos if p.get("Tipo Proceso")})

    activos = todos_activos
    if criticos_only:
        activos = [p for p in activos if _es_critico_proceso(p)]
    if tipo_proceso:
        activos = [p for p in activos if tipo_proceso.lower() in (p.get("Tipo Proceso") or "").lower()]

    atrasados    = [p for p in activos if p.get("Avance Gral Proceso") == "ATRASADO"]
    riesgo_pend  = [p for p in activos if p.get("Avance Gral Proceso") == "RIESGO"]
    a_tiempo_act = [p for p in activos if p.get("Avance Gral Proceso") not in ("ATRASADO", "RIESGO")]

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
        "riesgo_pendientes":   len(riesgo_pend),
        "activos_a_tiempo":    len(a_tiempo_act),
        "activos":             len(activos),
        "meta_pct":            90,
        "tipos_disponibles":   tipos_disponibles,
    }


@router.get("/adjudicacion-detalle")
def get_adjudicacion_detalle(
    fecha_desde:   Optional[str] = Query(None),
    fecha_hasta:   Optional[str] = Query(None),
    criticos_only: int           = Query(0),
    tipo_proceso:  str           = Query(""),
):
    procesos = _load_procesos()

    def _in_range(p: dict) -> bool:
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

    CAMPOS = ["N°PROCESO", "Proceso", "Tipo Proceso", "Tipo Servicio", "Etapa Actual", "Avance Gral Proceso",
              "Fecha fin programado", "Fecha fin real", "delta_dias",
              "fin_estimado", "brecha_dias", "Responsable"]

    def _pick(lst: list) -> list:
        return [{k: p.get(k) for k in CAMPOS} for p in lst]

    activos = [p for p in procesos if p.get("Etapa Actual") != "Finalizado"]
    if criticos_only:
        activos = [p for p in activos if _es_critico_proceso(p)]
    if tipo_proceso:
        activos = [p for p in activos if tipo_proceso.lower() in (p.get("Tipo Proceso") or "").lower()]

    criticos     = [p for p in activos if p.get("Avance Gral Proceso") == "ATRASADO"]
    riesgo       = [p for p in activos if p.get("Avance Gral Proceso") == "RIESGO"]
    a_tiempo_act = [p for p in activos if p.get("Avance Gral Proceso") not in ("ATRASADO", "RIESGO")]

    finalizados = [p for p in procesos if p.get("Etapa Actual") == "Finalizado" and _in_range(p)]
    a_tiempo    = [p for p in finalizados if (p.get("delta_dias") or 1) <= 0]
    tarde       = sorted(
        [p for p in finalizados if (p.get("delta_dias") or 0) > 0],
        key=lambda p: p.get("delta_dias") or 0, reverse=True,
    )

    return {
        "criticos":        _pick(criticos),
        "riesgo":          _pick(riesgo),
        "activos_a_tiempo": _pick(a_tiempo_act),
        "a_tiempo":        _pick(a_tiempo),
        "tarde":           _pick(tarde),
    }


@router.get("/gestion-proveedores-detalle")
def get_gestion_proveedores_detalle(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    criticos_only: bool = Query(default=True),
    tipo_contrato: Optional[str] = Query(None),
):
    """Detalle de procesos detrás de cada KPI de gestión de proveedores."""
    def _in_range(ym: str) -> bool:
        if not ym:
            return not fecha_desde and not fecha_hasta
        if fecha_desde and ym < fecha_desde:
            return False
        if fecha_hasta and ym > fecha_hasta:
            return False
        return True

    def _mod_in_range(m: dict) -> bool:
        fecha = (_fecha_aprobacion(m) or m.get("fecha_solicitud") or "")[:7]
        return _in_range(fecha)

    # ── Contratos adjudicados (base oferentes) ────────────────────────────────
    contratos: list = []
    try:
        df_ctts = _load_contratos_proveedor(
            solo_criticos=criticos_only,
            fecha_desde=fecha_desde,
            fecha_hasta=fecha_hasta,
        )
        if tipo_contrato:
            df_ctts = df_ctts[df_ctts["Tipo_contrato"] == tipo_contrato]
        df_ctts = df_ctts[df_ctts["Proveedor"].replace("", None).notna()].copy()
        contratos = (
            df_ctts
            .sort_values("Proveedor")
            .fillna("")
            .to_dict("records")
        )
    except Exception:
        pass

    modificaciones_desvio: list = []
    modificaciones_fuera_cc: list = []
    try:
        from app.routers.monday import _fetch_all_items
        montos_ctt = _load_montos_contratos()
        mods = [m for m in _fetch_all_items() if _mod_in_range(m)]
        _razones_cc = ("coyuntura", "perforaci", "pai")

        def _es_cc(razon: str) -> bool:
            r = (razon or "").lower()
            return any(k in r for k in _razones_cc)

        for m in mods:
            if _es_aprobado(m):
                codigo = (m.get("codigo_contrato") or "").strip().upper()
                monto  = montos_ctt.get(codigo)
                modificaciones_desvio.append({
                    "nombre":             m.get("nombre", ""),
                    "codigo_contrato":    m.get("codigo_contrato", ""),
                    "proyecto":           m.get("proyecto", ""),
                    "monto_actualizado":  monto,
                    "impacto_monto_clp":  m.get("impacto_monto_clp"),
                    "estado":             m.get("estado_mod") or m.get("estado_solicitud") or "",
                    "fecha_aprobacion":   _fecha_aprobacion(m),
                    "razon":              m.get("razon", ""),
                })

        for m in mods:
            if not _es_cc(m.get("razon") or ""):
                modificaciones_fuera_cc.append({
                    "nombre":            m.get("nombre", ""),
                    "codigo_contrato":   m.get("codigo_contrato", ""),
                    "proyecto":          m.get("proyecto", ""),
                    "razon":             m.get("razon", ""),
                    "descripcion":       m.get("descripcion", ""),
                    "tipo_modificacion": m.get("tipo_modificacion", ""),
                    "estado":            m.get("estado_mod") or m.get("estado_solicitud") or "",
                    "fecha_firma_pyc":   m.get("fecha_firma_pyc", ""),
                    "impacto_monto_clp": m.get("impacto_monto_clp"),
                })
    except Exception:
        pass

    return {
        "contratos":               contratos,
        "modificaciones_desvio":   modificaciones_desvio,
        "modificaciones_fuera_cc": modificaciones_fuera_cc,
    }


# ── Endpoints: Pipeline SOLPE ─────────────────────────────────────────────────

@router.get("/debug-contratos-cols")
def get_debug_contratos_cols():
    """Devuelve columnas, filas de SIC_Contratos y diagnóstico de _load_montos_contratos."""
    try:
        from app.data.sql_server import query_df
        import unicodedata as _uc, re as _re
        import pandas as _pd

        df = query_df("SELECT TOP 3 * FROM dbo.SIC_Contratos ORDER BY _fecha_carga DESC")
        df.columns = [c.strip() for c in df.columns]
        filas = []
        for _, r in df.iterrows():
            filas.append({c: str(v) for c, v in r.items()})

        def _norm(s: str) -> str:
            nfkd = _uc.normalize("NFD", s.lower())
            return _re.sub(r'\s+', ' ', ''.join(c for c in nfkd if _uc.category(c) != 'Mn')).strip()

        monto_col = None
        for priority in [
            lambda cn: 'monto' in cn and any(k in cn for k in ('actual', 'vigente', 'total', 'contrato')),
            lambda cn: cn in ('monto', 'valor', 'monto contrato', 'monto actualizado', 'valor contrato'),
            lambda cn: 'monto' in cn,
            lambda cn: 'valor' in cn,
        ]:
            for c in df.columns:
                if priority(_norm(c)):
                    monto_col = c
                    break
            if monto_col:
                break

        montos = _load_montos_contratos()
        sample_keys = list(montos.keys())[:10]

        from app.routers.monday import _fetch_all_items
        mods = _fetch_all_items()
        sample_codigos_monday = list({m.get("codigo_contrato", "") for m in mods if m.get("codigo_contrato")})[:10]

        return {
            "columnas": list(df.columns),
            "filas": filas,
            "monto_col_detectada": monto_col,
            "montos_count": len(montos),
            "montos_sample_keys": sample_keys,
            "monday_codigos_sample": sample_codigos_monday,
        }
    except Exception as e:
        import traceback
        return {"error": str(e), "traceback": traceback.format_exc()}


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


@router.get("/tiempos/columnas")
def get_tiempos_columnas():
    """Devuelve los nombres de columna crudos de dbo.SIC_EPs para diagnóstico."""
    from app.data.sql_server import query_df
    from fastapi import HTTPException as _HTTPException
    try:
        ep = query_df("SELECT TOP 1 * FROM dbo.SIC_EPs")
        ep.columns = [c.strip() for c in ep.columns]
        return {"columnas": list(ep.columns), "primera_fila": ep.iloc[0].fillna("").to_dict()}
    except Exception as exc:
        raise _HTTPException(status_code=502, detail=str(exc))


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

    # Mapeo directo con los nombres exactos de dbo.SIC_EPs; fallback genérico si cambian
    cols = set(ep.columns)
    id_col  = "Número"          if "Número"          in cols else _find_word("numero") or _find(["n_ep"])
    ctr_col = "Contrato Código" if "Contrato Código" in cols else _find(["codigo", "contrato"]) or _find(["contrato"])
    prj_col = _find(["proyecto"])
    prv_col = "Proveedor"       if "Proveedor"       in cols else _find(["nombre", "proveedor"]) or _find(["proveedor"])

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
