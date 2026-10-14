import io
import os
import pandas as pd
from fastapi import APIRouter, UploadFile, File, HTTPException
from datetime import datetime

router = APIRouter(prefix="/upload", tags=["upload"])


@router.post("/proyecciones-pyc")
async def upload_proyecciones_pyc(file: UploadFile = File(...), usuario: str = ""):
    if not file.filename.endswith('.parquet'):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .parquet")

    content = await file.read()
    try:
        df = pd.read_parquet(io.BytesIO(content))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"No se pudo leer el parquet: {e}")

    try:
        from app.data.sql_server import load_proyecciones_pyc, log_carga
        result = load_proyecciones_pyc(df)
        log_carga("dbo.proyecciones", "Plataforma", file.filename, usuario or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar en SQL Server: {e}")

    return result


@router.post("/dimensiones")
async def upload_dimensiones(file: UploadFile = File(...), usuario: str = ""):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx o .xls")

    content = await file.read()
    try:
        from app.data.sql_server import load_dimensiones, log_carga
        result = load_dimensiones(content)
        for tabla in result.get("tablas", {}).keys():
            log_carga(f"dbo.{tabla}", "Plataforma", file.filename, usuario or None)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar en SQL Server: {e}")

    return result


@router.post("/incidencias")
async def upload_incidencias(file: UploadFile = File(...), usuario: str = ""):
    if not (file.filename.endswith('.xlsx') or file.filename.endswith('.xls')):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx o .xls")

    content = await file.read()
    try:
        from app.data.sql_server import load_incidencias, log_carga
        result = load_incidencias(content)
        log_carga("dbo.registro_incidencias", "Plataforma", file.filename, usuario or None)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar en SQL Server: {e}")

    return result


# ── Costo Metro ───────────────────────────────────────────────────────────────

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}

_COL_CMP  = 3
_COL_REAL = 6
_COL_POND = 8
_COL_CMA  = 13
_COL_DESV = 14


def _parse_costo_metro(content: bytes, filename: str) -> list[dict]:
    """
    Parsea el Excel de Costo Metro y retorna una lista de dicts:
    uno por campaña (columna con 'camp' en cabecera, si no col 0) y uno TOTAL.
    Las columnas clave se buscan por nombre de encabezado para tolerar
    variaciones en el número de columnas entre archivos.
    """
    name_lower = filename.lower()
    mes_nombre = next((m for m in _MESES if m in name_lower), None)
    if not mes_nombre:
        raise ValueError(
            f"No se pudo detectar el mes en el nombre '{filename}'. "
            "Asegúrate de que el archivo se llame 'Costo Metro - <Mes>.xlsx'."
        )
    mes  = _MESES[mes_nombre]
    anio = datetime.now().year

    wb = pd.ExcelFile(io.BytesIO(content))
    if "Export" not in wb.sheet_names:
        raise ValueError(f"Hoja 'Export' no encontrada. Hojas disponibles: {wb.sheet_names}")

    df = pd.read_excel(io.BytesIO(content), sheet_name="Export", header=1)
    df = df.dropna(how="all")

    if len(df.columns) < 4:
        raise ValueError(f"La hoja Export tiene solo {len(df.columns)} columnas.")

    import unicodedata as _uc
    def _norm(s: str) -> str:
        return _uc.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode('ascii').lower()

    def _find_col(must: list[str], exclude: list[str] = (), default: int = 0) -> int:
        """Retorna el índice de la primera columna cuyo nombre contiene todas las
        palabras de *must* y ninguna de *exclude*. Si no hay coincidencia, retorna
        *default* (clampeado al rango válido)."""
        for i, col in enumerate(df.columns):
            cn = _norm(str(col))
            if all(kw in cn for kw in must) and not any(ex in cn for ex in exclude):
                return i
        return min(default, len(df.columns) - 1)

    camp_col_idx  = _find_col(['camp'],                       default=0)
    col_cmp       = _find_col(['costo', 'program'],            default=_COL_CMP)
    col_pond      = _find_col(['ponderador'],                  default=_COL_POND)
    col_cma       = _find_col(['ajustado'], exclude=['desv'],  default=_COL_CMA)
    col_desv      = _find_col(['desv', 'ajust'],               default=_COL_DESV)
    # Ponderador Perforación Real — columna específica para brecha ajustada
    col_pond_real = _find_col(['ponderador', 'real'],          default=col_pond)

    pond_series      = pd.to_numeric(df.iloc[:, col_pond],      errors="coerce").fillna(0)
    pond_real_series = pd.to_numeric(df.iloc[:, col_pond_real], errors="coerce").fillna(0)

    def _wavg(col_idx: int, row_mask) -> float:
        """Promedio ponderado — equivale al valor directo si hay una sola fila por campaña."""
        vals  = pd.to_numeric(df.iloc[:, col_idx], errors="coerce").fillna(0)
        total = float(pond_series[row_mask].sum())
        if total == 0:
            return float(vals[row_mask].iloc[0]) if row_mask.any() else 0.0
        return float((vals[row_mask] * pond_series[row_mask]).sum() / total)

    def _wsum(col_idx: int, row_mask) -> float:
        """Suma ponderada — para el TOTAL."""
        vals = pd.to_numeric(df.iloc[:, col_idx], errors="coerce").fillna(0)
        return float((vals[row_mask] * pond_series[row_mask]).sum())

    camp_series = df.iloc[:, camp_col_idx].fillna('').astype(str).str.strip()
    _CAMP_EXCLUIR = {'nan', '', 'total', 'totales', 'total general', 'grand total', 'suma', 'resumen'}
    unique_camps = [
        c for c in camp_series[pond_series > 0].unique()
        if _norm(c) not in _CAMP_EXCLUIR and not _norm(c).startswith('total')
    ]

    results: list[dict] = []
    for camp in unique_camps:
        mask = (camp_series == camp) & (pond_series > 0)
        results.append(dict(
            anio=anio, mes=mes, mes_nombre=mes_nombre.capitalize(),
            campana=camp,
            programado=_wavg(col_cmp,  mask),
            real      =_wavg(col_cma,  mask),
            ajustado  =_wavg(col_cma,  mask),
            brecha    =_wavg(col_desv, mask),
        ))

    # TOTAL: solo filas de campañas válidas (excluye filas de totales del Excel con camp=NaN)
    total_mask = camp_series.isin(unique_camps) & (pond_series > 0)
    desv_vals  = pd.to_numeric(df.iloc[:, col_desv], errors='coerce').fillna(0)
    brecha_total = float((desv_vals[total_mask] * pond_real_series[total_mask]).sum())
    results.append(dict(
        anio=anio, mes=mes, mes_nombre=mes_nombre.capitalize(),
        campana='TOTAL',
        programado=_wsum(col_cmp,  total_mask),
        real      =_wsum(col_cma,  total_mask),
        ajustado  =_wsum(col_cma,  total_mask),
        brecha    =brecha_total,
    ))

    return results


@router.post("/costo-metro")
async def upload_costo_metro(file: UploadFile = File(...), usuario: str = ""):
    if not (file.filename.endswith(".xlsx") or file.filename.endswith(".xls")):
        raise HTTPException(status_code=400, detail="Solo se aceptan archivos .xlsx o .xls")

    content = await file.read()
    try:
        parsed_list = _parse_costo_metro(content, file.filename)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Error al leer el archivo: {e}")

    try:
        from app.data.sql_server import upsert_costo_metro, log_carga
        results = [upsert_costo_metro(**p) for p in parsed_list]
        log_carga("dbo.CostoMetro", "Plataforma", file.filename, usuario or None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al guardar en SQL Server: {e}")

    total = next((r for r in results if r.get('campana') == 'TOTAL'), results[-1] if results else {})
    total['campanas_cargadas'] = len([r for r in results if r.get('campana') != 'TOTAL'])
    return total


# ── Registro de cargas ────────────────────────────────────────────────────────

@router.get("/registro")
def get_registro():
    from app.data.sql_server import get_registro_cargas
    return get_registro_cargas()


@router.get("/fuentes-externas")
def get_fuentes_externas():
    from app.config import EXCEL_FILES

    sic = [
        ("ep_detalle", "EP Detalle", "Saldo · Análisis · Alertas · Home", "No aplica (Excel en caché)"),
        ("itemizado",  "Itemizado",  "Proyecciones P&C",                   "No aplica (Excel en caché)"),
        ("solpe",      "SOLPE",      "Licitaciones · Sourcing Plan",        "No aplica (Excel en caché)"),
        ("boletas",    "Boletas",    "Alertas",                             "No aplica (Excel en caché)"),
    ]
    result = []
    for key, tabla, modulos, tabla_sql in sic:
        fp    = EXCEL_FILES.get(key)
        fecha = None
        if fp and fp.exists():
            fecha = datetime.fromtimestamp(fp.stat().st_mtime).strftime('%Y-%m-%d %H:%M:%S')
        result.append({
            "tabla":          tabla,
            "tabla_sql":      tabla_sql,
            "modulos":        modulos,
            "fuente":         "API SIC",
            "nombre_archivo": fp.name if fp else None,
            "fecha_carga":    fecha,
        })

    for tabla, modulos, board_ref in [
        ("Modificaciones de Contratos", "Modificaciones",   "ENV: MONDAY_BOARD_ID"),
        ("Licitaciones",                "Licitaciones",     "ENV: MONDAY_BOARD_LICITACIONES"),
    ]:
        result.append({
            "tabla":          tabla,
            "tabla_sql":      "No aplica (API en vivo)",
            "modulos":        modulos,
            "fuente":         "Monday API",
            "nombre_archivo": f"monday.com · {board_ref}",
            "fecha_carga":    None,
        })

    return result
