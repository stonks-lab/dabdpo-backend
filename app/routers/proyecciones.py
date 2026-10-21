from fastapi import APIRouter, Query, HTTPException
from typing import Optional
from app.config import SQL_PROYECCIONES_ENABLED

router = APIRouter(prefix="/proyecciones", tags=["proyecciones"])

_NO_DATA = {"detail": "Proyecciones no disponibles. Ejecutá el ETL para cargar datos desde P&C."}


def _check():
    if not SQL_PROYECCIONES_ENABLED:
        raise HTTPException(status_code=503, detail=_NO_DATA["detail"])


@router.get("/status")
def get_status():
    """Indica si los datos de proyecciones están disponibles."""
    if not SQL_PROYECCIONES_ENABLED:
        return {"disponible": False, "mensaje": _NO_DATA["detail"]}

    try:
        from app.data.sql_server import get_proyecciones_kpis
        kpis = get_proyecciones_kpis()
        return {"disponible": True, "ultima_carga": kpis["ultima_carga"], "periodo_max": kpis["periodo_max"]}
    except Exception as e:
        return {"disponible": False, "mensaje": str(e)}


@router.get("/kpis")
def get_kpis():
    _check()
    from app.data.sql_server import get_proyecciones_kpis
    return get_proyecciones_kpis()


@router.get("/por-mes")
def get_por_mes():
    _check()
    from app.data.sql_server import get_proyecciones_por_mes
    return get_proyecciones_por_mes()


@router.get("/tabla")
def get_tabla(
    contrato:      Optional[str] = Query(None),
    periodo_desde: Optional[str] = Query(None, description="Formato YYYY-MM"),
    tipo:          Optional[str] = Query(None, description="P&C o AdC"),
):
    _check()
    from app.data.sql_server import get_proyecciones
    df = get_proyecciones(contrato=contrato, periodo_desde=periodo_desde, tipo=tipo)
    return df.fillna("").to_dict("records")


@router.get("/vs-ep")
def get_vs_ep(periodo_desde: Optional[str] = Query(None)):
    _check()
    from app.data.sql_server import get_proyecciones_vs_ep
    df = get_proyecciones_vs_ep(periodo_desde=periodo_desde)
    return df.fillna(0).to_dict("records")


def _norm_ctt(code: str) -> str:
    """Normaliza código de contrato: quita prefijo CO-, espacios y non-breaking spaces."""
    c = code.strip().replace('\xa0', '').replace(' ', '')
    upper = c.upper()
    if upper.startswith('CO-'):
        c = c[3:]
    return c.strip()


def _load_sic_ep() -> tuple[dict[str, dict[str, float]], dict[str, str]]:
    """
    Carga SIC_EPs y devuelve:
      ep_grp   : {norm_codigo → {periodo(YYYY-MM) → monto_clp}}
      ultimo_ep: {norm_codigo → max_periodo}
    Período basado en 'Fecha Hasta' del EP.
    """
    import pandas as pd
    from app.data.sql_server import query_df
    try:
        ep = query_df("SELECT * FROM dbo.SIC_EPs")
    except Exception:
        return {}, {}

    if ep.empty:
        return {}, {}

    # Columna 1 = Contrato Código (encoding variable)
    cols = ep.columns.tolist()
    ep.rename(columns={cols[1]: '_codigo'}, inplace=True)
    ep['_codigo'] = ep['_codigo'].astype(str).str.strip().str.replace('\xa0', '').str.replace(' ', '')

    ep['Fecha Hasta'] = pd.to_datetime(ep['Fecha Hasta'], errors='coerce')
    ep = ep.dropna(subset=['Fecha Hasta'])
    ep['_periodo'] = ep['Fecha Hasta'].dt.strftime('%Y-%m')

    monto  = pd.to_numeric(ep['Monto'],              errors='coerce').fillna(0)
    cambio = pd.to_numeric(ep['Valor de Cambio'],    errors='coerce').fillna(1).replace(0, 1)
    ep['_monto_clp'] = monto * cambio

    ep['_norm'] = ep['_codigo'].apply(_norm_ctt)

    ep_grp:    dict[str, dict[str, float]] = {}
    ultimo_ep: dict[str, str]              = {}

    for (norm, per), v in ep.groupby(['_norm', '_periodo'])['_monto_clp'].sum().items():
        ep_grp.setdefault(str(norm), {})[str(per)] = float(v)

    for norm, grp_df in ep.groupby('_norm'):
        ultimo_ep[str(norm)] = str(grp_df['_periodo'].max())

    return ep_grp, ultimo_ep


@router.get("/timeline")
def get_timeline(
    contrato:   Optional[str] = Query(None),
    meses_hist: int           = Query(3),
    meses_fut:  int           = Query(24),
    limite:     int           = Query(20),
):
    _check()
    import pandas as pd
    from app.data.sql_server import get_proyecciones

    today    = pd.Timestamp.now().to_period('M').to_timestamp()
    mes_act  = today.strftime('%Y-%m')
    hist_ini = (today - pd.DateOffset(months=meses_hist)).to_period('M').to_timestamp()
    hist_str = hist_ini.strftime('%Y-%m')

    df = get_proyecciones(contrato=contrato)
    if df.empty:
        return {'meses': [], 'mes_actual': mes_act, 'mes_actual_idx': 0,
                'contratos': [], 'totales_mes': [], 'ultimo_ep': None}

    df = df[df['periodo'] >= hist_str].copy()

    # Month list: hist_ini → today + meses_fut
    fut_end    = (today + pd.DateOffset(months=meses_fut)).to_period('M').to_timestamp()
    month_list: list[str] = [m.strftime('%Y-%m') for m in pd.date_range(hist_ini, fut_end, freq='MS')]

    # P&C agrupado: {contrato → {periodo → amount}}
    proy_grp: dict[str, dict[str, float]] = {}
    for (ctt, per), v in df.groupby(['contrato', 'periodo'])['monto_proyectado'].sum().items():
        proy_grp.setdefault(str(ctt), {})[str(per)] = float(v)

    # EP de SIC: {norm_codigo → {periodo → monto_clp}}, {norm_codigo → ultimo_ep}
    ep_grp, ultimo_ep_per = _load_sic_ep()

    # Top N contratos por proyección futura
    future_df = df[df['periodo'] >= mes_act]
    future_totals = (
        future_df.groupby('contrato')['monto_proyectado'].sum()
        .sort_values(ascending=False)
        .head(limite)
    )

    result = []
    for ctt, total_fut in future_totals.items():
        ctt_str  = str(ctt)
        norm     = _norm_ctt(ctt_str)
        proy_ctt = proy_grp.get(ctt_str, {})
        ep_ctt   = ep_grp.get(norm, {})
        ult_ep   = ultimo_ep_per.get(norm)   # último EP real para este contrato

        # saldos: EP para meses ≤ ultimo_ep, P&C para meses posteriores
        saldos = []
        for m in month_list:
            if ult_ep and m <= ult_ep:
                saldos.append(float(ep_ctt.get(m, 0)))
            else:
                saldos.append(float(proy_ctt.get(m, 0)))

        result.append({
            'contrato':     ctt_str,
            'total_futuro': float(total_fut),
            'ultimo_ep':    ult_ep,
            'saldos':       saldos,
        })

    # Totales por mes (todos los contratos de proyecciones en ventana)
    tot_serie   = df.groupby('periodo')['monto_proyectado'].sum()
    totales_mes = [float(tot_serie.get(m, 0)) for m in month_list]

    mes_actual_idx = month_list.index(mes_act) if mes_act in month_list else 0

    # ultimo_ep global para la fila de totales (máximo entre todos los contratos mostrados)
    ultimo_ep_global = max(
        (r['ultimo_ep'] for r in result if r['ultimo_ep']),
        default=None
    )

    return {
        'meses':          month_list,
        'mes_actual':     mes_act,
        'mes_actual_idx': mes_actual_idx,
        'contratos':      result,
        'totales_mes':    totales_mes,
        'ultimo_ep':      ultimo_ep_global,
    }
