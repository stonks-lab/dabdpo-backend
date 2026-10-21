"""
Carga datos del Sourcing Plan en SQL Server dbo.sourcing_plan.
Formato largo: una fila por (proyecto × mes).
Siempre TRUNCATE + reload.
"""
import pyodbc
import pandas as pd
from datetime import datetime

from config_etl import SQL_SERVER, SQL_DATABASE, SQL_AUTH, SQL_USER, SQL_PASSWORD, SP_SQL_TABLE

_SCHEMA = "dbo"

_CREATE_SQL = f"""
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = '{_SCHEMA}' AND t.name = '{SP_SQL_TABLE}'
)
BEGIN
    CREATE TABLE [{_SCHEMA}].[{SP_SQL_TABLE}] (
        id             INT             IDENTITY(1,1) NOT NULL,
        estatus        NVARCHAR(100)   NULL,
        proyecto       NVARCHAR(300)   NULL,
        id_blanco      NVARCHAR(100)   NULL,
        programa       NVARCHAR(100)   NULL,
        tipo_fin       NVARCHAR(100)   NULL,
        cliente        NVARCHAR(200)   NULL,
        contrato       NVARCHAR(100)   NULL,
        empresa        NVARCHAR(200)   NULL,
        lic            NVARCHAR(100)   NULL,
        tipo           NVARCHAR(50)    NULL,
        inicio         DATE            NULL,
        termino        DATE            NULL,
        alcance        NVARCHAR(500)   NULL,
        cantidad_total DECIMAL(18,2)   NULL,
        periodo        NVARCHAR(7)     NOT NULL,
        cantidad_plan  DECIMAL(18,2)   NULL,
        activo         BIT             NULL,
        fecha_carga    DATETIME        NOT NULL DEFAULT GETDATE(),
        CONSTRAINT [PK_{SP_SQL_TABLE}] PRIMARY KEY CLUSTERED (id)
    );
    CREATE INDEX [IX_{SP_SQL_TABLE}_contrato_periodo]
        ON [{_SCHEMA}].[{SP_SQL_TABLE}] (contrato, periodo);
    CREATE INDEX [IX_{SP_SQL_TABLE}_proyecto]
        ON [{_SCHEMA}].[{SP_SQL_TABLE}] (proyecto);
    PRINT 'Tabla {_SCHEMA}.{SP_SQL_TABLE} creada.';
END
"""

_COLS = [
    "estatus", "proyecto", "id_blanco", "programa", "tipo_fin",
    "cliente", "contrato", "empresa", "lic", "tipo",
    "inicio", "termino", "alcance", "cantidad_total",
    "periodo", "cantidad_plan", "activo", "fecha_carga",
]


def _get_conn() -> pyodbc.Connection:
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise RuntimeError("No se encontró driver ODBC para SQL Server.")
    driver = sorted(drivers)[-1]
    if SQL_AUTH == "windows":
        cs = (f"DRIVER={{{driver}}};SERVER={SQL_SERVER};"
              f"DATABASE={SQL_DATABASE};Trusted_Connection=yes;")
    else:
        cs = (f"DRIVER={{{driver}}};SERVER={SQL_SERVER};"
              f"DATABASE={SQL_DATABASE};UID={SQL_USER};PWD={SQL_PASSWORD};")
    return pyodbc.connect(cs, autocommit=False)


def _safe(v):
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    return v


_COL_TYPES = {
    "estatus":        "NVARCHAR(100)",
    "proyecto":       "NVARCHAR(300)",
    "id_blanco":      "NVARCHAR(100)",
    "programa":       "NVARCHAR(100)",
    "tipo_fin":       "NVARCHAR(100)",
    "cliente":        "NVARCHAR(200)",
    "contrato":       "NVARCHAR(100)",
    "empresa":        "NVARCHAR(200)",
    "lic":            "NVARCHAR(100)",
    "tipo":           "NVARCHAR(50)",
    "inicio":         "DATE",
    "termino":        "DATE",
    "alcance":        "NVARCHAR(500)",
    "cantidad_total": "DECIMAL(18,2)",
    "periodo":        "NVARCHAR(7)",
    "cantidad_plan":  "DECIMAL(18,2)",
    "activo":         "BIT",
    "fecha_carga":    "DATETIME",
}


def _ensure_columns(cur, conn):
    """Agrega columnas que falten en la tabla (migración automática)."""
    existing = {
        row[0].lower()
        for row in cur.execute(
            f"SELECT COLUMN_NAME FROM INFORMATION_SCHEMA.COLUMNS "
            f"WHERE TABLE_SCHEMA='{_SCHEMA}' AND TABLE_NAME='{SP_SQL_TABLE}'"
        ).fetchall()
    }
    for col, sql_type in _COL_TYPES.items():
        if col not in existing:
            cur.execute(f"ALTER TABLE [{_SCHEMA}].[{SP_SQL_TABLE}] ADD [{col}] {sql_type} NULL")
            conn.commit()
            print(f"  + Columna agregada: {col} ({sql_type})")


def cargar_sourcing_plan(rows: list[dict]):
    print(f"\nConectando a SQL Server {SQL_SERVER}/{SQL_DATABASE}...")
    conn = _get_conn()
    print("✓ Conexión establecida")

    cur = conn.cursor()
    cur.execute(_CREATE_SQL)
    conn.commit()
    print(f"✓ Tabla [{_SCHEMA}].[{SP_SQL_TABLE}] verificada/creada")

    _ensure_columns(cur, conn)
    print("✓ Columnas verificadas/migradas")

    df = pd.DataFrame(rows)
    cols = [c for c in _COLS if c in df.columns]
    df = df[cols].copy()

    # Tipos
    for dc in ("inicio", "termino"):
        if dc in df.columns:
            df[dc] = pd.to_datetime(df[dc], errors="coerce").dt.date
            df[dc] = df[dc].where(df[dc].notna(), None)

    for nc in ("cantidad_plan", "cantidad_total"):
        if nc in df.columns:
            df[nc] = pd.to_numeric(df[nc], errors="coerce")

    if "activo" in df.columns:
        df["activo"] = df["activo"].astype(bool).astype(int)

    df["fecha_carga"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    cur.execute(f"TRUNCATE TABLE [{_SCHEMA}].[{SP_SQL_TABLE}]")

    col_names    = ",".join([f"[{c}]" for c in cols])
    placeholders = ",".join(["?" for _ in cols])
    sql  = f"INSERT INTO [{_SCHEMA}].[{SP_SQL_TABLE}] ({col_names}) VALUES ({placeholders})"
    data = [tuple(_safe(v) for v in row) for row in df.itertuples(index=False)]

    cur.fast_executemany = True
    cur.executemany(sql, data)
    conn.commit()
    print(f"✓ {len(df):,} filas cargadas en [{_SCHEMA}].[{SP_SQL_TABLE}]")

    conn.close()
    print("✓ Conexión cerrada")
