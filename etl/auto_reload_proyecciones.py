"""
Auto-recarga dbo.proyecciones desde d2.parquet si el parquet es más nuevo
que la última carga registrada en SQL Server.

Pensado para correr como tarea programada (junto con el ETL de PBI).

Uso:
  python auto_reload_proyecciones.py            # recarga si es necesario
  python auto_reload_proyecciones.py --force    # recarga siempre
  python auto_reload_proyecciones.py --check    # solo informa, no carga
"""

import sys, argparse, os, logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, '../backend')

PARQUET   = Path(__file__).parent / 'd2.parquet'
LOG_FILE  = Path(__file__).parent / 'etl.log'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [auto_reload] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE, encoding='utf-8'),
        logging.StreamHandler(sys.stdout),
    ]
)
log = logging.getLogger(__name__)


def ultima_carga_sql() -> datetime | None:
    """Retorna la fecha de la última carga en dbo.proyecciones, o None si está vacía."""
    try:
        from app.data.sql_server import query_df
        df = query_df("SELECT MAX(fecha_carga) AS ult FROM dbo.proyecciones")
        val = df.iloc[0]['ult']
        return pd.Timestamp(val).to_pydatetime() if pd.notna(val) else None
    except Exception as e:
        log.warning(f"No se pudo consultar SQL Server: {e}")
        return None


def fecha_parquet() -> datetime:
    return datetime.fromtimestamp(PARQUET.stat().st_mtime)


def recargar() -> bool:
    try:
        import pandas as pd
        from app.data.sql_server import load_proyecciones_pyc, log_carga
        df = pd.read_parquet(PARQUET)
        result = load_proyecciones_pyc(df)
        log_carga('dbo.proyecciones', 'auto_reload', PARQUET.name, 'scheduler')
        log.info(
            f"Carga completada: {result['filas_insertadas']:,} filas | "
            f"{result['contratos']} contratos | "
            f"periodos {result['periodos'][0]} → {result['periodos'][-1]}"
        )
        return True
    except Exception as e:
        log.error(f"Error en la recarga: {e}")
        return False


def main():
    import pandas as pd  # importado aquí para que el log esté configurado antes

    parser = argparse.ArgumentParser()
    parser.add_argument('--force', action='store_true', help='Recarga siempre, sin comparar fechas')
    parser.add_argument('--check', action='store_true', help='Solo informa, no carga')
    args = parser.parse_args()

    if not PARQUET.exists():
        log.error(f"Parquet no encontrado: {PARQUET}")
        sys.exit(1)

    fecha_pq  = fecha_parquet()
    fecha_sql = ultima_carga_sql()

    log.info(f"Parquet modificado : {fecha_pq.strftime('%Y-%m-%d %H:%M:%S')}")
    log.info(f"Última carga SQL   : {fecha_sql.strftime('%Y-%m-%d %H:%M:%S') if fecha_sql else 'nunca'}")

    if args.check:
        if fecha_sql is None or fecha_pq > fecha_sql:
            log.info("-> Parquet más nuevo que SQL. Se requiere recarga.")
        else:
            log.info("-> SQL Server está al día.")
        return

    if args.force:
        log.info("--force: recargando sin comparar fechas...")
        recargar()
        return

    if fecha_sql is None:
        log.info("SQL Server sin datos previos. Cargando...")
        recargar()
    elif fecha_pq > fecha_sql:
        log.info(f"Parquet {(fecha_pq - fecha_sql).seconds // 60} min más nuevo. Recargando...")
        recargar()
    else:
        log.info("SQL Server ya está sincronizado con el parquet. Sin cambios.")


if __name__ == '__main__':
    main()
