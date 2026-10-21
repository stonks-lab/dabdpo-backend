"""
Configuración ETL — copiar este archivo como config_etl.py y completar los valores.
NO subir config_etl.py a git (está en .gitignore).
"""

# ── Credenciales Microsoft 365 ──────────────────────────────────────────────
PBI_USERNAME = "usuario@empresa.cl"
PBI_PASSWORD = "TU_PASSWORD"

TENANT_ID = "organizations"

# ── IDs de Power BI ─────────────────────────────────────────────────────────
PBI_WORKSPACE_ID = "TU_WORKSPACE_ID"
PBI_DATASET_ID   = "TU_DATASET_ID"
PBI_TABLE_NAME   = "proyecciones_gastos"

# ── SQL Server destino ──────────────────────────────────────────────────────
SQL_SERVER   = "IP_O_HOSTNAME"
SQL_DATABASE = "DAB"
SQL_AUTH     = "sql"
SQL_USER     = "DAB_user"
SQL_PASSWORD = "TU_PASSWORD"
SQL_SCHEMA   = "dbo"
SQL_TABLE    = "proyecciones"

# ── Comportamiento ──────────────────────────────────────────────────────────
LOAD_MODE        = "full"
MONTHS_BACK      = 6
TOKEN_CACHE_FILE = ".pbi_token_cache"

# ── SharePoint / Sourcing Plan ───────────────────────────────────────────────
SP_LOCAL_PATH      = r"C:\Users\TU_USUARIO\Codelco\Planificación y P&C - BBDD Planificación servicios\Sourcingplan_actualizado.xlsx"
SP_SHEET_NAME      = "Plan"
SP_HEADER_ROW      = 2
SP_SQL_TABLE       = "sourcing_plan"
SP_COL_PROYECTO    = "Proyecto"
SP_COL_CONTRATO    = "Contrato"
SP_COL_PERIODO_INI = "Período_Inicio"
SP_COL_PERIODO_FIN = "Período_Fin"
