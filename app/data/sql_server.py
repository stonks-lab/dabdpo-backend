"""
Conexión y queries a SQL Server.
Usado por el router de proyecciones (y extensible para otras tablas).
"""

import re
import unicodedata
import pyodbc
import pandas as pd
from datetime import datetime
from functools import lru_cache
from app.config import SQL_SERVER, SQL_DATABASE, SQL_AUTH, SQL_USER, SQL_PASSWORD


def _normalize_col(s: str) -> str:
    """Normaliza nombre de columna a snake_case ASCII."""
    s = unicodedata.normalize('NFKD', str(s).strip()).encode('ascii', 'ignore').decode('ascii')
    s = s.lower().replace(' ', '_').replace('-', '_')
    return re.sub(r'[^a-z0-9_]', '', s) or 'col'


def get_connection() -> pyodbc.Connection:
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise RuntimeError("No hay driver ODBC para SQL Server instalado.")
    driver = sorted(drivers)[-1]

    if SQL_AUTH == "windows":
        cs = (
            f"DRIVER={{{driver}}};SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};Trusted_Connection=yes;"
        )
    else:
        cs = (
            f"DRIVER={{{driver}}};SERVER={SQL_SERVER};"
            f"DATABASE={SQL_DATABASE};UID={SQL_USER};PWD={SQL_PASSWORD};"
        )
    return pyodbc.connect(cs)


def query_df(sql: str, params: tuple = ()) -> pd.DataFrame:
    """Ejecuta un SELECT y devuelve DataFrame."""
    conn = get_connection()
    try:
        df = pd.read_sql(sql, conn, params=params)
        return df
    finally:
        conn.close()


# ── Proyecciones ─────────────────────────────────────────────────────────────

def get_proyecciones(
    contrato: str | None = None,
    periodo_desde: str | None = None,
    tipo: str | None = None,
) -> pd.DataFrame:
    conditions = ["1=1"]
    params: list = []

    if contrato:
        conditions.append("contrato = ?")
        params.append(contrato)
    if periodo_desde:
        conditions.append("periodo >= ?")
        params.append(periodo_desde)
    if tipo:
        conditions.append("tipo_proyeccion = ?")
        params.append(tipo)

    sql = f"""
        SELECT contrato, proyecto, periodo, monto_proyectado, moneda, tipo_proyeccion, fecha_carga
        FROM dbo.proyecciones
        WHERE {' AND '.join(conditions)}
        ORDER BY contrato, periodo, tipo_proyeccion
    """
    return query_df(sql, tuple(params))


def get_proyecciones_vs_ep(periodo_desde: str | None = None) -> pd.DataFrame:
    """Lee la vista que cruza proyecciones con EP real."""
    cond = "WHERE periodo >= ?" if periodo_desde else ""
    params = (periodo_desde,) if periodo_desde else ()
    sql = f"""
        SELECT contrato, proyecto, periodo, tipo_proyeccion,
               monto_proyectado, ep_consumido_clp, diferencia_clp
        FROM dbo.v_proyecciones_vs_ep
        {cond}
        ORDER BY contrato, periodo
    """
    return query_df(sql, params)


def get_proyecciones_kpis() -> dict:
    sql = """
        SELECT
            COUNT(DISTINCT contrato)                AS contratos_con_proyeccion,
            SUM(CASE WHEN tipo_proyeccion = 'P&C' THEN monto_proyectado ELSE 0 END) AS total_pyc,
            SUM(CASE WHEN tipo_proyeccion = 'AdC' THEN monto_proyectado ELSE 0 END) AS total_adc,
            SUM(monto_proyectado)                   AS total_proyectado,
            MIN(periodo)                            AS periodo_min,
            MAX(periodo)                            AS periodo_max,
            MAX(fecha_carga)                        AS ultima_carga
        FROM dbo.proyecciones
        WHERE periodo >= FORMAT(DATEADD(month, -1, GETDATE()), 'yyyy-MM')
    """
    df = query_df(sql)
    row = df.iloc[0]
    return {
        "contratos_con_proyeccion": int(row["contratos_con_proyeccion"] or 0),
        "total_pyc":                float(row["total_pyc"] or 0),
        "total_adc":                float(row["total_adc"] or 0),
        "total_proyectado":         float(row["total_proyectado"] or 0),
        "periodo_min":              str(row["periodo_min"] or ""),
        "periodo_max":              str(row["periodo_max"] or ""),
        "ultima_carga":             str(row["ultima_carga"] or ""),
    }


# ── Sourcing Plan ─────────────────────────────────────────────────────────────

def get_sourcing_kpis() -> dict:
    sql = """
        SELECT
            COUNT(DISTINCT proyecto)                                    AS total_proyectos,
            COUNT(DISTINCT contrato)                                    AS total_contratos,
            COUNT(DISTINCT id_blanco)                                   AS total_blancos,
            (SELECT COUNT(DISTINCT contrato) FROM dbo.sourcing_plan
             WHERE activo = 1
               AND periodo = FORMAT(GETDATE(), 'yyyy-MM'))              AS activos_mes_actual,
            MAX(fecha_carga)                                            AS ultima_carga
        FROM dbo.sourcing_plan
    """
    df  = query_df(sql)
    row = df.iloc[0]
    return {
        "total_proyectos":    int(row["total_proyectos"]    or 0),
        "total_contratos":    int(row["total_contratos"]    or 0),
        "total_blancos":      int(row["total_blancos"]      or 0),
        "activos_mes_actual": int(row["activos_mes_actual"] or 0),
        "ultima_carga":       str(row["ultima_carga"]       or ""),
    }


def get_sourcing_por_mes() -> list[dict]:
    sql = """
        SELECT periodo, SUM(cantidad_plan) AS cantidad_total
        FROM dbo.sourcing_plan
        GROUP BY periodo
        ORDER BY periodo
    """
    return query_df(sql).fillna(0).to_dict("records")


def get_sourcing_por_estatus() -> list[dict]:
    sql = """
        SELECT
            ISNULL(estatus, 'Sin Estatus')  AS estatus,
            COUNT(DISTINCT contrato)        AS contratos
        FROM dbo.sourcing_plan
        GROUP BY estatus
        ORDER BY contratos DESC
    """
    return query_df(sql).fillna(0).to_dict("records")


def get_sourcing_por_proyecto() -> list[dict]:
    sql = """
        SELECT
            proyecto,
            SUM(cantidad_plan)       AS cantidad_total,
            COUNT(DISTINCT contrato) AS contratos
        FROM dbo.sourcing_plan
        GROUP BY proyecto
        ORDER BY cantidad_total DESC
    """
    return query_df(sql).fillna(0).to_dict("records")


def get_sourcing_tabla() -> list[dict]:
    sql = """
        SELECT
            MAX(estatus)    AS estatus,
            MAX(proyecto)   AS proyecto,
            MAX(id_blanco)  AS id_blanco,
            contrato,
            MAX(empresa)    AS empresa,
            MAX(tipo)       AS tipo,
            MIN(inicio)     AS inicio,
            MAX(termino)    AS termino,
            MAX(alcance)    AS alcance,
            SUM(cantidad_plan) AS cantidad_total_plan
        FROM dbo.sourcing_plan
        GROUP BY contrato
        ORDER BY MAX(proyecto), contrato
    """
    df = query_df(sql)
    for col in ("inicio", "termino"):
        if col in df.columns:
            df[col] = df[col].astype(str).replace("NaT", "").replace("None", "")
    return df.fillna("").to_dict("records")


def get_proyecciones_por_mes() -> list[dict]:
    sql = """
        SELECT periodo,
               tipo_proyeccion,
               SUM(monto_proyectado) AS monto
        FROM dbo.proyecciones
        GROUP BY periodo, tipo_proyeccion
        ORDER BY periodo, tipo_proyeccion
    """
    return query_df(sql).fillna(0).to_dict("records")


# ── Carga desde parquet P&C ───────────────────────────────────────────────────

_CREATE_PROYECCIONES = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t
    JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'dbo' AND t.name = 'proyecciones'
)
BEGIN
    CREATE TABLE dbo.proyecciones (
        id               INT           IDENTITY(1,1) NOT NULL,
        contrato         NVARCHAR(200) NOT NULL,
        proyecto         NVARCHAR(300) NULL,
        periodo          NVARCHAR(7)   NOT NULL,
        monto_proyectado DECIMAL(18,2) NULL,
        moneda           NVARCHAR(10)  NULL,
        tipo_proyeccion  NVARCHAR(10)  NULL,
        fecha_carga      DATETIME      NOT NULL CONSTRAINT DF_proyecciones_fecha_carga DEFAULT GETDATE(),
        CONSTRAINT PK_proyecciones PRIMARY KEY CLUSTERED (id)
    );
    CREATE NONCLUSTERED INDEX IX_proyecciones_periodo  ON dbo.proyecciones (periodo);
    CREATE NONCLUSTERED INDEX IX_proyecciones_contrato ON dbo.proyecciones (contrato);
END
"""


def _ensure_proyecciones_table(conn: pyodbc.Connection) -> None:
    cursor = conn.cursor()
    cursor.execute(_CREATE_PROYECCIONES)
    conn.commit()


def _transform_pyc(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.strip() for c in df.columns]

    date_cols = [c for c in df.columns if len(c) == 10 and c[4] == '-' and c[7] == '-']
    id_cols   = [c for c in ['CODIGO PROVEEDOR', 'ID_BLANCO', 'MONEDA'] if c in df.columns]

    for col in date_cols:
        df[col] = pd.to_numeric(df[col], errors='coerce')

    df_long = df[id_cols + date_cols].melt(
        id_vars=id_cols, value_vars=date_cols,
        var_name='fecha_col', value_name='monto'
    )
    df_long = df_long[df_long['monto'].notna() & (df_long['monto'] > 0)].copy()
    df_long['periodo'] = df_long['fecha_col'].str[:7]

    df_long.rename(columns={
        'CODIGO PROVEEDOR': 'contrato',
        'ID_BLANCO':        'proyecto',
        'MONEDA':           'moneda',
    }, inplace=True)

    group = [c for c in ['contrato', 'proyecto', 'periodo', 'moneda'] if c in df_long.columns]
    result = df_long.groupby(group, dropna=False).agg(
        monto_proyectado=('monto', 'sum')
    ).reset_index()

    result['tipo_proyeccion'] = 'P&C'
    result['fecha_carga']     = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    # contrato es NOT NULL en la tabla destino
    result = result[result['contrato'].notna() & (result['contrato'].astype(str).str.strip() != '')]
    return result


def load_proyecciones_pyc(df: pd.DataFrame) -> dict:
    data = _transform_pyc(df)

    conn   = get_connection()
    cursor = conn.cursor()

    _ensure_proyecciones_table(conn)
    cursor.execute("TRUNCATE TABLE [dbo].[proyecciones]")
    conn.commit()

    cols  = ['contrato', 'proyecto', 'periodo', 'monto_proyectado', 'moneda', 'tipo_proyeccion', 'fecha_carga']
    ph    = ','.join(['?'] * len(cols))
    names = ','.join(f'[{c}]' for c in cols)
    sql   = f"INSERT INTO [dbo].[proyecciones] ({names}) VALUES ({ph})"

    rows = data[cols].astype(object).where(pd.notna(data[cols]), None).values.tolist()
    for i in range(0, len(rows), 500):
        cursor.executemany(sql, rows[i:i + 500])
    conn.commit()
    conn.close()

    return {
        "filas_insertadas": len(rows),
        "contratos":        int(data['contrato'].nunique()),
        "periodos":         sorted(data['periodo'].dropna().unique().tolist()),
        "ultima_carga":     data['fecha_carga'].iloc[0] if len(data) else None,
    }


# ── Carga de dimensiones desde Excel ─────────────────────────────────────────

_DIM_SHEETS = {
    'Tabla_Articulos': 'dim_articulos',
    'Tabla_Proyectos': 'dim_proyectos',
    'Tabla_Programas': 'dim_programas',
}


def _load_dim_table(cursor, conn, table: str, df: pd.DataFrame) -> int:
    df = df.copy()
    df.columns = [c.strip() for c in df.columns]
    df['fecha_carga'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    df = df.astype(object).where(pd.notna(df), None)

    cursor.execute(
        f"IF OBJECT_ID(N'dbo.[{table}]', N'U') IS NOT NULL DROP TABLE [dbo].[{table}]"
    )
    col_defs = ', '.join(f'[{c}] NVARCHAR(MAX)' for c in df.columns)
    cursor.execute(f'CREATE TABLE [dbo].[{table}] ({col_defs})')
    conn.commit()

    ph  = ','.join(['?'] * len(df.columns))
    nms = ','.join(f'[{c}]' for c in df.columns)
    sql = f'INSERT INTO [dbo].[{table}] ({nms}) VALUES ({ph})'
    rows = df.values.tolist()
    for i in range(0, len(rows), 500):
        cursor.executemany(sql, rows[i:i + 500])
    conn.commit()
    return len(rows)


def load_dimensiones(excel_bytes: bytes) -> dict:
    import io
    xl = pd.ExcelFile(io.BytesIO(excel_bytes))

    missing = [s for s in _DIM_SHEETS if s not in xl.sheet_names]
    if missing:
        raise ValueError(f"Hojas no encontradas en el Excel: {missing}. Hojas disponibles: {xl.sheet_names}")

    conn   = get_connection()
    cursor = conn.cursor()
    resumen = {}
    try:
        for sheet, table in _DIM_SHEETS.items():
            df = pd.read_excel(io.BytesIO(excel_bytes), sheet_name=sheet)
            n  = _load_dim_table(cursor, conn, table, df)
            resumen[table] = n
    finally:
        conn.close()

    return {"tablas": resumen, "ultima_carga": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


# ── Carga de incidencias desde Excel ─────────────────────────────────────────

def load_incidencias(excel_bytes: bytes) -> dict:
    import io
    xl = pd.ExcelFile(io.BytesIO(excel_bytes))
    if 'Registro incidencias' not in xl.sheet_names:
        raise ValueError(f"Hoja 'Registro incidencias' no encontrada. Hojas disponibles: {xl.sheet_names}")

    df = pd.read_excel(io.BytesIO(excel_bytes), sheet_name='Registro incidencias')
    # Eliminar columnas sin nombre (Unnamed)
    df = df.loc[:, ~df.columns.str.startswith('Unnamed')]

    conn   = get_connection()
    cursor = conn.cursor()
    n = _load_dim_table(cursor, conn, 'registro_incidencias', df)
    conn.close()

    return {"tabla": "registro_incidencias", "filas_insertadas": n,
            "ultima_carga": datetime.now().strftime('%Y-%m-%d %H:%M:%S')}


# ── Lectura de incidencias ────────────────────────────────────────────────────

_INC_RENAME = {
    0:  'administrador',
    1:  'codigo',
    2:  'fecha',
    3:  'proyecto',
    4:  'contrato',
    5:  'proveedor',
    6:  'alcance',
    7:  'tipo',
    8:  'impacto',
    9:  'descripcion',
    10: 'estado',
    11: 'horas_perdidas',
    12: 'impacto_metros',
    13: 'impacto_economico',
    14: 'compromiso',
    15: 'responsable_compromiso',
    16: 'fecha_cierre_compromiso',
    17: 'estado_compromiso',
    18: 'categoria_compromiso',
    19: 'observaciones',
}

def get_incidencias() -> list[dict]:
    df = query_df("SELECT * FROM dbo.registro_incidencias")
    # Renombrar por posición (evita problemas de encoding en nombres de columna)
    rename = {df.columns[i]: name for i, name in _INC_RENAME.items() if i < len(df.columns)}
    df.rename(columns=rename, inplace=True)
    # Descartar columna de metadata
    df.drop(columns=[c for c in df.columns if c == 'fecha_carga'], errors='ignore', inplace=True)
    for col in ('horas_perdidas', 'impacto_metros', 'impacto_economico'):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)
    # Normalizar fecha a ISO YYYY-MM-DD para que el frontend la parsee sin problemas
    if 'fecha' in df.columns:
        parsed = pd.to_datetime(df['fecha'], dayfirst=True, errors='coerce')
        df['fecha'] = parsed.dt.strftime('%Y-%m-%d').where(parsed.notna(), '')
    return df.fillna('').to_dict('records')


# ── Conexión secundaria (team_bi) ─────────────────────────────────────────────

def get_connection_bi() -> pyodbc.Connection:
    from app.config import SQL_BI_USER, SQL_BI_PASSWORD
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise RuntimeError("No hay driver ODBC para SQL Server instalado.")
    driver = sorted(drivers)[-1]
    cs = (
        f"DRIVER={{{driver}}};SERVER={SQL_SERVER};"
        f"DATABASE={SQL_DATABASE};UID={SQL_BI_USER};PWD={SQL_BI_PASSWORD};"
    )
    return pyodbc.connect(cs)


def query_df_bi(sql: str) -> pd.DataFrame:
    conn = get_connection_bi()
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


# ── PAI Proyectos ─────────────────────────────────────────────────────────────

def get_pai_proyectos() -> list[dict]:
    df = query_df_bi("SELECT * FROM dbo.PAI_Proyectos")
    df.columns = [_normalize_col(c) for c in df.columns]

    # Normalizar columna mes a YYYY-MM
    if 'mes' in df.columns:
        parsed = pd.to_datetime(df['mes'], dayfirst=True, errors='coerce')
        mask = parsed.notna()
        df.loc[mask, 'mes'] = parsed[mask].dt.strftime('%Y-%m')

    # Convertir columnas de metros a numérico
    for col in [c for c in df.columns if 'metro' in c]:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    return df.fillna('').to_dict('records')


# ── Avance Semanal ────────────────────────────────────────────────────────────

def get_avance_semanal() -> list[dict]:
    df = query_df_bi("SELECT * FROM dbo.dbo_Avance_Semanal")
    df.columns = [_normalize_col(c) for c in df.columns]

    # Normalizar primera columna de fecha (no fecha_carga) a ISO YYYY-MM-DD
    for col in df.columns:
        if 'fecha' in col and col != 'fecha_carga':
            parsed = pd.to_datetime(df[col], dayfirst=True, errors='coerce')
            df[col] = parsed.dt.strftime('%Y-%m-%d').where(parsed.notna(), '')
            break

    # Convertir columnas de metros a numérico
    for col in [c for c in df.columns if 'metro' in c]:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    return df.fillna('').to_dict('records')
