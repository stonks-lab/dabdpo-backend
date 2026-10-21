import pandas as pd

df = pd.read_parquet("ruta/al/archivo.parquet")

# Columnas disponibles
print("=== COLUMNAS ===")
print(df.columns.tolist())

# Filas de CO-009/2025
co = df[df['CODIGO PROVEEDOR'].astype(str).str.contains('009/2025',
na=False)]
print(f"\n=== CO-009/2025: {len(co)} filas ===")
print(co[['CODIGO PROVEEDOR', 'PRECIO UNITARIO (CLP $)', 'PRECIO UNITARIO(US$)', 'MONEDA']].to_string())

# Columnas de fecha detectadas
date_cols = [c for c in df.columns if len(c) == 10 and c[4] == '-' and
c[7] == '-']
print(f"\n=== DATE COLS detectados: {date_cols[:6]} ===")

# Valores en Sep/Oct para ese contrato
sep = [c for c in date_cols if c.startswith('2025-09')]
oct_ = [c for c in date_cols if c.startswith('2025-10')]
if co.shape[0] > 0 and (sep or oct_):
    print("\n=== Valores Sep/Oct ===")
    print(co[['CODIGO PROVEEDOR', 'PRECIO UNITARIO (CLP $)', 'PRECIO UNITARIO (US$)', 'MONEDA'] + sep + oct_].to_string())