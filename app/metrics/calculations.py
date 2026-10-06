import pandas as pd
from functools import lru_cache
from app.data.loader import load_ep, load_solpe, load_boletas


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


# ── SIC Contratos — fuente de presupuesto ─────────────────────────────────────

def _load_sic_presupuesto() -> pd.DataFrame:
    """
    Retorna un DataFrame con presupuesto actualizado desde SIC_Contratos.
    Columnas: codigo_ctt, Presupuesto_CLP, Descripcion_ctt,
              Tipo_contrato, Area_contrato, Estado_ctt
    """
    try:
        from app.data.sql_server import query_df
        df = query_df(
            "SELECT * FROM dbo.SIC_Contratos ORDER BY _fecha_carga DESC"
        )
    except Exception:
        return pd.DataFrame(columns=[
            'codigo_ctt', 'Presupuesto_CLP', 'Descripcion_ctt',
            'Tipo_contrato', 'Area_contrato', 'Estado_ctt',
        ])

    if df.empty:
        return pd.DataFrame(columns=[
            'codigo_ctt', 'Presupuesto_CLP', 'Descripcion_ctt',
            'Tipo_contrato', 'Area_contrato', 'Estado_ctt',
        ])

    # Renombre por posición — evita problemas de encoding en Código y Área
    # Posición 1 → Código (codigo_ctt)
    # Posición 2 → Nombre (Descripcion_ctt)
    # Posición 3 → Estado (Estado_ctt)
    # Posición 20 → Área (Area_contrato, tiene tilde)
    # Posición 21 → Tipo Contrato
    pos_rename = {
        1:  'codigo_ctt',
        2:  'Descripcion_ctt',
        3:  'Estado_ctt',
        20: 'Area_contrato',
        21: 'Tipo_contrato',
    }
    rename_map = {df.columns[i]: name for i, name in pos_rename.items() if i < len(df.columns)}
    df.rename(columns=rename_map, inplace=True)

    # Monto Actualizado y Cambio no tienen problemas de encoding
    if 'Monto Actualizado' not in df.columns or 'codigo_ctt' not in df.columns:
        return pd.DataFrame(columns=[
            'codigo_ctt', 'Presupuesto_CLP', 'Descripcion_ctt',
            'Tipo_contrato', 'Area_contrato', 'Estado_ctt',
        ])

    df['codigo_ctt'] = df['codigo_ctt'].astype(str).str.strip()
    df = df.drop_duplicates(subset='codigo_ctt', keep='first')  # keep más reciente (ORDER BY _fecha_carga DESC)

    monto  = pd.to_numeric(df['Monto Actualizado'], errors='coerce').fillna(0)
    cambio = pd.to_numeric(df.get('Cambio', 1),     errors='coerce').fillna(1).replace(0, 1)
    df['Presupuesto_CLP'] = monto * cambio

    keep = [c for c in ['codigo_ctt', 'Presupuesto_CLP', 'Descripcion_ctt',
                         'Tipo_contrato', 'Area_contrato', 'Estado_ctt'] if c in df.columns]
    return df[keep].copy()


# ── Saldo de contratos ─────────────────────────────────────────────────────────

def saldo_por_contrato() -> pd.DataFrame:
    ep   = load_ep()
    ppto = _load_sic_presupuesto()

    ep_sum = ep.groupby("codigo_ctt")["monto_CLP"].sum().reset_index()
    ep_sum.columns = ["codigo_ctt", "EP_Consumido_CLP"]

    # Base: todos los contratos con presupuesto en SIC
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

    # Atributos: usar SIC_Contratos como fuente primaria; EP como fallback
    ep_attrs = ep[["codigo_ctt", "Descripcion_ctt", "Tipo_contrato",
                   "Area_contrato", "Estado_ctt"]].drop_duplicates("codigo_ctt")

    for col in ("Descripcion_ctt", "Tipo_contrato", "Area_contrato", "Estado_ctt"):
        if col not in merged.columns:
            merged = merged.merge(ep_attrs[["codigo_ctt", col]], on="codigo_ctt", how="left")

    # Add alcance from sourcing_plan
    try:
        from app.data.sql_server import query_df
        sp = query_df(
            "SELECT contrato, MAX(alcance) AS alcance FROM dbo.sourcing_plan "
            "WHERE contrato IS NOT NULL AND contrato != '' GROUP BY contrato"
        )
        if not sp.empty:
            merged = merged.merge(sp.rename(columns={'contrato': 'codigo_ctt'}),
                                  on='codigo_ctt', how='left')
    except Exception:
        pass
    if 'alcance' not in merged.columns:
        merged['alcance'] = ''

    return merged


def saldo_timeline(
    area: str = '',
    tipo: str = '',
    estado_ctt: str = '',
    contrato: str = '',
    estado_saldo: str = '',
    alcance: str = '',
    meses_hist: int = 12,
    meses_fut:  int = 18,
    limite: int = 60,
) -> dict:
    """
    Devuelve la evolución del saldo por contrato:
    - Histórico: presupuesto − Σ(EP hasta T)
    - Mes actual: saldo_actual (ancla real)
    - Futuro: saldo_actual − Σ(proyecciones P&C hasta T)
    """
    ep       = load_ep()
    saldo_df = saldo_por_contrato()

    if estado_ctt:   saldo_df = saldo_df[saldo_df['Estado_ctt']   == estado_ctt]
    if area:         saldo_df = saldo_df[saldo_df['Area_contrato'] == area]
    if tipo:         saldo_df = saldo_df[saldo_df['Tipo_contrato'] == tipo]
    if estado_saldo: saldo_df = saldo_df[saldo_df['Estado_Saldo']  == estado_saldo]
    if alcance and 'alcance' in saldo_df.columns:
        saldo_df = saldo_df[saldo_df['alcance'] == alcance]
    if contrato:
        codes = [c.strip() for c in contrato.split(',') if c.strip()]
        if len(codes) > 1:
            saldo_df = saldo_df[saldo_df['codigo_ctt'].isin(codes)]
        else:
            mask = (
                saldo_df['codigo_ctt'].str.contains(codes[0], case=False, na=False) |
                saldo_df['Descripcion_ctt'].str.contains(codes[0], case=False, na=False)
            )
            saldo_df = saldo_df[mask]

    today      = pd.Timestamp.now().to_period('M').to_timestamp()
    mes_actual = today.strftime('%Y-%m')
    hist_start = (today - pd.DateOffset(months=meses_hist)).to_period('M').to_timestamp()
    hist_start_str = hist_start.strftime('%Y-%m')

    # Proyecciones convertidas a CLP
    proy_grp: dict[str, dict[str, float]] = {}
    try:
        from app.config import SQL_PROYECCIONES_ENABLED
        if SQL_PROYECCIONES_ENABLED:
            from app.data.sql_server import get_proyecciones as _gproy
            proy_df = _gproy()
            if not proy_df.empty:
                fx = _fx_rates()
                for _, pr in proy_df.iterrows():
                    ctt_key = str(pr.get('contrato', '') or '').strip()
                    per     = str(pr.get('periodo',   '') or '').strip()
                    mon     = str(pr.get('moneda',    'CLP') or 'CLP').strip().upper()
                    amt     = float(pr.get('monto_proyectado', 0) or 0)
                    if ctt_key and per:
                        proy_grp.setdefault(ctt_key, {})[per] = (
                            proy_grp.get(ctt_key, {}).get(per, 0) + amt * fx.get(mon, 1.0)
                        )
    except Exception:
        pass

    # Horizonte futuro: hasta el último mes con proyección, mínimo meses_fut
    max_proy_per = max(
        (per for cps in proy_grp.values() for per in cps),
        default=''
    )
    fut_end = today + pd.DateOffset(months=meses_fut)
    if max_proy_per:
        try:
            cand = pd.to_datetime(max_proy_per + '-01')
            if cand > fut_end:
                fut_end = cand
        except Exception:
            pass
    fut_end = fut_end.to_period('M').to_timestamp()

    months     = pd.date_range(hist_start, fut_end, freq='MS')
    month_list: list[str] = [m.strftime('%Y-%m') for m in months]
    mes_actual_idx = month_list.index(mes_actual) if mes_actual in month_list else len(month_list) - 1

    # EP agrupado por contrato y período (todos los períodos, no solo la ventana)
    ep_grp: dict[str, dict[str, float]] = {}
    if not ep.empty and 'Fecha_Periodo' in ep.columns:
        ep_c = ep[['codigo_ctt', 'Fecha_Periodo', 'monto_CLP']].copy()
        ep_c['per'] = ep_c['Fecha_Periodo'].dt.strftime('%Y-%m')
        ep_c = ep_c.dropna(subset=['per'])
        for (ck, pk), v in ep_c.groupby(['codigo_ctt', 'per'])['monto_CLP'].sum().items():
            ep_grp.setdefault(str(ck), {})[str(pk)] = float(v)

    result: list[dict] = []
    for _, row in saldo_df.iterrows():
        ctt         = str(row['codigo_ctt'])
        presupuesto = float(row.get('Presupuesto_CLP', 0) or 0)
        saldo_act   = float(row.get('Saldo_CLP', 0) or 0)
        ep_ctt      = ep_grp.get(ctt, {})
        proy_ctt    = proy_grp.get(ctt, {})

        # EP consumido antes de la ventana de visualización
        ep_antes = sum(v for k, v in ep_ctt.items() if k < hist_start_str)

        saldos: list[float] = []
        cum_ep   = ep_antes
        cum_proy = 0.0

        for m in month_list:
            if m < mes_actual:
                cum_ep += ep_ctt.get(m, 0)
                saldos.append(round(presupuesto - cum_ep))
            elif m == mes_actual:
                saldos.append(round(saldo_act))   # ancla con el valor real
            else:
                cum_proy += proy_ctt.get(m, 0)
                saldos.append(round(saldo_act - cum_proy))

        # Primer mes futuro con saldo ≤ 0
        mes_agotamiento: str | None = None
        for i, m in enumerate(month_list):
            if m > mes_actual and saldos[i] <= 0:
                mes_agotamiento = m
                break

        # Último período con EP real registrado
        ultimo_ep: str | None = max(ep_ctt.keys(), default=None) if ep_ctt else None

        result.append({
            'codigo_ctt':       ctt,
            'descripcion':      str(row.get('Descripcion_ctt', '') or ''),
            'area':             str(row.get('Area_contrato',   '') or ''),
            'tipo':             str(row.get('Tipo_contrato',   '') or ''),
            'estado_ctt':       str(row.get('Estado_ctt',      '') or ''),
            'presupuesto':      presupuesto,
            'saldo_actual':     saldo_act,
            'pct_saldo':        float(row.get('Pct_Saldo', 0) or 0),
            'estado_saldo':     str(row.get('Estado_Saldo',   '') or ''),
            'mes_agotamiento':  mes_agotamiento,
            'ultimo_ep':        ultimo_ep,
            'tiene_proyeccion': bool(proy_ctt),
            'saldos':           saldos,
        })

    # Orden: con agotamiento primero (más próximo), luego por % saldo
    result.sort(key=lambda r: (
        0 if r['mes_agotamiento'] else 1,
        r['mes_agotamiento'] or '',
        r['pct_saldo'],
    ))

    total = len(result)
    result = result[:limite]

    # Recortar columnas: máximo 36 meses (12 hist + actual + 23 fut) para no saturar la tabla
    max_cols = meses_hist + 1 + min(meses_fut, 23)
    if len(month_list) > max_cols:
        # Asegurarse de que el mes actual esté incluido
        start = max(0, mes_actual_idx - meses_hist)
        end   = start + max_cols
        month_list    = month_list[start:end]
        mes_actual_idx = mes_actual_idx - start
        for r in result:
            r['saldos'] = r['saldos'][start:end]
            if r['mes_agotamiento'] and r['mes_agotamiento'] not in month_list:
                r['mes_agotamiento'] = None

    return {
        'meses':              month_list,
        'mes_actual':         mes_actual,
        'mes_actual_idx':     mes_actual_idx,
        'contratos':          result,
        'total_contratos':    total,
        'tiene_proyecciones': bool(proy_grp),
    }


def saldo_evolucion() -> list[dict]:
    """Retorna EP consumido acumulado mes a mes."""
    ep = load_ep()
    if 'Fecha_Periodo' not in ep.columns:
        return []
    monthly = (
        ep.dropna(subset=['Fecha_Periodo'])
          .groupby('Fecha_Periodo')['monto_CLP']
          .sum()
          .reset_index()
          .sort_values('Fecha_Periodo')
    )
    monthly['acumulado'] = monthly['monto_CLP'].cumsum()
    return [
        {
            'periodo': r['Fecha_Periodo'].strftime('%Y-%m'),
            'ep_mes':  round(float(r['monto_CLP']), 0),
            'ep_acum': round(float(r['acumulado']), 0),
        }
        for _, r in monthly.iterrows()
    ]


def _filter_ctts(estado_ctt: str = '', area: str = '', tipo: str = '', proyecto: str = '', contrato: str = '', campana: str = '') -> set | None:
    """Devuelve el conjunto de codigo_ctt que pasan los filtros, o None si no hay filtros."""
    if not (estado_ctt or area or tipo or proyecto or contrato or campana):
        return None

    base: set | None = None

    if estado_ctt or area or tipo:
        saldo_df = saldo_por_contrato()
        if estado_ctt:
            saldo_df = saldo_df[saldo_df["Estado_ctt"] == estado_ctt]
        if area:
            saldo_df = saldo_df[saldo_df["Area_contrato"] == area]
        if tipo:
            saldo_df = saldo_df[saldo_df["Tipo_contrato"] == tipo]
        base = set(saldo_df["codigo_ctt"].tolist())

    if campana:
        from app.data.sql_server import get_proyectos_por_campanas
        camp_list = [c.strip() for c in campana.split(",") if c.strip()]
        proy_codes = get_proyectos_por_campanas(camp_list)
        if proy_codes:
            ep = load_ep()
            proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
            camp_ctts = set(ep[ep[proj_col].isin(proy_codes)]["codigo_ctt"].tolist()) if proj_col else set()
        else:
            camp_ctts = set()
        base = camp_ctts if base is None else base & camp_ctts

    if proyecto:
        ep = load_ep()
        proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
        proy_list = [p.strip() for p in proyecto.split(",") if p.strip()]
        proy_ctts = set(ep[ep[proj_col].isin(proy_list)]["codigo_ctt"].tolist()) if proj_col else set()
        base = proy_ctts if base is None else base & proy_ctts

    if contrato:
        ctt_set = {c.strip() for c in contrato.split(",") if c.strip()}
        base = ctt_set if base is None else base & ctt_set

    return base


def home_kpis(estado_ctt: str = '', area: str = '', tipo: str = '', desde: str = '', hasta: str = '', proyecto: str = '', contrato: str = '', campana: str = '') -> dict:
    ep   = load_ep()
    sol  = load_solpe()
    bol  = load_boletas()

    ctts = _filter_ctts(estado_ctt, area, tipo, proyecto, contrato, campana)
    ep_f = ep[ep["codigo_ctt"].isin(ctts)] if ctts is not None else ep
    ep_f = _filter_ep_fechas(ep_f, desde, hasta)

    sic_df = _load_sic_presupuesto()
    if ctts is not None:
        sic_df = sic_df[sic_df["codigo_ctt"].isin(ctts)]
    presupuesto = float(sic_df["Presupuesto_CLP"].sum())
    consumido   = float(ep_f["monto_CLP"].sum())
    saldo       = presupuesto - consumido
    pct         = consumido / presupuesto if presupuesto else 0

    saldo_df = saldo_por_contrato()
    if ctts is not None:
        saldo_df = saldo_df[saldo_df["codigo_ctt"].isin(ctts)]
    criticos    = int((saldo_df["Estado_Saldo"] == "CRÍTICO").sum())
    advertencia = int((saldo_df["Estado_Saldo"] == "ADVERTENCIA").sum())
    negativos   = int((saldo_df["Estado_Saldo"] == "NEGATIVO").sum())

    solpes_activas = int((sol["Grupo_Estado"] == "En Proceso").sum()) if len(sol) else 0

    if ctts is not None and "Contrato" in bol.columns:
        bol = bol[bol["Contrato"].isin(ctts)]
    bol_criticas = 0
    bol_vencidas = 0
    if "Alerta_Vencimiento" in bol.columns:
        bol_criticas = int((bol["Alerta_Vencimiento"] == "CRÍTICO").sum())
        bol_vencidas = int((bol["Alerta_Vencimiento"] == "VENCIDA").sum())

    semaforo = (
        "ROJO"     if criticos >= 5 or negativos >= 1 else
        "NARANJA"  if bol_criticas >= 3 else
        "AMARILLO" if advertencia >= 5 else
        "VERDE"
    )

    totales   = int(sic_df["codigo_ctt"].nunique())
    regulares = max(0, totales - criticos - advertencia - negativos)

    return {
        "presupuesto_total_clp": presupuesto,
        "ep_consumido_clp":      consumido,
        "saldo_real_clp":        saldo,
        "pct_consumido":         round(pct, 4),
        "contratos_totales":     totales,
        "contratos_regulares":   regulares,
        "contratos_con_ep":      int(ep_f["codigo_ctt"].nunique()),
        "contratos_criticos":    criticos,
        "contratos_advertencia": advertencia,
        "contratos_negativos":   negativos,
        "solpes_activas":        solpes_activas,
        "boletas_criticas":      bol_criticas,
        "boletas_vencidas":      bol_vencidas,
        "semaforo":              semaforo,
    }


def home_filtros() -> dict:
    saldo_df = saldo_por_contrato()
    ep = load_ep()
    proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
    proyectos: list[str] = []
    if proj_col:
        proyectos = sorted(
            ep[proj_col].dropna().astype(str).str.strip()
            .replace("", float("nan")).dropna().unique().tolist()
        )
    ctts_df = (
        saldo_df[["codigo_ctt", "Descripcion_ctt"]]
        .drop_duplicates("codigo_ctt")
        .dropna(subset=["codigo_ctt"])
        .assign(codigo_ctt=lambda d: d["codigo_ctt"].astype(str).str.strip())
        .assign(Descripcion_ctt=lambda d: d["Descripcion_ctt"].fillna("").astype(str).str.strip())
        .sort_values("codigo_ctt")
    )
    contratos = [
        {"codigo": r["codigo_ctt"], "nombre": r["Descripcion_ctt"]}
        for _, r in ctts_df.iterrows()
    ]
    from app.data.sql_server import get_campanas_disponibles
    return {
        "estados_ctt": sorted(saldo_df["Estado_ctt"].dropna().unique().tolist()),
        "areas":        sorted(saldo_df["Area_contrato"].dropna().unique().tolist()),
        "tipos":        sorted(saldo_df["Tipo_contrato"].dropna().unique().tolist()),
        "proyectos":    proyectos,
        "contratos":    contratos,
        "campanas":     get_campanas_disponibles(),
    }


def _filter_ep_fechas(ep: pd.DataFrame, desde: str, hasta: str) -> pd.DataFrame:
    if desde:
        ep = ep[ep["Fecha_Periodo"] >= pd.to_datetime(desde + "-01", errors="coerce")]
    if hasta:
        ep = ep[ep["Fecha_Periodo"] <= pd.to_datetime(hasta + "-01", errors="coerce") + pd.offsets.MonthEnd(0)]
    return ep


def ep_por_mes(estado_ctt: str = '', area: str = '', tipo: str = '', desde: str = '', hasta: str = '', proyecto: str = '', contrato: str = '', campana: str = '') -> dict:
    ep   = load_ep()
    ctts = _filter_ctts(estado_ctt, area, tipo, proyecto, contrato, campana)
    if ctts is not None:
        ep = ep[ep["codigo_ctt"].isin(ctts)]
    ep = _filter_ep_fechas(ep, desde, hasta)

    ep = ep.copy()
    ep["_alcance"] = ep["Tipo_contrato"].fillna("Sin tipo").astype(str).str.strip().replace("", "Sin tipo")

    grp = (
        ep.groupby(["Fecha_Periodo", "_alcance"])["monto_CLP"]
        .sum()
        .reset_index()
        .sort_values("Fecha_Periodo")
    )
    grp["periodo"] = grp["Fecha_Periodo"].dt.strftime("%Y-%m")

    pivot = grp.pivot_table(
        index="periodo", columns="_alcance", values="monto_CLP",
        aggfunc="sum", fill_value=0,
    ).reset_index()
    pivot.columns.name = None

    alcances_all = [c for c in pivot.columns if c != "periodo"]
    # ordenar por EP total descendente para colores consistentes
    totals = pivot[alcances_all].sum()
    alcances = totals.sort_values(ascending=False).index.tolist()

    pivot["monto"]    = pivot[alcances].sum(axis=1)
    pivot["__zero__"] = 0

    return {"rows": pivot.to_dict("records"), "alcances": alcances}


def ep_por_tipo_contrato(estado_ctt: str = '', area: str = '', tipo: str = '', desde: str = '', hasta: str = '', proyecto: str = '', contrato: str = '', campana: str = '') -> list[dict]:
    ep   = load_ep()
    ctts = _filter_ctts(estado_ctt, area, tipo, proyecto, contrato, campana)
    if ctts is not None:
        ep = ep[ep["codigo_ctt"].isin(ctts)]
    ep = _filter_ep_fechas(ep, desde, hasta)
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


def alertas_boletas(estado_ctt: str = '', area: str = '', tipo: str = '', proyecto: str = '', contrato: str = '', campana: str = '') -> list[dict]:
    bol = load_boletas()
    if "Alerta_Vencimiento" not in bol.columns:
        return []
    ctts = _filter_ctts(estado_ctt, area, tipo, proyecto, contrato, campana)
    if ctts is not None and "Contrato" in bol.columns:
        bol = bol[bol["Contrato"].isin(ctts)]
    alertas = bol[bol["Alerta_Vencimiento"].isin(["CRÍTICO", "ADVERTENCIA", "ATENCIÓN", "VENCIDA"])]
    cols = [c for c in ["Contrato", "Nro Boleta", "Servicio", "Proveedor", "Monto", "Moneda",
                         "Fecha_Vencimiento", "Dias_Para_Vencer", "Alerta_Vencimiento",
                         "Estado_Garantia"] if c in alertas.columns]
    result = alertas[cols].sort_values("Dias_Para_Vencer", ascending=True)
    result = result.copy()
    if "Fecha_Vencimiento" in result.columns:
        result["Fecha_Vencimiento"] = result["Fecha_Vencimiento"].dt.strftime("%Y-%m-%d")
    return result.to_dict("records")
