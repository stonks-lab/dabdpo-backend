"""Script diagnóstico — Avance_Semanal (metros real)."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__) + "/backend")

from dotenv import load_dotenv
from pathlib import Path
load_dotenv(Path(__file__).parent / "backend" / ".env")

from app.data.sql_server import query_df_bi, _normalize_col
import pandas as pd

print("=== Avance_Semanal RAW ===")
df = query_df_bi("SELECT * FROM dbo.Avance_Semanal")
df.columns = [_normalize_col(c) for c in df.columns]

print(f"Filas totales: {len(df)}")
print(f"Columnas: {list(df.columns)}\n")

# Columnas de metro
metro_cols = [c for c in df.columns if 'metro' in c]
print(f"Columnas de metros: {metro_cols}")
for mc in metro_cols:
    df[mc] = pd.to_numeric(df[mc], errors='coerce').fillna(0)
    print(f"  {mc} → suma total: {df[mc].sum():>12,.1f}")

# Columnas de fecha
fecha_cols = [c for c in df.columns if 'fecha' in c]
print(f"\nColumnas de fecha: {fecha_cols}")
for fc in fecha_cols:
    unicos = sorted(df[fc].dropna().astype(str).unique().tolist())
    print(f"  {fc}: {unicos[:10]}{'...' if len(unicos)>10 else ''}")

# Columnas de versión/carga que podrían indicar duplicados
key_cols = [c for c in df.columns if any(w in c for w in ('version', 'fecha_carga', 'ano', 'año', 'anio', 'campana', 'campaña', 'proyecto'))]
print(f"\nOtras columnas clave: {key_cols}")
for kc in key_cols:
    unicos = sorted(df[kc].dropna().astype(str).unique().tolist())
    print(f"  {kc}: {unicos[:15]}{'...' if len(unicos)>15 else ''}")

# Conteo de filas por fecha_carga si existe
if 'fecha_carga' in df.columns:
    print(f"\nFilas por fecha_carga:")
    grp = df.groupby(df['fecha_carga'].astype(str).str[:10]).size().reset_index(name='n')
    print(grp.to_string(index=False))

# Si hay fecha de medición (no carga), ver distribución
fecha_med = next((c for c in fecha_cols if c != 'fecha_carga'), None)
if fecha_med:
    df['_mes'] = pd.to_datetime(df[fecha_med], errors='coerce').dt.strftime('%Y-%m')
    print(f"\nMetros real por mes (columna '{fecha_med}', primera col metros '{metro_cols[0] if metro_cols else '?'}'):")
    mc0 = metro_cols[0] if metro_cols else None
    if mc0:
        por_mes = df.groupby('_mes')[mc0].sum().sort_index()
        for mes, val in por_mes.items():
            print(f"  {mes}: {val:>10,.1f}")
        print(f"  TOTAL: {por_mes.sum():>10,.1f}")
