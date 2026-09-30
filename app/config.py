import os
from pathlib import Path
from dotenv import load_dotenv

# Carga .env si existe (desarrollo local)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

BASE_DIR  = Path(__file__).resolve().parent.parent.parent.parent
EXCEL_DIR = BASE_DIR / "API-SIC"

EXCEL_FILES = {
    "ep_detalle": EXCEL_DIR / "ep_detalle.xlsx",
    "itemizado":  EXCEL_DIR / "itemizado (2).xlsx",
    "solpe":      EXCEL_DIR / "solpe (4).xlsx",
    "boletas":    EXCEL_DIR / "boletas.xlsx",
}

# ── SQL Server ──────────────────────────────────────────────────────────────
SQL_SERVER   = os.environ["SQL_SERVER"]
SQL_DATABASE = os.environ["SQL_DATABASE"]
SQL_AUTH     = os.environ.get("SQL_AUTH", "sql")
SQL_USER     = os.environ.get("SQL_USER", "")
SQL_PASSWORD = os.environ.get("SQL_PASSWORD", "")

# Credenciales secundarias (lectura tablas avance/PAI)
SQL_BI_USER     = os.environ.get("SQL_BI_USER", "")
SQL_BI_PASSWORD = os.environ.get("SQL_BI_PASSWORD", "")

SQL_PROYECCIONES_ENABLED = True
SQL_SOURCING_ENABLED     = True
