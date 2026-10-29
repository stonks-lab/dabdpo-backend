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


# ── Proveedores EP ───────────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def get_proveedor_map_ep() -> dict:
    """Devuelve dict {codigo_ctt: proveedor} desde dbo.SIC_EPs."""
    try:
        df = query_df(
            "SELECT [Contrato Código] AS codigo_ctt, MAX(Proveedor) AS proveedor "
            "FROM dbo.SIC_EPs WHERE Proveedor IS NOT NULL GROUP BY [Contrato Código]"
        )
        return dict(zip(df['codigo_ctt'].astype(str), df['proveedor'].astype(str)))
    except Exception:
        return {}


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
            MAX(estatus)       AS estatus,
            proyecto,
            id_blanco,
            contrato,
            MAX(empresa)       AS empresa,
            MAX(lic)           AS lic,
            MAX(tipo)          AS tipo,
            MIN(inicio)        AS inicio,
            MAX(termino)       AS termino,
            alcance,
            SUM(cantidad_plan) AS cantidad_total_plan,
            CASE
                WHEN ISNULL(contrato, '') != '' THEN 'Con contrato'
                WHEN ISNULL(MAX(lic), '')  != '' THEN 'En licitación'
                ELSE 'Sin contrato ni licitación'
            END AS estado_contrato
        FROM dbo.sourcing_plan
        GROUP BY proyecto, id_blanco, alcance, contrato
        ORDER BY proyecto, id_blanco, contrato
    """
    df = query_df(sql)
    for col in ("inicio", "termino"):
        if col in df.columns:
            df[col] = df[col].astype(str).replace("NaT", "").replace("None", "")
    return df.fillna("").to_dict("records")


def get_sourcing_cobertura_metros() -> list[dict]:
    """
    Devuelve metros cubiertos vs no cubiertos por mes.
    Cubierto = todos los alcances del id_blanco tienen contrato asignado en sourcing_plan.
    Metros vienen de dbo.PAI_Proyectos (versión más reciente), columna metros_pai.
    """
    sp = query_df("""
        SELECT id_blanco, ISNULL(alcance, '') AS alcance,
               MAX(CASE WHEN ISNULL(contrato, '') != '' THEN 1 ELSE 0 END) AS tiene_contrato
        FROM dbo.sourcing_plan
        WHERE id_blanco IS NOT NULL AND id_blanco != ''
        GROUP BY id_blanco, ISNULL(alcance, '')
    """)

    if sp.empty:
        return []

    coverage = sp.groupby('id_blanco')['tiene_contrato'].min()
    covered_ids = set(coverage[coverage == 1].index.astype(str).tolist())

    try:
        pai = query_df_bi("SELECT * FROM dbo.PAI_Proyectos")
    except Exception:
        return []
    if pai.empty:
        return []

    pai.columns = [_normalize_col(c) for c in pai.columns]

    pai = _keep_latest_pai(pai)

    id_col = 'id_blanco' if 'id_blanco' in pai.columns else next(
        (c for c in pai.columns if 'blanco' in c.lower()), None
    )
    if not id_col or 'mes' not in pai.columns:
        return []

    metro_col = next((c for c in pai.columns if 'metro' in c.lower() and 'pai' in c.lower()), None)
    if not metro_col:
        metro_col = next((c for c in pai.columns if 'metro' in c.lower() and 'program' in c.lower()), None)
    if not metro_col:
        metro_col = next((c for c in pai.columns if 'metro' in c.lower()), None)
    if not metro_col:
        return []

    _bad = {'', 'none', 'nan'}
    pai = pai[pai[id_col].notna() & ~pai[id_col].astype(str).str.strip().str.lower().isin(_bad)].copy()
    parsed = pd.to_datetime(pai['mes'], format='%Y-%m-%d', errors='coerce')
    pai['_mes'] = parsed.dt.strftime('%Y-%m').where(parsed.notna(), None)
    pai = pai[pai['_mes'].notna()].copy()
    pai[metro_col] = pd.to_numeric(pai[metro_col], errors='coerce').fillna(0)

    pai['_cubierto'] = pai[id_col].astype(str).isin(covered_ids)
    grp = pai.groupby(['_mes', '_cubierto'])[metro_col].sum().reset_index()

    result: dict[str, dict] = {}
    for _, row in grp.iterrows():
        mes = str(row['_mes'])
        if mes not in result:
            result[mes] = {'mes': mes, 'cubiertos': 0.0, 'no_cubiertos': 0.0}
        if row['_cubierto']:
            result[mes]['cubiertos'] = round(float(row[metro_col]), 1)
        else:
            result[mes]['no_cubiertos'] = round(float(row[metro_col]), 1)

    return sorted(result.values(), key=lambda x: x['mes'])


def get_sourcing_cobertura_detalle(mes: str, cubierto: bool) -> dict:
    """
    Para un mes y tipo (cubierto/no_cubierto) devuelve:
    - por_proyecto: metros PAI agrupados por proyecto (para gráfico)
    - detalle: contratos de sourcing_plan para esos id_blancos
    """
    sp = query_df("""
        SELECT id_blanco, ISNULL(alcance, '') AS alcance,
               MAX(CASE WHEN ISNULL(contrato, '') != '' THEN 1 ELSE 0 END) AS tiene_contrato
        FROM dbo.sourcing_plan
        WHERE id_blanco IS NOT NULL AND id_blanco != ''
        GROUP BY id_blanco, ISNULL(alcance, '')
    """)
    if sp.empty:
        return {"por_proyecto": [], "detalle": []}

    coverage = sp.groupby('id_blanco')['tiene_contrato'].min()
    covered_ids = set(coverage[coverage == 1].index.astype(str).tolist())

    try:
        pai = query_df_bi("SELECT * FROM dbo.PAI_Proyectos")
    except Exception:
        pai = pd.DataFrame()

    id_blancos_filtrados: list[str] = []
    por_proyecto: list[dict] = []

    if not pai.empty:
        pai.columns = [_normalize_col(c) for c in pai.columns]
        pai = _keep_latest_pai(pai)

        id_col    = 'id_blanco' if 'id_blanco' in pai.columns else next((c for c in pai.columns if 'blanco' in c.lower()), None)
        metro_col = next((c for c in pai.columns if 'metro' in c.lower() and 'pai' in c.lower()), None) or \
                    next((c for c in pai.columns if 'metro' in c.lower()), None)

        if id_col and metro_col and 'mes' in pai.columns:
            _bad = {'', 'none', 'nan'}
            pai = pai[pai[id_col].notna() & ~pai[id_col].astype(str).str.strip().str.lower().isin(_bad)].copy()
            parsed = pd.to_datetime(pai['mes'], format='%Y-%m-%d', errors='coerce')
            pai['_mes'] = parsed.dt.strftime('%Y-%m').where(parsed.notna(), None)
            pai = pai[pai['_mes'] == mes].copy()
            pai[metro_col] = pd.to_numeric(pai[metro_col], errors='coerce').fillna(0)
            pai['_cubierto'] = pai[id_col].astype(str).isin(covered_ids)
            pai_f = pai[pai['_cubierto'] == cubierto].copy()

            id_blancos_filtrados = list(pai_f[id_col].astype(str).unique())

            nombre_sp = query_df("""
                SELECT id_blanco, MAX(proyecto) AS proyecto
                FROM dbo.sourcing_plan
                WHERE id_blanco IS NOT NULL AND id_blanco != ''
                GROUP BY id_blanco
            """)
            nombre_map = nombre_sp.set_index('id_blanco')['proyecto'].to_dict() if not nombre_sp.empty else {}

            grp = pai_f.groupby(id_col)[metro_col].sum().reset_index()
            grp.columns = ['id_blanco', 'metros']
            grp['id_blanco'] = grp['id_blanco'].astype(str)
            grp['proyecto'] = grp['id_blanco'].map(lambda x: nombre_map.get(x, x))
            grp['metros']   = grp['metros'].round(1)

            por_proyecto = (
                grp.groupby('proyecto')['metros'].sum()
                .reset_index()
                .sort_values('metros', ascending=False)
                .to_dict('records')
            )

    if not id_blancos_filtrados:
        if cubierto:
            id_blancos_filtrados = list(covered_ids)
        else:
            all_ids = set(sp['id_blanco'].astype(str).unique())
            id_blancos_filtrados = list(all_ids - covered_ids)

    if not id_blancos_filtrados:
        return {"por_proyecto": por_proyecto, "detalle": []}

    placeholders = ','.join(['?' for _ in id_blancos_filtrados])
    detalle_df = query_df(f"""
        SELECT
            MAX(proyecto)      AS proyecto,
            id_blanco,
            MAX(alcance)       AS alcance,
            contrato,
            MAX(lic)           AS lic,
            MIN(inicio)        AS inicio,
            MAX(termino)       AS termino,
            SUM(cantidad_plan) AS metros_plan,
            CASE
                WHEN ISNULL(contrato, '') != '' THEN 'Con contrato'
                WHEN ISNULL(MAX(lic), '')   != '' THEN 'En licitación'
                ELSE 'Sin contrato ni licitación'
            END AS estado_contrato
        FROM dbo.sourcing_plan
        WHERE id_blanco IN ({placeholders})
        GROUP BY id_blanco, contrato
        ORDER BY MAX(proyecto), id_blanco, contrato
    """, params=tuple(id_blancos_filtrados))

    for col in ('inicio', 'termino'):
        if col in detalle_df.columns:
            detalle_df[col] = detalle_df[col].astype(str).replace('NaT', '').replace('None', '')

    return {
        "por_proyecto": por_proyecto,
        "detalle": detalle_df.fillna('').to_dict('records'),
    }


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
    from app.config import SQL_BI_USER, SQL_BI_PASSWORD, SQL_BI_DATABASE
    drivers = [d for d in pyodbc.drivers() if "SQL Server" in d]
    if not drivers:
        raise RuntimeError("No hay driver ODBC para SQL Server instalado.")
    driver = sorted(drivers)[-1]
    cs = (
        f"DRIVER={{{driver}}};SERVER={SQL_SERVER};"
        f"DATABASE={SQL_BI_DATABASE};UID={SQL_BI_USER};PWD={SQL_BI_PASSWORD};"
    )
    return pyodbc.connect(cs)


def query_df_bi(sql: str) -> pd.DataFrame:
    conn = get_connection_bi()
    try:
        return pd.read_sql(sql, conn)
    finally:
        conn.close()


# ── PAI Proyectos ─────────────────────────────────────────────────────────────

def _keep_latest_pai(df: "pd.DataFrame") -> "pd.DataFrame":
    """Retiene solo la versión más reciente de PAI_Proyectos para evitar duplicados."""
    ano_col = next((c for c in df.columns if c in ('ano_pai', 'año_pai', 'anio_pai')), None)
    ver_col = next((c for c in df.columns if 'version' in c.lower()), None)
    fc_col  = next((c for c in df.columns if 'fecha_carga' in c), None)
    if ano_col and ver_col:
        latest = df.groupby(ano_col)[ver_col].max().reset_index(name='_max_ver')
        df = df.merge(latest, on=ano_col)
        df = df[df[ver_col] == df['_max_ver']].drop(columns='_max_ver').copy()
    elif fc_col:
        latest_date = str(df[fc_col].max())[:10]
        df = df[df[fc_col].astype(str).str[:10] == latest_date].copy()
    return df


def _fmt_ver(v: float) -> str:
    s = f"{v:.2f}".rstrip('0')
    return s + '0' if s.endswith('.') else s


def get_pai_versiones() -> list[dict]:
    """Retorna las versiones disponibles de PAI como (ano, version, es_ultima, fecha_carga, label)."""
    df = query_df_bi("SELECT * FROM dbo.PAI_Proyectos")
    df.columns = [_normalize_col(c) for c in df.columns]

    ano_col = next((c for c in df.columns if c in ('ano_pai', 'anio_pai')), None)
    ver_col = next((c for c in df.columns if 'version' in c.lower()), None)
    fc_col  = next((c for c in df.columns if 'fecha_carga' in c), None)

    if not (ano_col and ver_col):
        return []

    df[ano_col] = pd.to_numeric(df[ano_col], errors='coerce')
    df[ver_col] = pd.to_numeric(df[ver_col], errors='coerce')

    cols = [ano_col, ver_col] + ([fc_col] if fc_col else [])
    combos = df[cols].drop_duplicates().dropna(subset=[ano_col, ver_col]).copy()

    latest = df.groupby(ano_col)[ver_col].max().to_dict()

    result = []
    for _, row in combos.iterrows():
        ano      = int(row[ano_col])
        ver      = round(float(row[ver_col]), 2)
        ver_str  = _fmt_ver(ver)
        es_ultima = bool(ver == round(float(latest.get(ano, -1)), 2))
        fecha    = str(row[fc_col])[:10] if fc_col and str(row.get(fc_col, '')) not in ('', 'nan', 'None', 'NaT') else ''
        label    = f"PAI {ano} v{ver_str}" + (" (actual)" if es_ultima else (f" · {fecha}" if fecha else ""))
        result.append({
            'ano':        ano,
            'version':    ver_str,
            'es_ultima':  es_ultima,
            'fecha_carga': fecha,
            'label':      label,
            'key':        f"{ano}|{ver_str}",
        })

    return sorted(result, key=lambda x: (-x['ano'], -float(x['version'])))


def get_pai_proyectos(ano: "int | None" = None, version: "str | None" = None) -> list[dict]:
    df = query_df_bi("SELECT * FROM dbo.PAI_Proyectos")
    df.columns = [_normalize_col(c) for c in df.columns]

    if ano is not None and version is not None:
        ano_col = next((c for c in df.columns if c in ('ano_pai', 'anio_pai')), None)
        ver_col = next((c for c in df.columns if 'version' in c.lower()), None)
        if ano_col and ver_col:
            df[ano_col] = pd.to_numeric(df[ano_col], errors='coerce')
            df[ver_col] = pd.to_numeric(df[ver_col], errors='coerce').apply(lambda x: round(float(x), 2) if pd.notna(x) else None)
            df = df[(df[ano_col] == int(ano)) & (df[ver_col] == round(float(version), 2))].copy()
    else:
        df = _keep_latest_pai(df)

    # Normalizar columna mes a YYYY-MM (formato explícito para evitar ambigüedad con dayfirst)
    if 'mes' in df.columns:
        parsed = pd.to_datetime(df['mes'], format='%Y-%m-%d', errors='coerce')
        df['mes'] = parsed.dt.strftime('%Y-%m').where(parsed.notna(), '')

    # Convertir columnas de metros a numérico
    for col in [c for c in df.columns if 'metro' in c]:
        df[col] = pd.to_numeric(df[col], errors='coerce').fillna(0)

    return df.fillna('').to_dict('records')


# ── Avance Semanal ────────────────────────────────────────────────────────────

def get_avance_semanal() -> list[dict]:
    df = query_df_bi("SELECT * FROM dbo.Avance_Semanal")
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


# ── Costo Metro ───────────────────────────────────────────────────────────────

_MESES = {
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
    "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
    "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
}


def _ensure_costo_metro_schema(cursor, conn: pyodbc.Connection) -> None:
    """Crea o migra dbo.CostoMetro para incluir columna campana con PK (anio, mes, campana)."""
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='CostoMetro'
        )
        CREATE TABLE dbo.CostoMetro (
            anio                    INT           NOT NULL,
            mes                     INT           NOT NULL,
            campana                 NVARCHAR(100) NOT NULL,
            mes_nombre              NVARCHAR(20)  NOT NULL,
            costo_metro_programado  FLOAT         NOT NULL,
            costo_metro_real        FLOAT         NOT NULL,
            costo_metro_ajustado    FLOAT         NOT NULL,
            brecha                  FLOAT         NOT NULL,
            fecha_carga             DATETIME      NOT NULL CONSTRAINT DF_CM_fecha DEFAULT GETDATE(),
            CONSTRAINT PK_CostoMetro PRIMARY KEY (anio, mes, campana)
        )
    """)
    conn.commit()
    # Agrega campana si la tabla existía sin ella
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='CostoMetro' AND COLUMN_NAME='campana'
        )
        ALTER TABLE dbo.CostoMetro ADD campana NVARCHAR(100) NOT NULL
            CONSTRAINT DF_CM_campana DEFAULT 'TOTAL'
    """)
    conn.commit()
    # Columnas adicionales por compatibilidad hacia atrás
    for col, tipo in [("costo_metro_real", "FLOAT NOT NULL DEFAULT 0"),
                      ("costo_metro_ajustado", "FLOAT NOT NULL DEFAULT 0")]:
        cursor.execute(f"""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='CostoMetro' AND COLUMN_NAME='{col}'
            )
            ALTER TABLE dbo.CostoMetro ADD {col} {tipo}
        """)
    conn.commit()
    # Migra PK para incluir campana si aún no la tiene
    cursor.execute("""
        SELECT COUNT(*) FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
        WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='CostoMetro'
          AND CONSTRAINT_NAME='PK_CostoMetro' AND COLUMN_NAME='campana'
    """)
    row = cursor.fetchone()
    if not (row and row[0] > 0):
        cursor.execute("""
            SELECT COUNT(*) FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS
            WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='CostoMetro'
              AND CONSTRAINT_NAME='PK_CostoMetro'
        """)
        pk_row = cursor.fetchone()
        if pk_row and pk_row[0] > 0:
            cursor.execute("ALTER TABLE dbo.CostoMetro DROP CONSTRAINT PK_CostoMetro")
            conn.commit()
        cursor.execute(
            "ALTER TABLE dbo.CostoMetro ADD CONSTRAINT PK_CostoMetro PRIMARY KEY (anio, mes, campana)"
        )
        conn.commit()


def upsert_costo_metro(anio: int, mes: int, mes_nombre: str,
                       programado: float, real: float, ajustado: float, brecha: float,
                       campana: str = 'TOTAL') -> dict:
    """INSERT OR UPDATE en dbo.CostoMetro por (anio, mes, campana)."""
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_costo_metro_schema(cursor, conn)
        cursor.execute("""
            MERGE dbo.CostoMetro AS target
            USING (SELECT ? AS anio, ? AS mes, ? AS campana) AS src
              ON target.anio = src.anio AND target.mes = src.mes AND target.campana = src.campana
            WHEN MATCHED THEN
              UPDATE SET mes_nombre=?, costo_metro_programado=?,
                         costo_metro_real=?, costo_metro_ajustado=?,
                         brecha=?, fecha_carga=GETDATE()
            WHEN NOT MATCHED THEN
              INSERT (anio, mes, campana, mes_nombre, costo_metro_programado,
                      costo_metro_real, costo_metro_ajustado, brecha, fecha_carga)
              VALUES (?,?,?,?,?,?,?,?,GETDATE());
        """, anio, mes, campana,
             mes_nombre, programado, real, ajustado, brecha,
             anio, mes, campana, mes_nombre, programado, real, ajustado, brecha)
        conn.commit()
    finally:
        conn.close()

    return {
        "anio": anio, "mes": mes, "campana": campana, "mes_nombre": mes_nombre,
        "costo_metro_programado": round(programado, 4),
        "costo_metro_real":       round(real, 4),
        "costo_metro_ajustado":   round(ajustado, 4),
        "brecha":                 round(brecha, 4),
        "ultima_carga": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def get_costo_metro_historico(campana: str = 'TOTAL') -> list[dict]:
    try:
        try:
            df = query_df("""
                SELECT anio, mes, mes_nombre, campana,
                       costo_metro_programado, costo_metro_real, costo_metro_ajustado,
                       brecha, fecha_carga
                FROM dbo.CostoMetro
                WHERE campana = ?
                ORDER BY anio, mes
            """, (campana,))
        except Exception:
            # Fallback: tabla sin columna campana
            df = query_df("""
                SELECT anio, mes, mes_nombre,
                       costo_metro_programado, costo_metro_real, costo_metro_ajustado,
                       brecha, fecha_carga
                FROM dbo.CostoMetro ORDER BY anio, mes
            """)
            df['campana'] = 'TOTAL'
        df['fecha_carga'] = df['fecha_carga'].astype(str)
        return df.to_dict('records')
    except Exception:
        return []


def get_costo_metro_ultimo(campana: str = 'TOTAL') -> dict | None:
    try:
        try:
            df = query_df("""
                SELECT TOP 1 anio, mes, mes_nombre, campana,
                             costo_metro_programado, costo_metro_real, costo_metro_ajustado,
                             brecha, fecha_carga
                FROM dbo.CostoMetro
                WHERE campana = ?
                ORDER BY anio DESC, mes DESC
            """, (campana,))
        except Exception:
            df = query_df("""
                SELECT TOP 1 anio, mes, mes_nombre,
                             costo_metro_programado, costo_metro_real, costo_metro_ajustado,
                             brecha, fecha_carga
                FROM dbo.CostoMetro ORDER BY anio DESC, mes DESC
            """)
            if not df.empty:
                df['campana'] = 'TOTAL'
        if df.empty:
            return None
        row = df.iloc[0].to_dict()
        row['fecha_carga'] = str(row['fecha_carga'])
        return row
    except Exception:
        return None


# ── Configuración DPO ─────────────────────────────────────────────────────────

def _ensure_config_table(cursor) -> None:
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='ConfigDPO'
        )
        CREATE TABLE dbo.ConfigDPO (
            clave               NVARCHAR(50)  NOT NULL PRIMARY KEY,
            valor               FLOAT         NOT NULL,
            descripcion         NVARCHAR(200) NOT NULL DEFAULT '',
            fecha_actualizacion DATETIME      NOT NULL DEFAULT GETDATE()
        )
    """)


def upsert_config_dpo(meta_costo_metro: float, meta_tolerancia_brecha: float) -> dict:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_config_table(cursor)
        for clave, valor, desc in [
            ("meta_costo_metro",       meta_costo_metro,
             "Meta de Costo Metro en US$/m"),
            ("meta_tolerancia_brecha", meta_tolerancia_brecha,
             "Tolerancia máxima de brecha (fracción, ej: 0.05 = 5%)"),
        ]:
            cursor.execute("""
                MERGE dbo.ConfigDPO AS t
                USING (SELECT ? AS clave) AS s ON t.clave = s.clave
                WHEN MATCHED     THEN UPDATE SET valor=?, fecha_actualizacion=GETDATE()
                WHEN NOT MATCHED THEN INSERT (clave,valor,descripcion) VALUES (?,?,?);
            """, clave, valor, clave, valor, desc)
        conn.commit()
    finally:
        conn.close()
    return {
        "meta_costo_metro":       meta_costo_metro,
        "meta_tolerancia_brecha": meta_tolerancia_brecha,
        "guardado": datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


def get_config_dpo() -> dict:
    defaults = {"meta_costo_metro": None, "meta_tolerancia_brecha": None}
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        _ensure_config_table(cursor)
        conn.commit()
        conn.close()
        df = query_df("SELECT clave, valor FROM dbo.ConfigDPO")
        for _, row in df.iterrows():
            if row['clave'] in defaults:
                defaults[row['clave']] = float(row['valor'])
    except Exception:
        pass
    return defaults


# ── Proyectos Inicio ─────────────────────────────────────────────────────────

def _ensure_proyectos_inicio_schema(cursor, conn: pyodbc.Connection) -> None:
    cursor.execute("""
        IF NOT EXISTS (
            SELECT 1 FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='ProyectosInicio'
        )
        CREATE TABLE dbo.ProyectosInicio (
            proyecto        NVARCHAR(200) NOT NULL,
            estado_inicio   NVARCHAR(100) NOT NULL,
            mes             INT           NOT NULL,
            anio            INT           NOT NULL,
            fecha_carga     DATETIME      NOT NULL CONSTRAINT DF_PI_fecha DEFAULT GETDATE(),
            CONSTRAINT PK_ProyectosInicio PRIMARY KEY (proyecto, mes, anio)
        )
    """)
    conn.commit()


def upsert_proyectos_inicio(content: bytes) -> dict:
    import io, unicodedata as _uc

    df = pd.read_excel(io.BytesIO(content))
    df.columns = [str(c).strip() for c in df.columns]

    # Normalización insensible a tildes/mayúsculas para encontrar columnas
    # 'Año' → 'ano', 'Estado de inicio' → 'estado de inicio', etc.
    def _n(s: str) -> str:
        return _uc.normalize('NFKD', s).encode('ascii', 'ignore').decode('ascii').lower().strip()

    norm_map = {_n(c): c for c in df.columns}

    col_proyecto = norm_map.get('proyecto')
    col_estado   = norm_map.get('estado de inicio')
    col_mes      = norm_map.get('mes')
    col_anio     = norm_map.get('ano') or norm_map.get('anio') or norm_map.get('a_o')

    faltantes = []
    if not col_proyecto: faltantes.append('Proyecto')
    if not col_estado:   faltantes.append('Estado de inicio')
    if not col_mes:      faltantes.append('Mes')
    if not col_anio:     faltantes.append('Año')
    if faltantes:
        raise ValueError(
            f"Columnas faltantes: {faltantes}. "
            f"Columnas encontradas: {list(df.columns)}"
        )

    df = df.rename(columns={
        col_proyecto: '_proy',
        col_estado:   '_estado',
        col_mes:      '_mes',
        col_anio:     '_anio',
    })
    rows_raw = len(df)

    # Solo descartamos filas sin nombre de proyecto
    df = df.dropna(subset=['_proy'])

    # Mes: acepta número (10) o nombre español ("Agosto", "ago", …)
    _MESES_ES = {
        'enero':1,'ene':1,'febrero':2,'feb':2,'marzo':3,'mar':3,
        'abril':4,'abr':4,'mayo':5,'may':5,'junio':6,'jun':6,
        'julio':7,'jul':7,'agosto':8,'ago':8,
        'septiembre':9,'sep':9,'sept':9,
        'octubre':10,'oct':10,'noviembre':11,'nov':11,
        'diciembre':12,'dic':12,
    }
    def _to_mes(val):
        if pd.isna(val): return None
        n = pd.to_numeric(val, errors='coerce')
        if not pd.isna(n): return int(n)
        s = _uc.normalize('NFKD', str(val)).encode('ascii','ignore').decode('ascii').lower().strip()
        return _MESES_ES.get(s)

    df['_mes']  = df['_mes'].apply(_to_mes)
    df['_anio'] = pd.to_numeric(df['_anio'], errors='coerce')
    df = df.dropna(subset=['_mes', '_anio'])
    df['_mes']    = df['_mes'].astype(int)
    df['_anio']   = df['_anio'].astype(int)
    df['_proy']   = df['_proy'].astype(str).str.strip()
    df['_estado'] = df['_estado'].fillna('').astype(str).str.strip()

    conn   = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_proyectos_inicio_schema(cursor, conn)
        for _, row in df.iterrows():
            cursor.execute("""
                MERGE dbo.ProyectosInicio AS target
                USING (SELECT ? AS proyecto, ? AS mes, ? AS anio) AS src
                  ON target.proyecto = src.proyecto
                 AND target.mes      = src.mes
                 AND target.anio     = src.anio
                WHEN MATCHED THEN
                  UPDATE SET estado_inicio=?, fecha_carga=GETDATE()
                WHEN NOT MATCHED THEN
                  INSERT (proyecto, estado_inicio, mes, anio, fecha_carga)
                  VALUES (?, ?, ?, ?, GETDATE());
            """, row['_proy'], int(row['_mes']), int(row['_anio']),
                 row['_estado'],
                 row['_proy'], row['_estado'], int(row['_mes']), int(row['_anio']))
        conn.commit()
    finally:
        conn.close()

    periodos = sorted({f"{int(r['_mes'])}/{int(r['_anio'])}" for _, r in df.iterrows()})
    return {
        "tabla":            "ProyectosInicio",
        "filas_insertadas": len(df),
        "rows_raw":         rows_raw,
        "periodos":         periodos,
        "ultima_carga":     datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
    }


_KW_POSTERGADO     = ['posterga', 'no inicia', 'sin iniciar', 'reprograma']
_KW_INCUMPLIMIENTO = ['incumplim', 'desfase', 'retraso', 'tardio']
_KW_CUMPLIMIENTO   = ['cumplim', 'a tiempo', 'en fecha', 'segun prog', 'iniciado', 'en plazo']

_MES_ABREV_PI = {1:'Ene',2:'Feb',3:'Mar',4:'Abr',5:'May',6:'Jun',
                 7:'Jul',8:'Ago',9:'Sep',10:'Oct',11:'Nov',12:'Dic'}

def _classify_estado_inicio(estado: str) -> str:
    import unicodedata
    s = unicodedata.normalize('NFKD', str(estado).strip().lower()).encode('ascii', 'ignore').decode('ascii')
    if any(k in s for k in _KW_POSTERGADO):     return 'postergado'
    if any(k in s for k in _KW_INCUMPLIMIENTO): return 'incumplimiento'
    if any(k in s for k in _KW_CUMPLIMIENTO):   return 'cumplimiento'
    return 'otro'


def get_proyectos_inicio_resumen(fecha_desde: str | None = None, fecha_hasta: str | None = None) -> dict:
    empty = {"total": 0, "cumplimiento": 0, "incumplimiento": 0, "postergado": 0,
             "otro": 0, "pct_cumplimiento": 0.0, "periodo": None, "estados": {}}
    try:
        def _ym(s: str) -> int:
            y, m = s.split('-')
            return int(y) * 100 + int(m)

        if fecha_desde or fecha_hasta:
            desde = _ym(fecha_desde) if fecha_desde else 0
            hasta = _ym(fecha_hasta) if fecha_hasta else 999999
            df = query_df(
                """SELECT proyecto, estado_inicio, mes, anio FROM dbo.ProyectosInicio
                   WHERE (anio * 100 + mes) >= ? AND (anio * 100 + mes) <= ?
                   ORDER BY anio, mes""",
                (desde, hasta),
            )
        else:
            df = query_df(
                "SELECT proyecto, estado_inicio, mes, anio FROM dbo.ProyectosInicio ORDER BY anio, mes"
            )
    except Exception:
        return empty
    if df.empty:
        return empty

    df['cat'] = df['estado_inicio'].apply(_classify_estado_inicio)
    counts    = df['cat'].value_counts().to_dict()
    total     = len(df)

    cumplimiento   = int(counts.get('cumplimiento',   0))
    incumplimiento = int(counts.get('incumplimiento', 0))
    postergado     = int(counts.get('postergado',     0))
    otro           = int(counts.get('otro',           0))

    periodos = df[['anio', 'mes']].drop_duplicates().sort_values(['anio', 'mes'])
    first, last = periodos.iloc[0], periodos.iloc[-1]
    lbl_first = f"{_MES_ABREV_PI[int(first['mes'])]} {int(first['anio'])}"
    lbl_last  = f"{_MES_ABREV_PI[int(last['mes'])]}  {int(last['anio'])}"
    periodo   = lbl_first if lbl_first == lbl_last else f"{lbl_first} – {lbl_last}"

    return {
        "total":            total,
        "cumplimiento":     cumplimiento,
        "incumplimiento":   incumplimiento,
        "postergado":       postergado,
        "otro":             otro,
        "pct_cumplimiento": round(cumplimiento / total, 4) if total else 0.0,
        "periodo":          periodo,
        "estados":          {str(k): int(v) for k, v in df['estado_inicio'].value_counts().items()},
    }


# ── Registro de cargas ────────────────────────────────────────────────────────

def log_carga(tabla: str, fuente: str,
              nombre_archivo: str | None = None,
              usuario: str | None = None) -> None:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            IF NOT EXISTS (
                SELECT 1 FROM INFORMATION_SCHEMA.TABLES
                WHERE TABLE_SCHEMA='dbo' AND TABLE_NAME='RegistroCarga'
            )
            CREATE TABLE dbo.RegistroCarga (
                id             INT           IDENTITY(1,1) PRIMARY KEY,
                tabla          NVARCHAR(100) NOT NULL,
                fuente         NVARCHAR(50)  NOT NULL DEFAULT 'Plataforma',
                nombre_archivo NVARCHAR(255) NULL,
                usuario        NVARCHAR(100) NULL,
                fecha_carga    DATETIME      NOT NULL DEFAULT GETDATE()
            )
        """)
        cursor.execute(
            "INSERT INTO dbo.RegistroCarga (tabla, fuente, nombre_archivo, usuario)"
            " VALUES (?,?,?,?)",
            tabla, fuente, nombre_archivo, usuario
        )
        conn.commit()
    finally:
        conn.close()


def get_registro_cargas() -> list[dict]:
    try:
        df = query_df("""
            SELECT id, tabla, fuente, nombre_archivo, usuario, fecha_carga
            FROM dbo.RegistroCarga
            ORDER BY fecha_carga DESC
        """)
        df['fecha_carga'] = df['fecha_carga'].astype(str)
        return df.to_dict('records')
    except Exception:
        return []


def get_costo_metro_meses() -> list[dict]:
    try:
        df = query_df("""
            SELECT anio, mes, MIN(mes_nombre) AS mes_nombre, MAX(fecha_carga) AS fecha_carga
            FROM dbo.CostoMetro
            GROUP BY anio, mes
            ORDER BY anio, mes
        """)
        df['fecha_carga'] = df['fecha_carga'].astype(str)
        return df.to_dict('records')
    except Exception:
        return []


def delete_costo_metro_mes(anio: int, mes: int) -> dict:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "DELETE FROM dbo.CostoMetro WHERE anio=? AND mes=?", anio, mes
        )
        affected = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return {"eliminado": affected > 0, "anio": anio, "mes": mes}


def _get_costo_metro_campanas() -> set[str]:
    """Retorna el conjunto de campañas presentes en CostoMetro (excluye TOTAL y vacíos)."""
    try:
        df = query_df("""
            SELECT DISTINCT campana FROM dbo.CostoMetro
            WHERE campana IS NOT NULL AND campana NOT IN ('TOTAL', '')
        """)
        return set(df['campana'].dropna().tolist())
    except Exception:
        return set()


def get_campanas_disponibles() -> list[str]:
    """
    Retorna las campañas que existen en CostoMetro más la opción 'Otra campaña'
    para proyectos que no pertenecen a ninguna de ellas.
    """
    camps = sorted(_get_costo_metro_campanas())
    if camps:
        camps.append('Otra campaña')
    return camps


def get_proyectos_por_campanas(campanas: list[str]) -> list[str]:
    """
    Retorna códigos id_blanco de proyectos pertenecientes a las campañas indicadas.
    'Otra campaña' mapea a proyectos cuya campaña NO está en CostoMetro.
    """
    if not campanas:
        return []

    regular_camps = [c for c in campanas if c != 'Otra campaña']
    include_otra  = 'Otra campaña' in campanas
    costo_metro_camps = _get_costo_metro_campanas() if include_otra else set()

    result: list[str] = []

    for table in ('dbo.dim_inf_proyecto', 'dbo.dim_proyectos'):
        try:
            df = query_df(f"SELECT * FROM {table}")
            if df.empty:
                continue
            camp_col = next(
                (c for c in df.columns if 'camp' in c.lower().replace('\xf1', 'n')),
                None,
            )
            if not camp_col:
                continue
            id_col = next(
                (c for c in df.columns
                 if 'id_blanco' in c.lower() or c.lower() == 'id_blanco'),
                None,
            )
            if not id_col:
                code_col, _ = _identify_code_name_cols(df)
                id_col = code_col
            if not id_col:
                continue

            camp_series = df[camp_col].fillna('').astype(str).str.strip()

            if regular_camps:
                mask = camp_series.isin(regular_camps)
                result.extend(
                    df[mask][id_col].dropna().astype(str).str.strip()
                    .replace('', float('nan')).dropna().unique().tolist()
                )

            if include_otra:
                # Proyectos cuya campaña no está en ningún mes cargado de CostoMetro
                mask_otra = ~camp_series.isin(costo_metro_camps)
                result.extend(
                    df[mask_otra][id_col].dropna().astype(str).str.strip()
                    .replace('', float('nan')).dropna().unique().tolist()
                )

            if result:
                return list(dict.fromkeys(result))  # deduplica preservando orden
        except Exception:
            continue

    return list(dict.fromkeys(result))


# ── Simulación ─────────────────────────────────────────────────────────────────

_CREATE_SIMULACION = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'dbo' AND t.name = 'Simulacion'
)
BEGIN
    CREATE TABLE dbo.Simulacion (
        id             INT IDENTITY(1,1) NOT NULL,
        nombre         NVARCHAR(200)     NOT NULL,
        usuario        NVARCHAR(100)     NULL,
        fecha_guardado DATETIME          NOT NULL CONSTRAINT DF_Sim_fecha DEFAULT GETDATE(),
        CONSTRAINT PK_Simulacion PRIMARY KEY (id)
    )
END
"""

_CREATE_SIMULACION_LINEA = """
IF NOT EXISTS (
    SELECT 1 FROM sys.tables t JOIN sys.schemas s ON t.schema_id = s.schema_id
    WHERE s.name = 'dbo' AND t.name = 'SimulacionLinea'
)
BEGIN
    CREATE TABLE dbo.SimulacionLinea (
        id                INT IDENTITY(1,1) NOT NULL,
        simulacion_id     INT               NOT NULL,
        proyecto          NVARCHAR(300)     NOT NULL,
        contrato_pyc      NVARCHAR(200)     NOT NULL,
        tipo_contrato     NVARCHAR(200)     NULL,
        contrato_asignado NVARCHAR(200)     NOT NULL,
        tipo_asignacion   NVARCHAR(10)      NOT NULL,
        saldo_inicial     DECIMAL(18,2)     NULL,
        moneda            NVARCHAR(10)      NULL,
        CONSTRAINT PK_SimulacionLinea PRIMARY KEY (id),
        CONSTRAINT FK_SimLinea_Sim FOREIGN KEY (simulacion_id)
            REFERENCES dbo.Simulacion(id) ON DELETE CASCADE
    )
END
"""


def _ensure_sim_tables(conn: pyodbc.Connection) -> None:
    c = conn.cursor()
    c.execute(_CREATE_SIMULACION)
    c.execute(_CREATE_SIMULACION_LINEA)
    conn.commit()


def get_simulaciones() -> list[dict]:
    conn = get_connection()
    try:
        _ensure_sim_tables(conn)
    finally:
        conn.close()
    try:
        df = query_df("""
            SELECT s.id, s.nombre, s.usuario,
                   FORMAT(s.fecha_guardado, 'yyyy-MM-dd HH:mm') AS fecha_guardado,
                   COUNT(l.id) AS num_lineas
            FROM dbo.Simulacion s
            LEFT JOIN dbo.SimulacionLinea l ON l.simulacion_id = s.id
            GROUP BY s.id, s.nombre, s.usuario, s.fecha_guardado
            ORDER BY s.fecha_guardado DESC
        """)
        return df.fillna('').to_dict('records')
    except Exception:
        return []


def get_simulacion_by_id(sim_id: int) -> dict | None:
    sims = query_df("SELECT id, nombre, usuario FROM dbo.Simulacion WHERE id=?", (sim_id,))
    if sims.empty:
        return None
    sim = sims.iloc[0].fillna('').to_dict()
    lineas = query_df(
        "SELECT proyecto, contrato_pyc AS alcance, contrato_asignado, "
        "tipo_asignacion, CAST(saldo_inicial AS FLOAT) AS saldo_inicial, moneda "
        "FROM dbo.SimulacionLinea WHERE simulacion_id=?",
        (sim_id,)
    )
    sim['lineas'] = lineas.fillna('').to_dict('records')
    return sim


def save_simulacion(nombre: str, usuario: str | None, lineas: list[dict], sim_id: int | None = None) -> dict:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        _ensure_sim_tables(conn)
        if sim_id:
            cursor.execute(
                "UPDATE dbo.Simulacion SET nombre=?, usuario=?, fecha_guardado=GETDATE() WHERE id=?",
                nombre, usuario, sim_id
            )
            cursor.execute("DELETE FROM dbo.SimulacionLinea WHERE simulacion_id=?", sim_id)
            new_id = sim_id
        else:
            cursor.execute(
                "INSERT INTO dbo.Simulacion (nombre, usuario) OUTPUT INSERTED.id VALUES (?,?)",
                nombre, usuario
            )
            new_id = cursor.fetchone()[0]

        for l in lineas:
            # contrato_pyc column reused to store alcance (dupla key)
            cursor.execute(
                "INSERT INTO dbo.SimulacionLinea "
                "(simulacion_id, proyecto, contrato_pyc, "
                " contrato_asignado, tipo_asignacion, saldo_inicial, moneda) "
                "VALUES (?,?,?,?,?,?,?)",
                new_id, l['proyecto'], l.get('alcance', ''),
                l['contrato_asignado'], l['tipo_asignacion'],
                l.get('saldo_inicial'), l.get('moneda', 'CLP'),
            )
        conn.commit()
        return {'id': int(new_id), 'ok': True}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def delete_simulacion(sim_id: int) -> dict:
    conn   = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM dbo.Simulacion WHERE id=?", sim_id)
        conn.commit()
        return {'ok': True}
    finally:
        conn.close()


def _identify_code_name_cols(df: pd.DataFrame) -> tuple[str | None, str | None]:
    """
    Identifica columna código y columna nombre por contenido:
    - código: valores solo alfanuméricos (sin espacios ni guiones ni tildes)
    - nombre: valores con espacios, guiones, tildes u otros caracteres especiales
    """
    str_cols = [c for c in df.columns if df[c].dtype == object]
    if len(str_cols) < 2:
        return None, None

    def _pct_code(col: str) -> float:
        vals = df[col].dropna().astype(str).str.strip()
        vals = vals[vals != '']
        return float(vals.str.match(r'^[A-Za-z0-9_]+$').mean()) if not vals.empty else 0.0

    def _pct_name(col: str) -> float:
        vals = df[col].dropna().astype(str).str.strip()
        vals = vals[vals != '']
        return float(vals.str.contains(r'[\s\-áéíóúÁÉÍÓÚñÑüÜ,\(\)\./]', regex=True, na=False).mean()) if not vals.empty else 0.0

    code_col = max(str_cols, key=_pct_code)
    name_col = max((c for c in str_cols if c != code_col), key=_pct_name, default=None)

    if not name_col or _pct_code(code_col) < 0.5 or _pct_name(name_col) < 0.2:
        return None, None

    return code_col, name_col


def _dim_proyecto_nombres() -> dict[str, str]:
    """Obtiene {id_blanco: nombre} identificando columnas por contenido."""
    for table in ('dbo.dim_inf_proyecto', 'dbo.dim_proyectos'):
        try:
            df = query_df(f"SELECT * FROM {table}")
            if df.empty:
                continue
            code_col, name_col = _identify_code_name_cols(df)
            if code_col and name_col:
                mapping = df.set_index(code_col)[name_col].dropna()
                mapping = mapping[mapping.astype(str).str.strip() != '']
                if not mapping.empty:
                    return mapping.to_dict()
        except Exception:
            continue
    return {}


def get_sim_proyectos() -> dict:
    try:
        df = query_df(
            "SELECT DISTINCT proyecto FROM dbo.proyecciones "
            "WHERE proyecto IS NOT NULL AND proyecto != '' ORDER BY proyecto"
        )
        codigos: list[str] = df['proyecto'].dropna().tolist()
    except Exception:
        return {'proyectos': [], 'has_nombres': False}

    # nombres primarios desde dim_inf_proyecto (nom_resumen)
    nombre_map = _dim_proyecto_nombres()

    # fallback: sourcing_plan.id_blanco → sourcing_plan.proyecto (nombre)
    sourcing_nombre_map: dict[str, str] = {}
    try:
        sp = query_df(
            "SELECT id_blanco, MAX(proyecto) AS nombre "
            "FROM dbo.sourcing_plan "
            "WHERE id_blanco IS NOT NULL AND id_blanco != '' "
            "GROUP BY id_blanco"
        )
        sourcing_nombre_map = sp.set_index('id_blanco')['nombre'].dropna().to_dict()
    except Exception:
        pass

    proyectos = []
    for cod in codigos:
        nombre = nombre_map.get(cod, '') or sourcing_nombre_map.get(cod, '')
        proyectos.append({
            'codigo':    cod,
            'nombre':    nombre,
            'id_blanco': cod,   # en proyecciones, proyecto ya es el id_blanco
        })

    has_nombres = any(p['nombre'] and p['nombre'] != p['codigo'] for p in proyectos)
    return {
        'proyectos':   proyectos,
        'has_nombres': has_nombres,
    }


def get_sim_vendor_codes(proyectos: list[str]) -> list[dict]:
    """Retorna una fila por dupla (proyecto, alcance) con periodos agregados."""
    if not proyectos:
        return []
    ph = ','.join(['?'] * len(proyectos))
    # Get per-contract-period rows with alcance
    df = query_df(
        f"""
        SELECT p.contrato, p.proyecto, p.periodo,
               SUM(p.monto_proyectado) AS monto,
               MAX(p.moneda)           AS moneda,
               COALESCE(MAX(s.alcance), '')  AS alcance
        FROM dbo.proyecciones p
        LEFT JOIN dbo.sourcing_plan s ON s.contrato = p.contrato
        WHERE p.proyecto IN ({ph})
        GROUP BY p.contrato, p.proyecto, p.periodo
        """,
        tuple(proyectos)
    )
    if df.empty:
        return []

    df['alcance'] = df['alcance'].fillna('').astype(str)
    df['moneda']  = df['moneda'].fillna('CLP').astype(str)

    result = []
    # Group by (proyecto, alcance) — one row per dupla
    for (proyecto_val, alcance_val), grp in df.groupby(['proyecto', 'alcance'], sort=True):
        # Aggregate periods across all contracts in this dupla
        period_sums = grp.groupby('periodo')['monto'].sum().reset_index()
        periodos = sorted(
            [{'periodo': str(r['periodo']), 'monto': float(r['monto'] or 0)}
             for _, r in period_sums.iterrows()],
            key=lambda x: x['periodo']
        )
        contratos_pyc = sorted(grp['contrato'].astype(str).unique().tolist())
        moneda = str(grp['moneda'].iloc[0])

        result.append({
            'proyecto':         str(proyecto_val),
            'alcance':          str(alcance_val),
            'contratos_pyc':    contratos_pyc,
            'moneda':           moneda,
            'total_proyectado': float(grp['monto'].sum()),
            'periodo_inicio':   periodos[0]['periodo']  if periodos else '',
            'periodo_fin':      periodos[-1]['periodo'] if periodos else '',
            'periodos':         periodos,
        })
    return result


def get_sim_contratos() -> dict:
    import json as _json
    from app.metrics.calculations import saldo_por_contrato
    from app.data.loader import load_solpe

    sic = saldo_por_contrato()
    sic_cols = [c for c in ['codigo_ctt', 'Descripcion_ctt', 'Tipo_contrato',
                             'Saldo_CLP', 'Presupuesto_CLP', 'Estado_ctt'] if c in sic.columns]
    sic_df = sic[sic_cols].copy()
    for col in ('Saldo_CLP', 'Presupuesto_CLP'):
        if col in sic_df.columns:
            sic_df[col] = pd.to_numeric(sic_df[col], errors='coerce').fillna(0)
    sic_list = _json.loads(sic_df.to_json(orient='records'))

    solpe = load_solpe()
    solpe_cols = [c for c in ['Código', 'Nombre', 'Tipo', 'Monto', 'Moneda',
                               'Estado_SOLPE', 'Grupo_Estado'] if c in solpe.columns]
    solpe_df = solpe[solpe_cols].copy()
    if 'Monto' in solpe_df.columns:
        solpe_df['Monto'] = pd.to_numeric(solpe_df['Monto'], errors='coerce').fillna(0)
    solpe_list = _json.loads(solpe_df.to_json(orient='records'))

    return {'sic': sic_list, 'solpes': solpe_list}
