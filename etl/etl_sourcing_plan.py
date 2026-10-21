"""
ETL: Excel OneDrive (Sourcing Plan) → SQL Server dbo.sourcing_plan

Uso:
  python etl_sourcing_plan.py              # extrae y carga
  python etl_sourcing_plan.py --discover   # muestra columnas del Excel
  python etl_sourcing_plan.py --dry-run    # extrae sin escribir en SQL
"""

import sys, argparse
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="ETL Sourcing Plan -> SQL Server")
    parser.add_argument("--discover", action="store_true")
    parser.add_argument("--dry-run",  action="store_true")
    args = parser.parse_args()

    inicio = datetime.now()
    print(f"\n{'='*55}")
    print(f"ETL Sourcing Plan (OneDrive)  {inicio.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    if args.discover:
        from sharepoint_client import descubrir
        descubrir()
        return

    # ── Extracción ──────────────────────────────────────────────────────────────
    from config_etl import SP_LOCAL_PATH
    print(f"\n[1/2] Leyendo Excel desde OneDrive...")
    from sharepoint_client import fetch_sourcing_plan
    rows = fetch_sourcing_plan()

    if not rows:
        print("\nSin datos. Verificar SP_EXCEL_NAME y SP_SHEET_NAME en config_etl.py")
        sys.exit(1)

    print(f"\n  Preview (2 filas):")
    for r in rows[:2]:
        print(f"  {r}")

    if args.dry_run:
        print(f"\n[Dry Run] {len(rows):,} filas listas. NO se carga en SQL.")
        return

    # ── Carga ───────────────────────────────────────────────────────────────────
    print(f"\n[2/2] Cargando en SQL Server...")
    from sp_sql_loader import cargar_sourcing_plan
    cargar_sourcing_plan(rows)

    seg = (datetime.now() - inicio).seconds
    print(f"\nETL completado en {seg}s")


if __name__ == "__main__":
    main()
