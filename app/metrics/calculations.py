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
    proyecto: str = '',
    tipo_proyeccion: str = '',  # '', 'P&C', 'AdC', 'P&C+AdC'
    meses_hist: int = 12,
    meses_fut:  int = 18,
    limite: int = 60,
) -> dict:
    """
    Devuelve la evolución del saldo por contrato anclado en ultimo_ep:
    - Mes de ultimo_ep : saldo_actual (valor real SIC)
    - Meses anteriores : saldo_actual + Σ(EP desde m+1 hasta ultimo_ep)
    - Meses posteriores: saldo_actual − Σ(proyecciones desde ultimo_ep+1 hasta m)
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

    # Parsear selección de proyección (puede ser combinación: 'P&C,AdC', 'Lineal', 'P&C,Lineal', …)
    tipos_proj   = [t.strip() for t in tipo_proyeccion.split(',') if t.strip()] if tipo_proyeccion else []
    usa_lineal   = 'Lineal' in tipos_proj
    tipos_pyc    = [t for t in tipos_proj if t != 'Lineal']  # P&C y/o AdC seleccionados

    # Promedio mensual EP por contrato (para proyección Lineal)
    ep_lineal_avg: dict[str, float] = {}
    if usa_lineal:
        ep_cp = ep.copy()
        if 'Fecha_Periodo' in ep_cp.columns:
            ep_cp['_per'] = ep_cp['Fecha_Periodo'].dt.strftime('%Y-%m')
        else:
            ep_cp['_per'] = ''
        ag = ep_cp.groupby('codigo_ctt').agg(
            _total=('monto_CLP', 'sum'),
            _meses=('_per', 'nunique'),
        )
        for ctt_e, row_e in ag.iterrows():
            ep_lineal_avg[str(ctt_e)] = float(row_e['_total']) / max(int(row_e['_meses']), 1)

    # Proyecciones convertidas a CLP — filtradas por tipo si se indica
    proy_grp: dict[str, dict[str, float]] = {}
    proy_proyectos: dict[str, set[str]] = {}   # contrato_key -> proyectos únicos
    try:
        from app.config import SQL_PROYECCIONES_ENABLED
        if SQL_PROYECCIONES_ENABLED:
            from app.data.sql_server import get_proyecciones as _gproy
            if len(tipos_pyc) == 1:
                tipo_fil = tipos_pyc[0]
            elif not tipos_pyc:
                tipo_fil = None if usa_lineal else None  # sin P&C/AdC seleccionado
            else:
                tipo_fil = None  # P&C + AdC → todos
            # Si solo se seleccionó Lineal, no cargar proyecciones P&C/AdC
            proy_df  = _gproy(tipo=tipo_fil) if (tipos_pyc or not usa_lineal) else __import__('pandas').DataFrame()
            if not proy_df.empty:
                fx = _fx_rates()
                for _, pr in proy_df.iterrows():
                    ctt_key  = str(pr.get('contrato', '') or '').strip()
                    per      = str(pr.get('periodo',   '') or '').strip()
                    mon      = str(pr.get('moneda',    'CLP') or 'CLP').strip().upper()
                    amt      = float(pr.get('monto_proyectado', 0) or 0)
                    _proj = str(pr.get('proyecto',  '') or '').strip()
                    if ctt_key and per:
                        proy_grp.setdefault(ctt_key, {})[per] = (
                            proy_grp.get(ctt_key, {}).get(per, 0) + amt * fx.get(mon, 1.0)
                        )
                        if _proj and _proj.lower() not in ('nan', 'none', ''):
                            proy_proyectos.setdefault(ctt_key, set()).add(_proj)
    except Exception:
        pass

    # Filtrar por proyecto: mantener solo contratos que tengan proyección para ese proyecto
    if proyecto:
        proj_col = next((c for c in ep.columns if c.lower() == 'proyecto'), None)
        if proj_col:
            ctts_for_proj = set(ep[ep[proj_col] == proyecto]['codigo_ctt'].astype(str))
            # también buscar en proyecciones P&C (clave con y sin CO-)
            for k, rows in proy_proyectos.items():
                if proyecto in rows:
                    ctts_for_proj.add(k)
                    ctts_for_proj.add(k[3:] if k.startswith('CO-') else 'CO-' + k)
            saldo_df = saldo_df[saldo_df['codigo_ctt'].isin(ctts_for_proj)]

    # Normalización de códigos: si un código de proyección (p.ej. "CO-009/2025") no existe
    # como tal en SIC, construir un índice secundario sin el prefijo "CO-" para que coincida
    # con el código SIC (p.ej. "009/2025"). Los códigos SIC que SÍ tienen prefijo "CO-" se
    # resuelven por coincidencia exacta y no se re-mapean.
    valid_sic_codes: set[str] = set(saldo_df['codigo_ctt'].astype(str))
    proy_grp_stripped: dict[str, dict[str, float]] = {}
    for k, v in proy_grp.items():
        if k not in valid_sic_codes and k.startswith('CO-'):
            stripped = k[3:]
            if stripped not in proy_grp:  # no pisar coincidencia exacta existente
                for per, amt in v.items():
                    proy_grp_stripped.setdefault(stripped, {})[per] = (
                        proy_grp_stripped.get(stripped, {}).get(per, 0) + amt
                    )

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

    # EP agrupado por contrato y período (sin restricción de ventana — necesario para cálculo hacia atrás)
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
        saldo_act   = float(row.get('Saldo_CLP', 0) or 0)
        ep_ctt   = ep_grp.get(ctt, {})
        proy_ctt = proy_grp.get(ctt) or proy_grp_stripped.get(ctt, {})

        # Anchor: último período con EP real registrado
        ultimo_ep: str | None = max(ep_ctt.keys(), default=None) if ep_ctt else None

        saldos = [0.0] * len(month_list)

        # Proyección efectiva por mes: P&C/AdC donde existe, Lineal donde no hay dato
        lineal_avg = ep_lineal_avg.get(ctt, 0) if usa_lineal else 0

        def _proy(m: str) -> float:
            v = proy_ctt.get(m, 0)
            return v if v else lineal_avg

        if ultimo_ep is None:
            # Sin EP: mostrar saldo_act plano en histórico, proyectar hacia adelante
            running = saldo_act
            for i, m in enumerate(month_list):
                if m <= mes_actual:
                    saldos[i] = round(saldo_act)
                else:
                    running -= _proy(m)
                    saldos[i] = round(running)
        elif ultimo_ep in month_list:
            uep_idx = month_list.index(ultimo_ep)
            saldos[uep_idx] = round(saldo_act)

            # Hacia atrás: saldo(m) = saldo(m+1) + EP(m+1)
            running = saldo_act
            for i in range(uep_idx - 1, -1, -1):
                running += ep_ctt.get(month_list[i + 1], 0)
                saldos[i] = round(running)

            # Hacia adelante: saldo(m) = saldo(m-1) - proyeccion(m)
            running = saldo_act
            for i in range(uep_idx + 1, len(month_list)):
                running -= _proy(month_list[i])
                saldos[i] = round(running)
        elif ultimo_ep < month_list[0]:
            # Anchor antes de la ventana: todo en la ventana es "futuro"
            running = saldo_act
            for i, m in enumerate(month_list):
                running -= _proy(m)
                saldos[i] = round(running)
        else:
            # Anchor después de la ventana (caso raro): todo histórico
            running = saldo_act
            for i in range(len(month_list) - 1, -1, -1):
                running += ep_ctt.get(month_list[i + 1] if i + 1 < len(month_list) else ultimo_ep, 0)
                saldos[i] = round(running)

        # Primer mes después de ultimo_ep (o mes_actual si no hay EP) con saldo ≤ 0
        anchor_agot = ultimo_ep or mes_actual
        mes_agotamiento: str | None = None
        for i, m in enumerate(month_list):
            if m > anchor_agot and saldos[i] <= 0:
                mes_agotamiento = m
                break

        # Proyectos desde tabla de proyecciones (CO- y sin prefijo)
        _proy_set = proy_proyectos.get(ctt, set()) | proy_proyectos.get('CO-' + ctt, set())
        _proyectos_str = ', '.join(sorted(_proy_set)) if _proy_set else ''

        result.append({
            'codigo_ctt':       ctt,
            'descripcion':      str(row.get('Descripcion_ctt', '') or ''),
            'area':             str(row.get('Area_contrato',   '') or ''),
            'tipo':             str(row.get('Tipo_contrato',   '') or ''),
            'alcance':          str(row.get('alcance',         '') or ''),
            'proyectos':        _proyectos_str,
            'estado_ctt':       str(row.get('Estado_ctt',      '') or ''),
            'presupuesto':      float(row.get('Presupuesto_CLP', 0) or 0),
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
        start = max(0, mes_actual_idx - meses_hist)
        end   = start + max_cols
        month_list     = month_list[start:end]
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


def saldo_timeline_por_proyecto(
    tipo_proyeccion: str = '',
    meses_hist: int = 12,
    meses_fut: int = 18,
) -> dict:
    """
    Matriz de saldo mínimo por proyecto × mes.
    Para cada proyecto, el valor de cada mes es el mínimo saldo entre todos los
    contratos que lo abastecen. Rojo cuando alguno se agota (saldo ≤ 0).
    """
    tl = saldo_timeline(
        tipo_proyeccion=tipo_proyeccion,
        meses_hist=meses_hist,
        meses_fut=meses_fut,
        limite=999,
    )

    meses      = tl['meses']
    mes_actual = tl['mes_actual']

    if not tl['contratos']:
        return {
            'meses': meses, 'mes_actual': mes_actual,
            'mes_actual_idx': tl['mes_actual_idx'],
            'proyectos': [], 'tiene_proyecciones': tl['tiene_proyecciones'],
        }

    # Mapeo proyecto → contratos desde EP (fuente principal)
    ep       = load_ep()
    proj_col = next((c for c in ep.columns if c.lower() == 'proyecto'), None)
    proy_ctts: dict[str, set[str]] = {}
    if proj_col:
        for proy_val, grp in ep.dropna(subset=[proj_col]).groupby(proj_col):
            p = str(proy_val).strip()
            if p and p not in ('nan', 'None', ''):
                for ctt in grp['codigo_ctt'].astype(str).str.strip():
                    if ctt:
                        proy_ctts.setdefault(p, set()).add(ctt)

    # Complementar con proyectos desde proyecciones P&C (campo 'proyectos' en timeline)
    for ctt_data in tl['contratos']:
        for p in (x.strip() for x in (ctt_data.get('proyectos') or '').split(',') if x.strip()):
            proy_ctts.setdefault(p, set()).add(ctt_data['codigo_ctt'])

    ctt_map = {c['codigo_ctt']: c for c in tl['contratos']}
    n_meses = len(meses)

    result = []
    for proy in sorted(proy_ctts.keys()):
        matching = [ctt_map[c] for c in proy_ctts[proy] if c in ctt_map]
        if not matching:
            continue

        saldos_min = [
            min(c['saldos'][i] for c in matching)
            for i in range(n_meses)
        ]

        # Agotamiento del proyecto = mínima fecha de agotamiento entre sus contratos
        agots = [c['mes_agotamiento'] for c in matching if c['mes_agotamiento']]
        mes_agot = min(agots) if agots else None

        # Cuello de botella: contrato que agota primero
        contrato_critico = None
        if mes_agot:
            criticos = sorted(
                [c for c in matching if c['mes_agotamiento']],
                key=lambda c: c['mes_agotamiento'],
            )
            contrato_critico = criticos[0]['codigo_ctt']

        result.append({
            'proyecto':         proy,
            'n_contratos':      len(matching),
            'contratos':        [c['codigo_ctt'] for c in matching],
            'saldos_min':       saldos_min,
            'mes_agotamiento':  mes_agot,
            'contrato_critico': contrato_critico,
        })

    result.sort(key=lambda r: (
        0 if r['mes_agotamiento'] else 1,
        r['mes_agotamiento'] or '',
        r['proyecto'],
    ))

    return {
        'meses':              meses,
        'mes_actual':         mes_actual,
        'mes_actual_idx':     tl['mes_actual_idx'],
        'proyectos':          result,
        'tiene_proyecciones': tl['tiene_proyecciones'],
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


def home_ep_por_mes(estado_ctt: str = '', area: str = '', tipo: str = '', desde: str = '', hasta: str = '', proyecto: str = '', contrato: str = '', campana: str = '') -> dict:
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


# ── Estados de Pago ────────────────────────────────────────────────────────────

def _ep_base(
    area: str = '',
    tipo: str = '',
    contrato: str = '',
    estado_ctt: str = '',
    proyecto: str = '',
    periodo_desde: str = '',
    periodo_hasta: str = '',
) -> pd.DataFrame:
    ep = load_ep()
    if 'Fecha_Periodo' not in ep.columns:
        return ep
    ep = ep.copy()
    ep['periodo'] = ep['Fecha_Periodo'].dt.strftime('%Y-%m')

    if area       and 'Area_contrato' in ep.columns:
        ep = ep[ep['Area_contrato'] == area]
    if tipo       and 'Tipo_contrato' in ep.columns:
        ep = ep[ep['Tipo_contrato'] == tipo]
    if estado_ctt and 'Estado_ctt' in ep.columns:
        ep = ep[ep['Estado_ctt'] == estado_ctt]
    if proyecto:
        proj_col = next((c for c in ep.columns if c.lower() == 'proyecto'), None)
        if proj_col:
            ep = ep[ep[proj_col] == proyecto]
    if periodo_desde:
        ep = ep[ep['periodo'] >= periodo_desde]
    if periodo_hasta:
        ep = ep[ep['periodo'] <= periodo_hasta]
    if contrato:
        mask = ep['codigo_ctt'].str.contains(contrato, case=False, na=False)
        if 'Descripcion_ctt' in ep.columns:
            mask = mask | ep['Descripcion_ctt'].str.contains(contrato, case=False, na=False)
        ep = ep[mask]
    return ep


def ep_kpis(
    area: str = '',
    tipo: str = '',
    contrato: str = '',
    estado_ctt: str = '',
    proyecto: str = '',
    periodo_desde: str = '',
    periodo_hasta: str = '',
) -> dict:
    df = _ep_base(area, tipo, contrato, estado_ctt, proyecto, periodo_desde, periodo_hasta)
    return {
        'ep_total_clp':        float(df['monto_CLP'].sum()),
        'num_eps':             int(len(df)),
        'contratos_con_ep':    int(df['codigo_ctt'].nunique()),
        'ep_promedio_contrato': float(df['monto_CLP'].sum() / max(df['codigo_ctt'].nunique(), 1)),
    }


def ep_filtros() -> dict:
    ep = load_ep().copy()
    if 'Fecha_Periodo' in ep.columns:
        ep['periodo'] = ep['Fecha_Periodo'].dt.strftime('%Y-%m')
        periodos = sorted(ep['periodo'].dropna().unique().tolist())
    else:
        periodos = []
    proj_col = next((c for c in ep.columns if c.lower() == 'proyecto'), None)
    proyectos = sorted(
        ep[proj_col].replace('', float('nan')).dropna().unique().tolist()
    ) if proj_col else []
    return {
        'areas':       sorted(ep['Area_contrato'].dropna().unique().tolist()) if 'Area_contrato' in ep.columns else [],
        'tipos':       sorted(ep['Tipo_contrato'].dropna().unique().tolist()) if 'Tipo_contrato' in ep.columns else [],
        'estados_ctt': sorted(ep['Estado_ctt'].replace('', float('nan')).dropna().unique().tolist()) if 'Estado_ctt' in ep.columns else [],
        'proyectos':   proyectos,
        'periodos':    periodos,
    }


def ep_tabla(
    area: str = '',
    tipo: str = '',
    contrato: str = '',
    estado_ctt: str = '',
    proyecto: str = '',
    periodo_desde: str = '',
    periodo_hasta: str = '',
) -> list[dict]:
    df = _ep_base(area, tipo, contrato, estado_ctt, proyecto, periodo_desde, periodo_hasta)
    if df.empty:
        return []

    agg: dict = {'ep_total': ('monto_CLP', 'sum'), 'num_eps': ('monto_CLP', 'count')}
    if 'periodo' in df.columns:
        agg['primer_periodo'] = ('periodo', 'min')
        agg['ultimo_periodo'] = ('periodo', 'max')

    grp = df.groupby('codigo_ctt').agg(**{k: v for k, v in agg.items()}).reset_index()

    attrs_cols = [c for c in ['Descripcion_ctt', 'Tipo_contrato', 'Area_contrato', 'Estado_ctt'] if c in df.columns]
    if attrs_cols:
        attrs = df[['codigo_ctt'] + attrs_cols].drop_duplicates('codigo_ctt')
        grp = grp.merge(attrs, on='codigo_ctt', how='left')

    grp = grp.sort_values('ep_total', ascending=False)
    return grp.fillna('').to_dict('records')


def ep_por_mes(
    area: str = '',
    tipo: str = '',
    contrato: str = '',
    estado_ctt: str = '',
    proyecto: str = '',
    periodo_desde: str = '',
    periodo_hasta: str = '',
) -> list[dict]:
    df = _ep_base(area, tipo, contrato, estado_ctt, proyecto, periodo_desde, periodo_hasta)
    if df.empty or 'periodo' not in df.columns:
        return []
    grp = df.groupby('periodo')['monto_CLP'].sum().reset_index()
    grp.columns = ['periodo', 'ep_mes']
    return grp.sort_values('periodo').to_dict('records')


def ep_por_tipo(
    area: str = '',
    contrato: str = '',
    estado_ctt: str = '',
    proyecto: str = '',
    periodo_desde: str = '',
    periodo_hasta: str = '',
) -> list[dict]:
    df = _ep_base(area, '', contrato, estado_ctt, proyecto, periodo_desde, periodo_hasta)
    if df.empty or 'Tipo_contrato' not in df.columns:
        return []
    grp = df.groupby('Tipo_contrato')['monto_CLP'].sum().reset_index()
    grp.columns = ['tipo', 'monto']
    return grp.sort_values('monto', ascending=False).to_dict('records')


def ep_timeline(
    area: str = '',
    tipo: str = '',
    contrato: str = '',
    estado_ctt: str = '',
    proyecto: str = '',
    periodo_desde: str = '',
    periodo_hasta: str = '',
    limite: int = 60,
) -> dict:
    """
    Matriz de EP por contrato × mes.
    Columnas fijas: contrato, descripcion, area, tipo, total_ep, num_eps.
    Columnas de meses: EP consumido en ese mes (0 si no hubo).
    """
    df = _ep_base(area, tipo, contrato, estado_ctt, proyecto, periodo_desde, periodo_hasta)
    mes_actual = pd.Timestamp.now().strftime('%Y-%m')

    if df.empty or 'periodo' not in df.columns:
        return {'meses': [], 'mes_actual': mes_actual, 'contratos': [], 'total_contratos': 0}

    meses: list[str] = sorted(df['periodo'].dropna().unique().tolist())

    # EP por (contrato, periodo)
    ep_mx = df.groupby(['codigo_ctt', 'periodo'])['monto_CLP'].sum()

    # Agregado por contrato
    ctts_grp = (
        df.groupby('codigo_ctt')
        .agg(total_ep=('monto_CLP', 'sum'), num_eps=('monto_CLP', 'count'))
        .reset_index()
        .sort_values('total_ep', ascending=False)
        .head(limite)
    )

    attrs_cols = [c for c in ['Descripcion_ctt', 'Tipo_contrato', 'Area_contrato', 'Estado_ctt'] if c in df.columns]
    if attrs_cols:
        attrs = df[['codigo_ctt'] + attrs_cols].drop_duplicates('codigo_ctt')
        ctts_grp = ctts_grp.merge(attrs, on='codigo_ctt', how='left')

    total = int(df['codigo_ctt'].nunique())
    result = []
    for _, row in ctts_grp.iterrows():
        ctt = str(row['codigo_ctt'])
        eps = [round(float(ep_mx.get((ctt, m), 0) or 0)) for m in meses]
        result.append({
            'codigo_ctt':  ctt,
            'descripcion': str(row.get('Descripcion_ctt', '') or ''),
            'area':        str(row.get('Area_contrato',   '') or ''),
            'tipo':        str(row.get('Tipo_contrato',   '') or ''),
            'estado_ctt':  str(row.get('Estado_ctt',      '') or ''),
            'total_ep':    round(float(row['total_ep'])),
            'num_eps':     int(row['num_eps']),
            'eps':         eps,
        })

    return {
        'meses':            meses,
        'mes_actual':       mes_actual,
        'contratos':        result,
        'total_contratos':  total,
    }


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
