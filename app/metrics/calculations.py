import pandas as pd
from functools import lru_cache
from app.data.loader import load_ep, load_itemizado, load_solpe, load_boletas


# ── Exchange rates ─────────────────────────────────────────────────────────────

def _fx_rates() -> dict:
    ep = load_ep()
    valid = ep[ep["ExchageRate"] > 1]["ExchageRate"]
    avg = float(valid.mean()) if len(valid) else 950.0
    return {"USD": avg, "UF": 38000.0, "AUD": avg * 0.62, "CAD": avg * 0.74, "EUR": avg * 1.08}


def _item_total_clp(df: pd.DataFrame) -> pd.Series:
    fx = _fx_rates()
    return df.apply(
        lambda r: r["TOTAL"] * fx.get(r["MONEDA"], 1.0),
        axis=1,
    )


# ── Saldo de contratos ─────────────────────────────────────────────────────────

def saldo_por_contrato() -> pd.DataFrame:
    ep   = load_ep()
    item = load_itemizado()

    item2 = item.copy()
    item2["TOTAL_CLP"] = _item_total_clp(item2)

    ppto = item2.groupby("CODEMSA")["TOTAL_CLP"].sum().reset_index()
    ppto.columns = ["codigo_ctt", "Presupuesto_CLP"]

    ep_sum = ep.groupby("codigo_ctt")["monto_CLP"].sum().reset_index()
    ep_sum.columns = ["codigo_ctt", "EP_Consumido_CLP"]

    merged = ppto.merge(ep_sum, on="codigo_ctt", how="left")
    merged["EP_Consumido_CLP"] = merged["EP_Consumido_CLP"].fillna(0)
    merged["Saldo_CLP"] = merged["Presupuesto_CLP"] - merged["EP_Consumido_CLP"]
    merged["Pct_Consumido"] = (merged["EP_Consumido_CLP"] / merged["Presupuesto_CLP"]).fillna(0)
    merged["Pct_Saldo"] = 1 - merged["Pct_Consumido"]

    def estado(pct):
        if pct < 0:      return "NEGATIVO"
        if pct < 0.10:   return "CRÍTICO"
        if pct < 0.25:   return "ADVERTENCIA"
        if pct < 0.40:   return "ATENCIÓN"
        return "OK"

    merged["Estado_Saldo"] = merged["Pct_Saldo"].apply(estado)

    # Add contract attributes from EP
    attrs = ep[["codigo_ctt", "Descripcion_ctt", "Tipo_contrato",
                "Area_contrato", "Estado_ctt"]].drop_duplicates("codigo_ctt")
    merged = merged.merge(attrs, on="codigo_ctt", how="left")
    return merged


def home_kpis() -> dict:
    ep   = load_ep()
    item = load_itemizado()
    sol  = load_solpe()
    bol  = load_boletas()

    item2 = item.copy()
    item2["TOTAL_CLP"] = _item_total_clp(item2)
    presupuesto = float(item2["TOTAL_CLP"].sum())
    consumido   = float(ep["monto_CLP"].sum())
    saldo       = presupuesto - consumido
    pct         = consumido / presupuesto if presupuesto else 0

    saldo_df = saldo_por_contrato()
    criticos    = int((saldo_df["Estado_Saldo"] == "CRÍTICO").sum())
    advertencia = int((saldo_df["Estado_Saldo"] == "ADVERTENCIA").sum())
    negativos   = int((saldo_df["Estado_Saldo"] == "NEGATIVO").sum())

    solpes_activas = int((sol["Grupo_Estado"] == "En Proceso").sum()) if len(sol) else 0

    bol_criticas = 0
    if "Alerta_Vencimiento" in bol.columns:
        bol_criticas = int((bol["Alerta_Vencimiento"] == "CRÍTICO").sum())

    semaforo = (
        "ROJO"     if criticos >= 5 or negativos >= 1 else
        "NARANJA"  if bol_criticas >= 3 else
        "AMARILLO" if advertencia >= 5 else
        "VERDE"
    )

    return {
        "presupuesto_total_clp": presupuesto,
        "ep_consumido_clp":      consumido,
        "saldo_real_clp":        saldo,
        "pct_consumido":         round(pct, 4),
        "contratos_totales":     int(item["CODEMSA"].nunique()),
        "contratos_con_ep":      int(ep["codigo_ctt"].nunique()),
        "contratos_criticos":    criticos,
        "contratos_advertencia": advertencia,
        "contratos_negativos":   negativos,
        "solpes_activas":        solpes_activas,
        "boletas_criticas":      bol_criticas,
        "semaforo":              semaforo,
    }


def ep_por_mes() -> list[dict]:
    ep = load_ep()
    grp = (
        ep.groupby("Fecha_Periodo")["monto_CLP"]
        .sum()
        .reset_index()
        .sort_values("Fecha_Periodo")
    )
    grp["Fecha_Periodo"] = grp["Fecha_Periodo"].dt.strftime("%Y-%m")
    return grp.rename(columns={"Fecha_Periodo": "periodo", "monto_CLP": "monto"}).to_dict("records")


def ep_por_tipo_contrato() -> list[dict]:
    ep = load_ep()
    grp = ep.groupby("Tipo_contrato")["monto_CLP"].sum().reset_index()
    grp.columns = ["tipo", "monto"]
    return grp.sort_values("monto", ascending=False).to_dict("records")


# ── Licitaciones ───────────────────────────────────────────────────────────────

def licitaciones_kpis() -> dict:
    sol = load_solpe()
    total     = len(sol)
    en_proc   = int((sol["Grupo_Estado"] == "En Proceso").sum())
    cerradas  = int((sol["Grupo_Estado"] == "Cerrada").sum())
    rechaz    = int((sol["Grupo_Estado"] == "Rechazada").sum())
    iniciadas = int((sol["Grupo_Estado"] == "Iniciada").sum())

    dias_prom = None
    cerr = sol[sol["Grupo_Estado"] == "Cerrada"]["Dias_Gestion"].dropna()
    cerr = cerr[cerr > 0]
    if len(cerr):
        dias_prom = round(float(cerr.mean()), 1)

    monto_col = next((c for c in sol.columns if c == "Monto"), None)
    monto_en_gestion = 0.0
    if monto_col:
        monto_en_gestion = float(sol[sol["Grupo_Estado"] == "En Proceso"]["Monto"].fillna(0).sum())

    tasa = round(cerradas / (total - rechaz), 4) if (total - rechaz) > 0 else 0

    return {
        "total": total,
        "en_proceso": en_proc,
        "cerradas": cerradas,
        "rechazadas": rechaz,
        "iniciadas": iniciadas,
        "dias_promedio_gestion": dias_prom,
        "monto_en_gestion": monto_en_gestion,
        "tasa_adjudicacion": tasa,
    }


def solpes_por_estado() -> list[dict]:
    sol = load_solpe()
    grp = sol.groupby("Grupo_Estado").size().reset_index(name="cantidad")
    return grp.to_dict("records")


def solpes_por_anio() -> list[dict]:
    sol = load_solpe()
    if "Fecha_Creacion_SOLPE" not in sol.columns:
        return []
    sol2 = sol.copy()
    sol2["Año"] = pd.to_datetime(sol2["Fecha_Creacion_SOLPE"], errors="coerce").dt.year
    grp = sol2.groupby(["Año", "Grupo_Estado"]).size().reset_index(name="cantidad")
    return grp.dropna(subset=["Año"]).astype({"Año": int}).to_dict("records")


# ── Alertas ────────────────────────────────────────────────────────────────────

def alertas_contratos() -> list[dict]:
    saldo = saldo_por_contrato()
    criticos = saldo[saldo["Estado_Saldo"].isin(["CRÍTICO", "NEGATIVO", "ADVERTENCIA"])]
    cols = ["codigo_ctt", "Descripcion_ctt", "Tipo_contrato", "Area_contrato",
            "Estado_ctt", "Presupuesto_CLP", "EP_Consumido_CLP", "Saldo_CLP",
            "Pct_Saldo", "Estado_Saldo"]
    return criticos[cols].sort_values("Pct_Saldo").to_dict("records")


def analisis_kpis() -> dict:
    ep = load_ep()
    proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
    n_proyectos = int(ep[proj_col].dropna().nunique()) if proj_col else 0
    return {
        "proyectos_con_ep":  n_proyectos,
        "ep_total_clp":      float(ep["monto_CLP"].sum()),
        "contratos_con_ep":  int(ep["codigo_ctt"].nunique()),
    }


def analisis_por_proyecto() -> list[dict]:
    ep = load_ep()
    proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
    if not proj_col:
        return []
    grp = (
        ep.dropna(subset=[proj_col])
        .groupby(proj_col)
        .agg(
            EP_Consumido_CLP=pd.NamedAgg(column="monto_CLP", aggfunc="sum"),
            Num_Contratos=pd.NamedAgg(column="codigo_ctt", aggfunc="nunique"),
            Num_EPs=pd.NamedAgg(column="monto_CLP", aggfunc="count"),
        )
        .reset_index()
    )
    grp.columns = ["Proyecto", "EP_Consumido_CLP", "Num_Contratos", "Num_EPs"]
    return grp.sort_values("EP_Consumido_CLP", ascending=False).to_dict("records")


def analisis_contratos_por_proyecto(proyecto: str | None = None) -> list[dict]:
    ep = load_ep()
    proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
    if not proj_col:
        return []
    df = ep.dropna(subset=[proj_col])
    if proyecto:
        df = df[df[proj_col] == proyecto]
    grp = (
        df.groupby(["codigo_ctt", proj_col])
        .agg(
            EP_Consumido_CLP=pd.NamedAgg(column="monto_CLP", aggfunc="sum"),
            Num_EPs=pd.NamedAgg(column="monto_CLP", aggfunc="count"),
        )
        .reset_index()
    )
    grp.columns = ["codigo_ctt", "Proyecto", "EP_Consumido_CLP", "Num_EPs"]
    attrs = ep[["codigo_ctt", "Descripcion_ctt", "Tipo_contrato",
                "Area_contrato", "Estado_ctt"]].drop_duplicates("codigo_ctt")
    merged = grp.merge(attrs, on="codigo_ctt", how="left")
    return (
        merged.sort_values(["Proyecto", "EP_Consumido_CLP"], ascending=[True, False])
        .fillna("")
        .to_dict("records")
    )


def alertas_boletas() -> list[dict]:
    bol = load_boletas()
    if "Alerta_Vencimiento" not in bol.columns:
        return []
    alertas = bol[bol["Alerta_Vencimiento"].isin(["CRÍTICO", "ADVERTENCIA", "ATENCIÓN", "VENCIDA"])]
    cols = [c for c in ["Contrato", "Nro Boleta", "Servicio", "Proveedor", "Monto", "Moneda",
                         "Fecha_Vencimiento", "Dias_Para_Vencer", "Alerta_Vencimiento",
                         "Estado_Garantia"] if c in alertas.columns]
    result = alertas[cols].sort_values("Dias_Para_Vencer", ascending=True)
    result = result.copy()
    if "Fecha_Vencimiento" in result.columns:
        result["Fecha_Vencimiento"] = result["Fecha_Vencimiento"].dt.strftime("%Y-%m-%d")
    return result.to_dict("records")
