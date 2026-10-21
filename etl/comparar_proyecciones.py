"""
Compara dbo.proyecciones (SQL Server) contra el parquet actual.

Uso:
  python comparar_proyecciones.py              # resumen
  python comparar_proyecciones.py --detalle    # muestra todas las diferencias
  python comparar_proyecciones.py --contrato CO-009/2025  # filtra un contrato
"""

import sys, argparse
import pandas as pd

sys.path.insert(0, '../backend')
from app.data.sql_server import _transform_pyc, query_df

PARQUET = 'd2.parquet'
TOLERANCIA_MM = 1.0  # diferencia mínima en MM CLP para considerar discrepancia


def cargar_parquet(contrato=None) -> pd.DataFrame:
    df = pd.read_parquet(PARQUET)
    result = _transform_pyc(df)
    if contrato:
        result = result[result['contrato'].astype(str).str.strip() == contrato]
    result = result[result['monto_proyectado'] > 0].copy()
    result['monto_mm'] = (result['monto_proyectado'] / 1_000_000).round(2)
    return result[['contrato', 'proyecto', 'periodo', 'monto_mm']].rename(
        columns={'monto_mm': 'parquet_mm'}
    )


def cargar_sql(contrato=None) -> pd.DataFrame:
    where = f"AND contrato = '{contrato}'" if contrato else ""
    df = query_df(f"""
        SELECT contrato, proyecto, periodo,
               ROUND(SUM(monto_proyectado) / 1000000.0, 2) AS sql_mm
        FROM dbo.proyecciones
        WHERE monto_proyectado > 0 {where}
        GROUP BY contrato, proyecto, periodo
    """)
    return df


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--detalle',   action='store_true')
    parser.add_argument('--contrato',  default=None)
    args = parser.parse_args()

    print("\nCargando parquet...")
    pq = cargar_parquet(args.contrato)
    print(f"  Parquet : {len(pq):,} filas | {pq['contrato'].nunique()} contratos | {pq['proyecto'].nunique()} proyectos")

    print("Consultando SQL Server...")
    sq = cargar_sql(args.contrato)
    print(f"  SQL     : {len(sq):,} filas | {sq['contrato'].nunique()} contratos | {sq['proyecto'].nunique()} proyectos")

    # Merge completo
    merged = pq.merge(sq, on=['contrato', 'proyecto', 'periodo'], how='outer')
    merged['parquet_mm'] = merged['parquet_mm'].fillna(0)
    merged['sql_mm']     = merged['sql_mm'].fillna(0)
    merged['diff_mm']    = (merged['parquet_mm'] - merged['sql_mm']).abs().round(2)

    solo_parquet = merged[merged['sql_mm']     == 0]
    solo_sql     = merged[merged['parquet_mm'] == 0]
    distintos    = merged[
        (merged['parquet_mm'] > 0) &
        (merged['sql_mm']     > 0) &
        (merged['diff_mm']    > TOLERANCIA_MM)
    ]
    iguales      = merged[
        (merged['parquet_mm'] > 0) &
        (merged['sql_mm']     > 0) &
        (merged['diff_mm']    <= TOLERANCIA_MM)
    ]

    print(f"\n{'='*55}")
    print(f"  Filas iguales (diff <= {TOLERANCIA_MM} MM CLP) : {len(iguales):,}")
    print(f"  Filas con diferencia de monto          : {len(distintos):,}")
    print(f"  Solo en parquet (faltan en SQL)         : {len(solo_parquet):,}")
    print(f"  Solo en SQL     (no están en parquet)   : {len(solo_sql):,}")
    print(f"{'='*55}")

    if len(distintos) == 0 and len(solo_parquet) == 0 and len(solo_sql) == 0:
        print("\n  OK — SQL Server y parquet están sincronizados.\n")
        return

    if args.detalle or len(distintos) + len(solo_parquet) + len(solo_sql) <= 30:
        if len(distintos) > 0:
            print(f"\n--- Montos distintos ({len(distintos)} filas) ---")
            print(distintos.sort_values('diff_mm', ascending=False).to_string(index=False))

        if len(solo_parquet) > 0:
            print(f"\n--- Solo en parquet ({len(solo_parquet)} filas) ---")
            print(solo_parquet[['contrato','proyecto','periodo','parquet_mm']].to_string(index=False))

        if len(solo_sql) > 0:
            print(f"\n--- Solo en SQL ({len(solo_sql)} filas) ---")
            print(solo_sql[['contrato','proyecto','periodo','sql_mm']].to_string(index=False))
    else:
        print(f"\n  Usa --detalle para ver las {len(distintos)+len(solo_parquet)+len(solo_sql)} diferencias.")

    # Resumen por contrato de las diferencias
    if len(distintos) > 0 or len(solo_parquet) > 0 or len(solo_sql) > 0:
        diffs = pd.concat([distintos, solo_parquet, solo_sql])
        resumen = diffs.groupby('contrato').agg(
            filas_diff=('diff_mm', 'count'),
            diff_total_mm=('diff_mm', 'sum')
        ).sort_values('diff_total_mm', ascending=False)
        print(f"\n--- Resumen de diferencias por contrato ---")
        resumen['diff_total_mm'] = resumen['diff_total_mm'].round(1)
        print(resumen.to_string())

    print()


if __name__ == '__main__':
    main()
