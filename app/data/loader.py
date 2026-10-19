import pandas as pd
from functools import lru_cache
from app.config import EXCEL_FILES


def _read(key: str, **kwargs) -> pd.DataFrame:
    return pd.read_excel(EXCEL_FILES[key], **kwargs)


@lru_cache(maxsize=None)
def load_ep() -> pd.DataFrame:
    df = _read("ep_detalle")
    df.columns = [c.strip() for c in df.columns]

    # Normalise AÑO column (encoding may vary)
    year_col = next((c for c in df.columns if c.upper().startswith("A") and "O" in c.upper()), None)
    if year_col and year_col != "AÑO":
        df.rename(columns={year_col: "AÑO"}, inplace=True)

    df["monto_CLP"] = df.apply(
        lambda r: r["monto_consumido"]
        if (pd.isna(r["ExchageRate"]) or r["ExchageRate"] == 0)
        else r["monto_consumido"] * r["ExchageRate"],
        axis=1,
    )

    mes_map = {
        "enero": 1, "febrero": 2, "marzo": 3, "abril": 4,
        "mayo": 5, "junio": 6, "julio": 7, "agosto": 8,
        "septiembre": 9, "octubre": 10, "noviembre": 11, "diciembre": 12,
    }
    df["mes_num"] = df["MES"].str.lower().map(mes_map)
    df["Fecha_Periodo"] = pd.to_datetime(
        df["AÑO"].astype(str) + "-" + df["mes_num"].astype(str) + "-01",
        errors="coerce",
    )
    return df


@lru_cache(maxsize=None)
def load_itemizado() -> pd.DataFrame:
    df = _read("itemizado")
    df.columns = [c.strip() for c in df.columns]
    df["MONEDA"] = df["MONEDA"].str.strip()
    return df


@lru_cache(maxsize=None)
def load_solpe() -> pd.DataFrame:
    df = _read("solpe")
    df.columns = [c.strip() for c in df.columns]

    # Normalize column names with encoding issues
    rename = {}
    for c in df.columns:
        cu = c.upper().replace(" ", "_")
        if "DIGO" in cu or "ODIGO" in cu:
            rename[c] = "Código"
        elif "CREACI" in cu and "SOLPE" in cu:
            rename[c] = "Fecha_Creacion_SOLPE"
        elif "ADJUDICA" in cu:
            rename[c] = "Fecha_Adjudicacion"
        elif "SUSCRIPCI" in cu:
            rename[c] = "Fecha_Suscripcion"
    df.rename(columns=rename, inplace=True)

    now = pd.Timestamp.now()
    df["Dias_Gestion"] = df.apply(
        lambda r: (
            (r["Fecha_Adjudicacion"] - r["Fecha_Creacion_SOLPE"]).days
            if pd.notna(r.get("Fecha_Adjudicacion"))
            else (now - r["Fecha_Creacion_SOLPE"]).days
            if pd.notna(r.get("Fecha_Creacion_SOLPE"))
            else None
        ),
        axis=1,
    )

    estado_col = next((c for c in df.columns if "ESTADO" in c.upper() and "SOLPE" in c.upper()), None)
    if estado_col:
        df.rename(columns={estado_col: "Estado_SOLPE"}, inplace=True)
    elif "Estado Actual de la SOLPE" in df.columns:
        df.rename(columns={"Estado Actual de la SOLPE": "Estado_SOLPE"}, inplace=True)

    def grupo(estado):
        if pd.isna(estado):
            return "En Proceso"
        e = str(estado)
        if e in ("Cerrada", "Aplicado"):
            return "Cerrada"
        if e == "Rechazada":
            return "Rechazada"
        if e == "Aceptada":
            return "Aceptada"
        if e == "Enviado a PyC":
            return "Enviado a PyC"
        if e in ("Gestionada", "En Gestión") or e.startswith("Enviada"):
            return "En Proceso"
        return "En Proceso"

    df["Grupo_Estado"] = df["Estado_SOLPE"].apply(grupo)
    return df


@lru_cache(maxsize=None)
def load_boletas() -> pd.DataFrame:
    df = _read("boletas")
    df.columns = [c.strip() for c in df.columns]

    # Normalize column names
    rename = {}
    for c in df.columns:
        cu = c.upper()
        if "ESTADO" in cu and "GARANT" in cu:
            rename[c] = "Estado_Garantia"
        elif "VENCIMIENTO" in cu and "FECHA" in cu and "PROYECTADA" not in cu and "TÉRMINO" not in cu:
            rename[c] = "Fecha_Vencimiento"
        elif "AÑO" in cu or ("A" in cu and "O" in cu and len(c) <= 4):
            rename[c] = "Año"
    df.rename(columns=rename, inplace=True)

    if "Estado_Garantia" not in df.columns:
        # fallback: find by partial match
        col = next((c for c in df.columns if "garantia" in c.lower() or "garant" in c.lower()), None)
        if col:
            df.rename(columns={col: "Estado_Garantia"}, inplace=True)

    now = pd.Timestamp.now().normalize()
    if "Fecha_Vencimiento" in df.columns:
        df["Fecha_Vencimiento"] = pd.to_datetime(df["Fecha_Vencimiento"], errors="coerce")
        df["Dias_Para_Vencer"] = (df["Fecha_Vencimiento"] - now).dt.days

        def alerta(row):
            if row.get("Estado_Garantia") != "Vigente":
                return "N/A"
            d = row.get("Dias_Para_Vencer")
            if pd.isna(d):
                return "Sin Fecha"
            if d < 0:
                return "VENCIDA"
            if d <= 30:
                return "CRÍTICO"
            if d <= 60:
                return "ADVERTENCIA"
            if d <= 90:
                return "ATENCIÓN"
            return "OK"

        df["Alerta_Vencimiento"] = df.apply(alerta, axis=1)
    return df


def invalidate_cache():
    load_ep.cache_clear()
    load_itemizado.cache_clear()
    load_solpe.cache_clear()
    load_boletas.cache_clear()
