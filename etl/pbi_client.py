"""
Cliente Power BI REST API — sin registro de app en Azure AD.

Usa el Client ID público de Power BI Desktop (ea0616a9-...).
Flujo ROPC (usuario/contraseña) o Device Code si hay MFA.
"""

import json, os, warnings
import requests, msal, urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
warnings.filterwarnings("ignore", message="Unverified HTTPS request")

def _session() -> requests.Session:
    s = requests.Session()
    s.verify = False
    return s
from config_etl import (
    TENANT_ID, PBI_USERNAME, PBI_PASSWORD, TOKEN_CACHE_FILE,
    PBI_WORKSPACE_ID, PBI_DATASET_ID, PBI_TABLE_NAME, LOAD_MODE, MONTHS_BACK,
)

# Client ID público de Power BI Desktop — no requiere registro en Azure AD
PBI_DESKTOP_CLIENT_ID = "ea0616a9-be27-4c11-a2ea-2f60f13a91d7"
AUTHORITY  = f"https://login.microsoftonline.com/{TENANT_ID}"
SCOPES     = ["https://analysis.windows.net/powerbi/api/Dataset.Read.All"]
PBI_API    = "https://api.powerbi.com/v1.0/myorg"


# ── Autenticación ─────────────────────────────────────────────────────────────

def _load_cache() -> msal.SerializableTokenCache:
    cache = msal.SerializableTokenCache()
    if os.path.exists(TOKEN_CACHE_FILE):
        with open(TOKEN_CACHE_FILE, "r") as f:
            cache.deserialize(f.read())
    return cache


def _save_cache(cache: msal.SerializableTokenCache):
    if cache.has_state_changed:
        with open(TOKEN_CACHE_FILE, "w") as f:
            f.write(cache.serialize())


def get_access_token() -> str:
    cache = _load_cache()
    app = msal.PublicClientApplication(
        PBI_DESKTOP_CLIENT_ID,
        authority=AUTHORITY,
        token_cache=cache,
        http_client=_session(),
    )

    # 1. Token silencioso desde caché (no requiere login)
    accounts = app.get_accounts()
    if accounts:
        result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if result and "access_token" in result:
            _save_cache(cache)
            return result["access_token"]

    # 2. Usuario + contraseña (ROPC) — funciona si no hay MFA obligatorio
    if PBI_USERNAME and PBI_PASSWORD and "tu_usuario" not in PBI_USERNAME:
        result = app.acquire_token_by_username_password(
            username=PBI_USERNAME,
            password=PBI_PASSWORD,
            scopes=SCOPES,
        )
        if "access_token" in result:
            _save_cache(cache)
            print("Autenticado con usuario/contraseña")
            return result["access_token"]

        # MFA activo o credenciales incorrectas → caer al Device Code
        err = result.get("error_description", result.get("error", ""))
        if "MFA" in err or "AADSTS50076" in err or "AADSTS50079" in err:
            print("MFA detectado, usando Device Code Flow...")
        else:
            print(f"Error credenciales: {err}")
            print("Intentando Device Code Flow...")

    # 3. Device Code Flow — abre URL en el navegador (compatible con MFA)
    flow = app.initiate_device_flow(scopes=SCOPES)
    if "user_code" not in flow:
        raise RuntimeError(f"Error iniciando device flow: {flow}")

    print("\n" + "=" * 60)
    print("AUTENTICACION REQUERIDA")
    print(f"  1. Abre este URL en tu navegador:")
    print(f"     {flow['verification_uri']}")
    print(f"  2. Ingresa el codigo: {flow['user_code']}")
    print(f"  3. Inicia sesion con tu cuenta de Microsoft")
    print("=" * 60 + "\n")

    result = app.acquire_token_by_device_flow(flow)
    if "access_token" not in result:
        raise RuntimeError(f"Error autenticando: {result.get('error_description', result)}")

    _save_cache(cache)
    print("Autenticacion exitosa (token guardado para proximas ejecuciones)")
    return result["access_token"]


# ── Power BI REST API ─────────────────────────────────────────────────────────

def _hdr(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def list_workspaces(token: str) -> list[dict]:
    r = _session().get(f"{PBI_API}/groups", headers=_hdr(token))
    r.raise_for_status()
    return r.json().get("value", [])


def list_datasets(token: str, workspace_id: str) -> list[dict]:
    r = _session().get(f"{PBI_API}/groups/{workspace_id}/datasets", headers=_hdr(token))
    r.raise_for_status()
    return r.json().get("value", [])


def list_tables(token: str, workspace_id: str, dataset_id: str) -> list[dict]:
    """Lista tablas del dataset (útil para encontrar el nombre correcto)."""
    r = _session().get(
        f"{PBI_API}/groups/{workspace_id}/datasets/{dataset_id}/tables",
        headers=_hdr(token),
    )
    if r.status_code == 200:
        return r.json().get("value", [])
    return []


def execute_dax(token: str, dax: str) -> list[dict]:
    url  = f"{PBI_API}/groups/{PBI_WORKSPACE_ID}/datasets/{PBI_DATASET_ID}/executeQueries"
    body = {
        "queries": [{"query": dax}],
        "serializerSettings": {"includeNulls": True},
    }
    r = _session().post(url, headers=_hdr(token), json=body)
    if r.status_code != 200:
        raise RuntimeError(f"Error DAX {r.status_code}: {r.text[:600]}")

    rows = r.json()["results"][0]["tables"][0]["rows"]
    # Normalizar "NombreTabla[columna]" → "columna"
    return [
        {(k.split("[")[1].rstrip("]") if "[" in k else k): v for k, v in row.items()}
        for row in rows
    ]


# ── Extracción principal ──────────────────────────────────────────────────────

def fetch_proyecciones() -> list[dict]:
    token = get_access_token()

    if LOAD_MODE == "full":
        dax = f"EVALUATE '{PBI_TABLE_NAME}'"
    else:
        from dateutil.relativedelta import relativedelta
        from datetime import datetime
        cutoff = (datetime.today() - relativedelta(months=MONTHS_BACK)).strftime("%Y-%m")
        dax = f"""
        EVALUATE
        FILTER(
            '{PBI_TABLE_NAME}',
            '{PBI_TABLE_NAME}'[periodo] >= \"{cutoff}\"
        )
        """

    print(f"Ejecutando DAX sobre {PBI_DATASET_ID}...")
    rows = execute_dax(token, dax)
    print(f"  {len(rows):,} filas obtenidas")
    return rows


# ── Descubrimiento ────────────────────────────────────────────────────────────

def descubrir():
    """
    Muestra workspaces, datasets y tablas disponibles.
    Ejecutar: python etl_proyecciones.py --discover
    """
    token = get_access_token()

    print("\nWorkspaces disponibles:")
    for w in list_workspaces(token):
        print(f"  {w['id']}  ->  {w['name']}")

    if "TU_" not in PBI_WORKSPACE_ID:
        print(f"\nDatasets en workspace {PBI_WORKSPACE_ID}:")
        for d in list_datasets(token, PBI_WORKSPACE_ID):
            print(f"  {d['id']}  ->  {d['name']}")

    if "TU_" not in PBI_WORKSPACE_ID and "TU_" not in PBI_DATASET_ID:
        print(f"\nTablas en dataset {PBI_DATASET_ID}:")
        for t in list_tables(token, PBI_WORKSPACE_ID, PBI_DATASET_ID):
            print(f"  {t['name']}")
