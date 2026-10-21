"""
ETL: Power BI Service (P&C) → SQL Server dbo.proyecciones

No requiere registrar ninguna app en Azure AD.
Usa el Client ID público de Power BI Desktop.

Uso:
  python etl_proyecciones.py              # extrae y carga
  python etl_proyecciones.py --discover   # lista workspaces/datasets/tablas
  python etl_proyecciones.py --dry-run    # extrae sin escribir en SQL
  python etl_proyecciones.py --sql-only   # solo crea tabla en SQL Server
  python etl_proyecciones.py --clear-token  # borra caché de login
"""

import sys, argparse, os
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="ETL Proyecciones P&C -> SQL Server")
    parser.add_argument("--discover",     action="store_true")
    parser.add_argument("--dry-run",      action="store_true")
    parser.add_argument("--sql-only",     action="store_true")
    parser.add_argument("--clear-token",  action="store_true")
    args = parser.parse_args()

    inicio = datetime.now()
    print(f"\n{'='*55}")
    print(f"ETL Proyecciones P&C  {inicio.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*55}")

    # Borrar caché de token
    if args.clear_token:
        from config_etl import TOKEN_CACHE_FILE
        if os.path.exists(TOKEN_CACHE_FILE):
            os.remove(TOKEN_CACHE_FILE)
            print("Cache de token borrado. Proxima ejecucion pedira login.")
        else:
            print("No habia cache de token.")
        return

    # Solo verificar SQL Server
    if args.sql_only:
        from sql_loader import get_connection, ensure_table
        conn = get_connection()
        print("Conexion SQL Server OK")
        ensure_table(conn)
        conn.close()
        return

    # Descubrimiento de workspaces/datasets
    if args.discover:
        from pbi_client import descubrir
        descubrir()
        return

    # Verificar configuración mínima
    from config_etl import PBI_WORKSPACE_ID, PBI_DATASET_ID, LOAD_MODE
    if "TU_" in PBI_WORKSPACE_ID or "TU_" in PBI_DATASET_ID:
        print("\n  CONFIGURACION INCOMPLETA en config_etl.py:")
        print("  Falta: PBI_WORKSPACE_ID y/o PBI_DATASET_ID")
        print("\n  Paso 1: completar usuario/password en config_etl.py")
        print("  Paso 2: ejecutar:  python etl_proyecciones.py --discover")
        print("  Paso 3: copiar los IDs y volver a ejecutar sin --discover")
        sys.exit(1)

    # ── Extraccion ────────────────────────────────────────────────────────────
    print(f"\n[1/2] Extrayendo de Power BI Service (modo: {LOAD_MODE})...")
    from pbi_client import fetch_proyecciones
    rows = fetch_proyecciones()

    if not rows:
        print("\nSin datos. Verificar PBI_TABLE_NAME en config_etl.py")
        print("Ejecutar --discover para ver las tablas disponibles.")
        sys.exit(1)

    print(f"\n  Preview:")
    for r in rows[:2]:
        print(f"  {r}")

    if args.dry_run:
        print(f"\n[Dry Run] {len(rows):,} filas listas. NO se carga en SQL.")
        return

    # ── Carga en SQL Server ───────────────────────────────────────────────────
    print(f"\n[2/2] Cargando en SQL Server...")
    from sql_loader import cargar_en_sql
    cargar_en_sql(rows)

    seg = (datetime.now() - inicio).seconds
    print(f"\nETL completado en {seg}s")


if __name__ == "__main__":
    main()
