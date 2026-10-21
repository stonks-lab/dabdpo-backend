"""
Carga datos de proyecciones en SQL Server.
Crea la tabla si no existe, luego hace upsert (merge).
"""

import pyodbc
import pandas as pd
from datetime import datetime
from config_etl import (
    SQL_SERVER, SQL_DATABASE, SQL_AUTH, SQL_USER, SQL_PASSWORD,
    SQL_SCHEMA, SQL_TABLE, LOAD_MODE,
)

# ── Conexión ──────────────────────────────────────────────────────────────────

def get_connection() -> pyodbc.Connection:
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise RuntimeError(
            "No se encontró driver ODBC para SQL Server. "
            "Instalá 'ODBC Driver 17 for SQL Server' o 'ODBC Driver 18 for SQL Server'."
        )
    driver = sorted(drivers)[-1]   # usa el más nuevo disponible

    if SQL_AUTH == "windows":
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            "Trusted_Connection=yes;"
        )
    else:
        conn_str = (
            f"DRIVER={{{driver}}};"
            f"SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};"
            f"UID={SQL_USER};PWD={SQL_PASSWORD};"
        )

    return pyodbc.connect(conn_str, autocommit=False)


# ── DDL ───────────────────────────────────────────────────────────────────────

CREATE_TABLE_SQL = f"""
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = '{SQL_SCHEMA}' AND t.name = '{SQL_TABLE}'
)
BEGIN
    CREATE TABLE [{SQL_SCHEMA}].[{SQL_TABLE}] (
        id               INT           IDENTITY(1,1) NOT NULL,
        contrato         NVARCHAR(100) NOT NULL,
        proyecto         NVARCHAR(300) NULL,
        periodo          NVARCHAR(7)   NOT NULL,   -- YYYY-MM
        monto_proyectado DECIMAL(18,2) NULL,
        moneda           NVARCHAR(5)   NULL,
        tipo_proyeccion  NVARCHAR(10)  NULL,       -- 'P&C' o 'AdC'
        fecha_carga      DATETIME      NOT NULL DEFAULT GETDATE(),
        CONSTRAINT [PK_{SQL_TABLE}] PRIMARY KEY CLUSTERED (id),
        CONSTRAINT [UQ_{SQL_TABLE}_key]
            UNIQUE (contrato, proyecto, periodo, tipo_proyeccion)
    );

    CREATE INDEX [IX_{SQL_TABLE}_periodo]  ON [{SQL_SCHEMA}].[{SQL_TABLE}] (periodo);
    CREATE INDEX [IX_{SQL_TABLE}_contrato] ON [{SQL_SCHEMA}].[{SQL_TABLE}] (contrato);

    PRINT 'Tabla {SQL_SCHEMA}.{SQL_TABLE} creada.';
END
"""


def ensure_table(conn: pyodbc.Connection):
    cursor = conn.cursor()
    cursor.execute(CREATE_TABLE_SQL)
    conn.commit()
    print(f"✓ Tabla [{SQL_SCHEMA}].[{SQL_TABLE}] verificada/creada")


# ── Carga ─────────────────────────────────────────────────────────────────────

def _normalize_df(rows: list[dict]) -> pd.DataFrame:
    """Normaliza columnas y tipos antes de insertar."""
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # Mapeo flexible de nombres de columna (el dataset de P&C puede tener variaciones)
    rename_map = {}
    for col in df.columns:
        cl = col.lower().strip()
        if cl in ("contrato", "cod_contrato", "codigo_contrato", "contract"):
            rename_map[col] = "contrato"
        elif cl in ("proyecto", "project", "cod_proyecto"):
            rename_map[col] = "proyecto"
        elif cl in ("periodo", "period", "fecha_periodo", "mes"):
            rename_map[col] = "periodo"
        elif cl in ("monto_proyectado", "monto", "amount", "monto_proyeccion"):
            rename_map[col] = "monto_proyectado"
        elif cl in ("moneda", "currency", "moneda_proyeccion"):
            rename_map[col] = "moneda"
        elif cl in ("tipo_proyeccion", "tipo", "type", "fuente"):
            rename_map[col] = "tipo_proyeccion"
    df.rename(columns=rename_map, inplace=True)

    required = ["contrato", "periodo"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(f"Columnas requeridas no encontradas: {missing}\nColumnas disponibles: {list(df.columns)}")

    # Asegurar tipos
    df["contrato"] = df["contrato"].astype(str).str.strip()
    df["periodo"]  = df["periodo"].astype(str).str[:7]   # forzar YYYY-MM

    if "monto_proyectado" in df.columns:
        df["monto_proyectado"] = pd.to_numeric(df["monto_proyectado"], errors="coerce")
    else:
        df["monto_proyectado"] = None

    for col in ("proyecto", "moneda", "tipo_proyeccion"):
        if col not in df.columns:
            df[col] = None
        else:
            df[col] = df[col].astype(str).where(df[col].notna(), None)

    df["fecha_carga"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return df[["contrato", "proyecto", "periodo", "monto_proyectado", "moneda", "tipo_proyeccion", "fecha_carga"]]


def load_full(conn: pyodbc.Connection, rows: list[dict]):
    """Borra todo y recarga."""
    df = _normalize_df(rows)
    cursor = conn.cursor()
    cursor.execute(f"TRUNCATE TABLE [{SQL_SCHEMA}].[{SQL_TABLE}]")
    _bulk_insert(cursor, df)
    conn.commit()
    print(f"✓ Full load: {len(df):,} filas insertadas")


def load_incremental(conn: pyodbc.Connection, rows: list[dict]):
    """
    MERGE (upsert) sobre la clave (contrato, proyecto, periodo, tipo_proyeccion).
    Inserta nuevas filas, actualiza las existentes.
    """
    df = _normalize_df(rows)
    if df.empty:
        print("  Sin filas para cargar")
        return

    cursor = conn.cursor()

    # Tabla temporal para el merge
    cursor.execute(f"""
        CREATE TABLE #proy_stage (
            contrato         NVARCHAR(100),
            proyecto         NVARCHAR(300),
            periodo          NVARCHAR(7),
            monto_proyectado DECIMAL(18,2),
            moneda           NVARCHAR(5),
            tipo_proyeccion  NVARCHAR(10),
            fecha_carga      DATETIME
        )
    """)
    _bulk_insert(cursor, df, table="#proy_stage")

    cursor.execute(f"""
        MERGE [{SQL_SCHEMA}].[{SQL_TABLE}] AS tgt
        USING #proy_stage AS src
            ON  tgt.contrato        = src.contrato
            AND ISNULL(tgt.proyecto, '')      = ISNULL(src.proyecto, '')
            AND tgt.periodo         = src.periodo
            AND ISNULL(tgt.tipo_proyeccion,'') = ISNULL(src.tipo_proyeccion,'')
        WHEN MATCHED THEN
            UPDATE SET
                tgt.monto_proyectado = src.monto_proyectado,
                tgt.moneda           = src.moneda,
                tgt.fecha_carga      = src.fecha_carga
        WHEN NOT MATCHED THEN
            INSERT (contrato, proyecto, periodo, monto_proyectado, moneda, tipo_proyeccion, fecha_carga)
            VALUES (src.contrato, src.proyecto, src.periodo, src.monto_proyectado, src.moneda, src.tipo_proyeccion, src.fecha_carga);
    """)

    affected = cursor.rowcount
    conn.commit()
    print(f"✓ Incremental: {affected:,} filas insertadas/actualizadas")


def _bulk_insert(cursor, df: pd.DataFrame, table: str = None):
    tbl = table or f"[{SQL_SCHEMA}].[{SQL_TABLE}]"
    cols = list(df.columns)
    placeholders = ",".join(["?" for _ in cols])
    col_names = ",".join([f"[{c}]" for c in cols])
    sql = f"INSERT INTO {tbl} ({col_names}) VALUES ({placeholders})"
    data = [tuple(None if pd.isna(v) else v for v in row) for row in df.itertuples(index=False)]
    cursor.fast_executemany = True
    cursor.executemany(sql, data)


# ── Punto de entrada ──────────────────────────────────────────────────────────

def cargar_en_sql(rows: list[dict]):
    print(f"\nConectando a SQL Server {SQL_SERVER}/{SQL_DATABASE}...")
    conn = get_connection()
    print("✓ Conexión establecida")

    ensure_table(conn)

    if LOAD_MODE == "full":
        load_full(conn, rows)
    else:
        load_incremental(conn, rows)

    conn.close()
    print("✓ Conexión cerrada")
