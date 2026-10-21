"""
Cliente Sourcing Plan — lee el Excel sincronizado via OneDrive.

Estructura del Excel:
  - Fila 0: índices numéricos (se descarta)
  - Fila 1: encabezados reales (ESTATUS, PROYECTO, ..., fechas por mes)
  - Filas 2+: datos

El resultado es formato largo: una fila por (proyecto × mes).
"""
from datetime import date
from pathlib import Path

import pandas as pd

from config_etl import (
    SP_LOCAL_PATH, SP_SHEET_NAME, SP_HEADER_ROW,
)

# Mapeo de nombres Excel → nombres SQL
_META_RENAME = {
    "estatus":               "estatus",
    "proyecto":              "proyecto",
    "id_blanco":             "id_blanco",
    "programa":              "programa",
    "tipo financiamiento":   "tipo_fin",
    "cliente":               "cliente",
    "financiamiento":        "financiamiento",
    "cantidad":              "cantidad_total",
    "tipo":                  "tipo",
    "inicio servicio":       "inicio",
    "término servicio":      "termino",
    "termino servicio":      "termino",
    "alcance del servicio":  "alcance",
    "lic":                   "lic",
    "contrato":              "contrato",
    "empresa":               "empresa",
}

# Columnas que se cargan en SQL (las demás se descartan)
_SQL_COLS = [
    "estatus", "proyecto", "id_blanco", "programa", "tipo_fin",
    "cliente", "contrato", "empresa", "lic", "tipo",
    "inicio", "termino", "alcance", "cantidad_total",
    "periodo", "cantidad_plan", "activo",
]


def _check_path() -> Path:
    p = Path(SP_LOCAL_PATH)
    if not p.exists():
        raise FileNotFoundError(
            f"Archivo no encontrado: {SP_LOCAL_PATH}\n"
            "Verificar que OneDrive esté sincronizado y SP_LOCAL_PATH sea correcto."
        )
    return p


def _is_date_col(col) -> bool:
    """True si la columna es un período mensual (Timestamp o string con fecha)."""
    if isinstance(col, pd.Timestamp):
        return True
    try:
        ts = pd.Timestamp(str(col))
        return ts.year >= 2020
    except Exception:
        return False


def fetch_sourcing_plan() -> list[dict]:
    p  = _check_path()
    df = pd.read_excel(p, sheet_name=SP_SHEET_NAME, header=SP_HEADER_ROW)

    # Separar columnas de metadatos vs columnas de meses
    meta_cols = [c for c in df.columns if not _is_date_col(c)]
    date_cols = [c for c in df.columns if _is_date_col(c)]

    # Descartar filas sin proyecto
    proyecto_col = next((c for c in meta_cols if str(c).strip().upper() == "PROYECTO"), None)
    if proyecto_col:
        df = df[df[proyecto_col].notna() & (df[proyecto_col].astype(str).str.strip() != "")]

    print(f"  {len(df):,} contratos × {len(date_cols)} meses = {len(df) * len(date_cols):,} filas")

    # Pivotar a formato largo
    df_long = df.melt(
        id_vars=meta_cols,
        value_vars=date_cols,
        var_name="_periodo_raw",
        value_name="cantidad_plan",
    )

    # Convertir columna de período a YYYY-MM
    df_long["periodo"] = pd.to_datetime(df_long["_periodo_raw"], errors="coerce").dt.strftime("%Y-%m")
    df_long = df_long[df_long["periodo"].notna()].copy()
    df_long.drop(columns=["_periodo_raw"], inplace=True)

    # Flag activo (cantidad > 0)
    df_long["activo"] = (
        pd.to_numeric(df_long["cantidad_plan"], errors="coerce").fillna(0) > 0
    ).astype(int)

    # Normalizar nombres de columnas de metadatos
    rename = {c: _META_RENAME[str(c).strip().lower()]
              for c in meta_cols if str(c).strip().lower() in _META_RENAME}
    df_long.rename(columns=rename, inplace=True)

    # Convertir fechas de inicio/termino
    for dc in ("inicio", "termino"):
        if dc in df_long.columns:
            df_long[dc] = pd.to_datetime(df_long[dc], errors="coerce").dt.date
            df_long[dc] = df_long[dc].where(df_long[dc].notna(), None)

    # Mantener solo columnas SQL conocidas que existen
    cols_presentes = [c for c in _SQL_COLS if c in df_long.columns]
    df_long = df_long[cols_presentes].copy()

    df_long["fecha_carga"] = pd.Timestamp.now().strftime("%Y-%m-%d %H:%M:%S")
    return df_long.to_dict("records")


def descubrir():
    p = _check_path()
    stat = p.stat()
    print(f"\nArchivo: {p}")
    print(f"  Tamaño    : {stat.st_size // 1024} KB")
    print(f"  Modificado: {pd.Timestamp(stat.st_mtime, unit='s').strftime('%Y-%m-%d %H:%M')}")

    xl = pd.ExcelFile(p)
    print(f"  Hojas     : {xl.sheet_names}")

    df = xl.parse(SP_SHEET_NAME, header=SP_HEADER_ROW)
    meta_cols = [c for c in df.columns if not _is_date_col(c)]
    date_cols = [c for c in df.columns if _is_date_col(c)]

    print(f"\n  Columnas de metadatos ({len(meta_cols)}): {meta_cols}")
    if date_cols:
        print(f"  Períodos   : {str(date_cols[0])[:7]}  →  {str(date_cols[-1])[:7]}  ({len(date_cols)} meses)")

    proyecto_col = next((c for c in meta_cols if str(c).strip().upper() == "PROYECTO"), None)
    n_rows = df[proyecto_col].notna().sum() if proyecto_col else len(df)
    print(f"  Contratos  : {n_rows:,}")
    print(f"  Filas SQL  : ~{n_rows * len(date_cols):,}  (formato largo: 1 fila por contrato × mes)")

    print("\n  Primeras 3 filas (metadatos):")
    print(df[meta_cols].head(3).to_string(index=False))
