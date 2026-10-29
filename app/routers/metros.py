from fastapi import APIRouter
import numpy as np
from pathlib import Path

router = APIRouter(prefix="/metros", tags=["metros"])

_PARQUET = Path(__file__).resolve().parent.parent.parent / "etl" / "d2.parquet"


def _classify_tipo(desc: str) -> str | None:
    d = str(desc).lower()
    if any(x in d for x in ['tricono', 'martillo reverso', 'aire reverso']):
        return 'AR'
    if any(x in d for x in ['diamantina', 'diamantino']):
        return 'DHH'
    return None


@router.get("/pai/versiones")
def list_pai_versiones():
    from app.data.sql_server import get_pai_versiones
    return get_pai_versiones()


@router.get("/pai")
def get_pai(ano: int | None = None, version: str | None = None):
    from app.data.sql_server import get_pai_proyectos
    return get_pai_proyectos(ano=ano, version=version)


@router.get("/avance")
def get_avance():
    from app.data.sql_server import get_avance_semanal
    return get_avance_semanal()


@router.get("/presupuesto-anos")
def get_presupuesto_anos():
    """Returns years available in the parquet budget (d2.parquet date columns)."""
    import pandas as pd
    df = pd.read_parquet(_PARQUET)
    metros = df[df['UN'].astype(str).str.lower() == 'metro']
    date_cols = [c for c in metros.columns if len(c) == 10 and c[:4].isdigit()]
    anos = sorted({int(c[:4]) for c in date_cols}, reverse=True)
    return {'anos': anos}


@router.get("/presupuesto-perforacion")
def get_presupuesto_perforacion(ano: int | None = None):
    import pandas as pd

    df = pd.read_parquet(_PARQUET)
    metros = df[df['UN'].astype(str).str.lower() == 'metro'].copy()

    # Keep latest file per _codigo_proyecto
    metros['_fecha_extraccion'] = pd.to_datetime(metros['_fecha_extraccion'], errors='coerce')
    latest = (
        metros.groupby('_codigo_proyecto')['_fecha_extraccion']
        .max().reset_index().rename(columns={'_fecha_extraccion': '_max_fecha'})
    )
    metros = metros.merge(latest, on='_codigo_proyecto')
    metros = metros[metros['_fecha_extraccion'] == metros['_max_fecha']].drop(columns=['_max_fecha'])

    metros['tipo'] = metros['DESCRIPCION'].apply(_classify_tipo)
    metros = metros[metros['tipo'].notna()].copy()  # defragment

    date_cols = [c for c in metros.columns if len(c) == 10 and c[:4].isdigit()]
    if ano is not None:
        date_cols = [c for c in date_cols if c[:4] == str(ano)]
    metros[date_cols] = metros[date_cols].apply(pd.to_numeric, errors='coerce').fillna(0)
    metros['ctd']     = pd.to_numeric(metros['CTD'], errors='coerce').fillna(0)
    metros['ppt_usd'] = metros[date_cols].sum(axis=1)

    # Strip leading "NN." version prefix from project code
    metros['proyecto'] = metros['_codigo_proyecto'].str.replace(r'^\d{2}\.', '', regex=True).str.strip()

    # Drop rows whose project code has no letters or no digits (years, junk like "Rev", "Formulación")
    has_letter = metros['proyecto'].str.contains(r'[A-Za-z]', regex=True, na=False)
    has_digit  = metros['proyecto'].str.contains(r'\d',       regex=True, na=False)
    metros = metros[has_letter & has_digit].copy()

    # Build nombre map: parquet ID_BLANCO → NOM_RESUMEN_ID_BLANCO from dim_proyectos
    nombre_map: dict[str, str] = {}
    try:
        from app.data.sql_server import query_df as _qdf
        dim = _qdf("SELECT ID_BLANCO, NOM_RESUMEN_ID_BLANCO FROM dbo.dim_proyectos")
        for _, r in dim.iterrows():
            k = str(r['ID_BLANCO'] or '').strip()
            v = str(r['NOM_RESUMEN_ID_BLANCO'] or '').strip()
            if k and v and v.lower() not in ('nan', 'none', ''):
                nombre_map[k] = v
    except Exception:
        pass

    # For each proyecto, pick the ID_BLANCO with the most meters (most representative blanco)
    blanco_per_proy: dict[str, str] = {}
    if 'ID_BLANCO' in metros.columns:
        bp = (
            metros.groupby(['proyecto', 'ID_BLANCO'])['ctd'].sum()
            .reset_index().sort_values('ctd', ascending=False)
        )
        for p, grp_p in bp.groupby('proyecto'):
            blanco_per_proy[str(p)] = str(grp_p.iloc[0]['ID_BLANCO']).strip()

    grp = metros.groupby(['proyecto', 'tipo']).agg(
        metros_ppt=('ctd', 'sum'),
        ppt_usd=('ppt_usd', 'sum'),
    ).reset_index()

    grp['usd_por_metro'] = np.where(
        grp['metros_ppt'] > 0,
        (grp['ppt_usd'] / grp['metros_ppt']).round(1),
        np.nan,
    )

    pivot: dict = {}
    for _, row in grp.iterrows():
        p = row['proyecto']
        t = row['tipo'].lower()
        if p not in pivot:
            blanco = blanco_per_proy.get(p, p)
            nombre = nombre_map.get(blanco) or nombre_map.get(p, '')
            pivot[p] = {'proyecto': p, 'nombre': nombre}
        pivot[p][f'metros_{t}']       = round(float(row['metros_ppt']), 1)
        pivot[p][f'ppt_usd_{t}']      = round(float(row['ppt_usd']), 0)
        upm = row['usd_por_metro']
        pivot[p][f'usd_por_metro_{t}'] = round(float(upm), 1) if not np.isnan(upm) else None

    proyectos = sorted(pivot.values(), key=lambda x: x['proyecto'])

    tot_m_ar  = sum(p.get('metros_ar',  0) for p in proyectos)
    tot_m_dhh = sum(p.get('metros_dhh', 0) for p in proyectos)
    tot_p_ar  = sum(p.get('ppt_usd_ar',  0) for p in proyectos)
    tot_p_dhh = sum(p.get('ppt_usd_dhh', 0) for p in proyectos)

    totales = {
        'metros_ar':       round(tot_m_ar,  1),
        'metros_dhh':      round(tot_m_dhh, 1),
        'ppt_usd_ar':      round(tot_p_ar,  0),
        'ppt_usd_dhh':     round(tot_p_dhh, 0),
        'usd_por_metro_ar':  round(tot_p_ar  / tot_m_ar,  1) if tot_m_ar  > 0 else None,
        'usd_por_metro_dhh': round(tot_p_dhh / tot_m_dhh, 1) if tot_m_dhh > 0 else None,
    }

    return {'proyectos': proyectos, 'totales': totales}


@router.get("/cruce/debug")
def get_cruce_debug():
    """Muestra qué códigos de proyecto existen en PAI vs Avance y cuántos cruzan."""
    from app.data.sql_server import query_df_bi, get_pai_proyectos, get_avance_semanal
    import pandas as pd

    pai = pd.DataFrame(get_pai_proyectos())
    av  = pd.DataFrame(get_avance_semanal())

    def find_proj_col(df: pd.DataFrame, hints: list[str]) -> str | None:
        for hint in hints:
            for c in df.columns:
                if hint in c:
                    non_empty = df[c].replace('', pd.NA).dropna()
                    if len(non_empty) > 0:
                        return c
        return None

    pai_col = find_proj_col(pai, ['project', 'blanco'])
    av_col  = find_proj_col(av,  ['project', 'proyecto'])

    pai_vals = set(pai[pai_col].dropna().astype(str).unique()) if pai_col else set()
    av_vals  = set(av[av_col].dropna().replace('', pd.NA).dropna().astype(str).unique()) if av_col else set()

    return {
        "pai_columna_detectada": pai_col,
        "av_columna_detectada":  av_col,
        "pai_valores_unicos":    sorted(pai_vals)[:50],
        "av_valores_unicos":     sorted(av_vals)[:50],
        "en_pai_no_en_av":       sorted(pai_vals - av_vals)[:30],
        "en_av_no_en_pai":       sorted(av_vals  - pai_vals)[:30],
        "cruzan":                sorted(pai_vals & av_vals)[:30],
    }


@router.get("/avance/debug")
def get_avance_debug():
    """Muestra columnas y muestra de valores de Avance_Semanal para diagnosticar claves de proyecto."""
    from app.data.sql_server import query_df_bi

    df = query_df_bi("SELECT TOP 50 * FROM dbo.Avance_Semanal")
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]

    return {
        "columnas": list(df.columns),
        "muestra_10_filas": df.head(10).fillna('').to_dict('records'),
        "valores_unicos_por_columna": {
            col: sorted(df[col].dropna().astype(str).unique().tolist())[:20]
            for col in df.columns
            if df[col].nunique() < 50
        },
    }


@router.get("/pai/debug")
def get_pai_debug():
    """Muestra estructura cruda de PAI_Proyectos para diagnosticar duplicados."""
    from app.data.sql_server import query_df_bi
    import pandas as pd

    df = query_df_bi("SELECT * FROM dbo.PAI_Proyectos")
    df.columns = [c.strip().lower().replace(' ', '_') for c in df.columns]

    info: dict = {
        "total_filas": len(df),
        "columnas": list(df.columns),
    }

    # Columnas clave para dedup
    for key in df.columns:
        if any(w in key for w in ('version', 'fecha_carga', 'ano', 'año', 'anio')):
            try:
                unicos = sorted(df[key].dropna().astype(str).unique().tolist())
                info[f"valores_{key}"] = unicos[:30]
            except Exception:
                pass

    # Columnas de metros: suma total sin filtro
    metro_cols = [c for c in df.columns if 'metro' in c]
    info["columnas_metros"] = metro_cols
    for mc in metro_cols:
        df[mc] = pd.to_numeric(df[mc], errors='coerce').fillna(0)
        info[f"suma_{mc}"] = round(float(df[mc].sum()), 1)

    # Conteo de filas por combinación de identificadores clave
    key_cols = [c for c in df.columns if any(w in c for w in ('version', 'fecha_carga', 'ano', 'año', 'anio'))]
    if key_cols:
        info["conteo_por_claves"] = (
            df.groupby(key_cols).size().reset_index(name='n')
            .sort_values('n', ascending=False)
            .head(20)
            .to_dict('records')
        )

    return info


@router.get("/curva-s")
def get_curva_s(tipo: str = "all", ano: int | None = None):
    """
    Monthly S-curve time series: physical (PAI vs Avance) + financial (PPT vs EPs).
    tipo: 'all' | 'AR' | 'DHH'
    ano:  calendar year filter (e.g. 2025); None = all years
    """
    import pandas as pd
    import numpy as np
    from datetime import date

    # ── A. Financial planned from parquet ──────────────────────────────────────
    df_pq = pd.read_parquet(_PARQUET)
    mdf = df_pq[df_pq['UN'].astype(str).str.lower() == 'metro'].copy()

    mdf['_fecha_extraccion'] = pd.to_datetime(mdf['_fecha_extraccion'], errors='coerce')
    latest = mdf.groupby('_codigo_proyecto')['_fecha_extraccion'].max().reset_index(name='_mx')
    mdf = mdf.merge(latest, on='_codigo_proyecto')
    mdf = mdf[mdf['_fecha_extraccion'] == mdf['_mx']].drop(columns='_mx')

    mdf['_tipo'] = mdf['DESCRIPCION'].apply(_classify_tipo)
    mdf = mdf[mdf['_tipo'].notna()].copy()

    tipo_up = tipo.upper()
    if tipo_up in ('AR', 'DHH'):
        mdf = mdf[mdf['_tipo'] == tipo_up].copy()

    # Project codes for this tipo (used to try to filter PAI/Avance)
    proy_codes: set[str] = set(
        mdf['_codigo_proyecto'].str.replace(r'^\d{2}\.', '', regex=True).str.strip().unique()
    )

    date_cols = [c for c in mdf.columns if len(c) == 10 and c[:4].isdigit()]
    if ano is not None:
        date_cols = [c for c in date_cols if c[:4] == str(ano)]
    mdf[date_cols] = mdf[date_cols].apply(pd.to_numeric, errors='coerce').fillna(0)

    ppt_by_month: dict[str, float] = {}
    for col in date_cols:
        mes = col[:7]
        val = float(mdf[col].sum())
        if val:
            ppt_by_month[mes] = ppt_by_month.get(mes, 0) + val
    total_ppt_usd = sum(ppt_by_month.values())

    # ── B. Physical planned from PAI ───────────────────────────────────────────
    from app.data.sql_server import get_pai_proyectos, get_avance_semanal

    pai_raw = pd.DataFrame(get_pai_proyectos())
    pai_by_month: dict[str, float] = {}

    if not pai_raw.empty and 'mes' in pai_raw.columns:
        metro_col = next(
            (c for c in pai_raw.columns if 'metro' in c and 'pai' in c),
            next((c for c in pai_raw.columns if 'metro' in c and 'real' not in c), None),
        )
        proj_col = next(
            (c for c in pai_raw.columns if 'project' in c.lower()),
            next((c for c in pai_raw.columns if 'blanco' in c.lower()), None),
        )
        if metro_col:
            pai_raw[metro_col] = pd.to_numeric(pai_raw[metro_col], errors='coerce').fillna(0)
            for _, r in pai_raw.iterrows():
                mes = str(r.get('mes', ''))[:7]
                if not mes or mes < '2020-01':
                    continue
                if ano is not None and mes[:4] != str(ano):
                    continue
                # Try to filter by tipo project codes; skip row if no match
                if tipo_up in ('AR', 'DHH') and proj_col:
                    pv = str(r.get(proj_col, '')).strip()
                    pv_norm = pv.split('.')[-1].strip() if '.' in pv else pv
                    if pv not in proy_codes and pv_norm not in proy_codes:
                        continue
                pai_by_month[mes] = pai_by_month.get(mes, 0) + float(r[metro_col])

        # Fallback: if tipo filter left nothing, use all PAI rows
        if tipo_up in ('AR', 'DHH') and not pai_by_month and metro_col:
            for _, r in pai_raw.iterrows():
                mes = str(r.get('mes', ''))[:7]
                if mes and mes >= '2020-01' and (ano is None or mes[:4] == str(ano)):
                    pai_by_month[mes] = pai_by_month.get(mes, 0) + float(r[metro_col])

    total_pai = sum(pai_by_month.values())

    # ── C. Physical actual from Avance_Semanal ─────────────────────────────────
    av_raw = pd.DataFrame(get_avance_semanal())
    av_by_month: dict[str, float] = {}

    if not av_raw.empty:
        fecha_col = next(
            (c for c in av_raw.columns if 'fecha' in c and 'carga' not in c), None
        )
        metro_av = next(
            (c for c in av_raw.columns if 'metro' in c and ('avance' in c or 'real' in c)),
            next((c for c in av_raw.columns if 'metro' in c), None),
        )
        if fecha_col and metro_av:
            av_raw[metro_av] = pd.to_numeric(av_raw[metro_av], errors='coerce').fillna(0)
            for _, r in av_raw.iterrows():
                mes = str(r.get(fecha_col, ''))[:7]
                if mes and mes >= '2020-01' and (ano is None or mes[:4] == str(ano)):
                    av_by_month[mes] = av_by_month.get(mes, 0) + float(r[metro_av])

    total_av = sum(av_by_month.values())

    # ── D. Financial actual from SIC_EPs (best-effort, filtered to perforación) ─
    from app.data.sql_server import query_df

    ep_by_month: dict[str, float] = {}
    ep_filter_info = 'none'

    try:
        ep = query_df("SELECT * FROM dbo.SIC_EPs")
        ep.columns = [c.strip() for c in ep.columns]

        # Detect project column and filter to known perforación project codes
        prj_col = next((c for c in ep.columns if 'proyecto' in c.lower()), None)
        ep_work = ep.copy()
        if prj_col and proy_codes:
            masked = ep[ep[prj_col].astype(str).str.strip().isin(proy_codes)]
            if not masked.empty:
                ep_work = masked
                ep_filter_info = 'by_proyecto'

        # Date: prefer HES date, fallback to Fecha Hasta
        fecha_ep_col = next(
            (c for c in ep_work.columns if 'hes' in c.lower()),
            next((c for c in ep_work.columns if 'hasta' in c.lower()), None),
        )

        if fecha_ep_col:
            ep_work = ep_work.copy()
            ep_work['_fep'] = pd.to_datetime(ep_work[fecha_ep_col], errors='coerce')
            ep_work = ep_work.dropna(subset=['_fep'])
            ep_work['_mes'] = ep_work['_fep'].dt.strftime('%Y-%m')

            monto_col  = next((c for c in ep_work.columns if c.lower() == 'monto'), None)
            cambio_col = next((c for c in ep_work.columns if 'cambio' in c.lower()), None)

            if monto_col:
                monto_s = pd.to_numeric(ep_work[monto_col], errors='coerce').fillna(0)
                if cambio_col:
                    cambio_s = pd.to_numeric(ep_work[cambio_col], errors='coerce').fillna(1).replace(0, 1)
                    # cambio > 10 → Monto already in foreign (USD); cambio ≈ 1 → Monto in CLP
                    monto_usd = np.where(cambio_s > 10, monto_s, monto_s / 950.0)
                else:
                    monto_usd = monto_s.values

                ep_work = ep_work.copy()
                ep_work['_usd'] = monto_usd
                if ano is not None:
                    ep_work = ep_work[ep_work['_fep'].dt.year == ano]
                for mes, grp in ep_work.groupby('_mes'):
                    ep_by_month[str(mes)] = float(grp['_usd'].sum())
    except Exception:
        pass

    total_ep_usd = sum(ep_by_month.values())

    # ── Build monthly time series ───────────────────────────────────────────────
    hoy = date.today().strftime('%Y-%m')
    sparse = sorted(set(
        list(ppt_by_month) + list(pai_by_month) +
        list(av_by_month)  + list(ep_by_month)
    ))
    sparse = [m for m in sparse if '2020-01' <= m <= '2030-12']

    # Fill every calendar month between first and last so cumulative curves have no gaps
    def _fill_months(months: list[str]) -> list[str]:
        if not months:
            return months
        sy, sm = int(months[0][:4]),  int(months[0][5:7])
        ey, em = int(months[-1][:4]), int(months[-1][5:7])
        out, y, mo = [], sy, sm
        while (y, mo) <= (ey, em):
            out.append(f'{y:04d}-{mo:02d}')
            mo += 1
            if mo > 12:
                mo, y = 1, y + 1
        return out

    all_months = _fill_months(sparse)

    cum_ppt = cum_pai = cum_av = cum_ep = 0.0
    result: list[dict] = []

    for m in all_months:
        cum_ppt += ppt_by_month.get(m, 0)
        cum_pai += pai_by_month.get(m, 0)
        es_futuro = m > hoy
        if not es_futuro:
            cum_av += av_by_month.get(m, 0)
            cum_ep += ep_by_month.get(m, 0)

        result.append({
            'mes':          m,
            'ppt_usd_mes':  round(ppt_by_month.get(m, 0), 0),
            'pai_metros_mes': round(pai_by_month.get(m, 0), 1),
            'av_metros_mes':  round(av_by_month.get(m, 0), 1) if not es_futuro else None,
            'ep_usd_mes':     round(ep_by_month.get(m, 0), 0) if not es_futuro else None,
            'cum_ppt_usd':    round(cum_ppt, 0),
            'cum_pai_metros': round(cum_pai, 1),
            'cum_av_metros':  round(cum_av, 1)  if not es_futuro else None,
            'cum_ep_usd':     round(cum_ep, 0)  if not es_futuro else None,
            'pct_ppt_fin': round(cum_ppt / total_ppt_usd * 100, 1) if total_ppt_usd > 0 else None,
            'pct_pai_fis': round(cum_pai / total_pai * 100, 1)     if total_pai > 0 else None,
            'pct_av_fis':  round(cum_av  / total_pai * 100, 1)     if (not es_futuro and total_pai > 0) else None,
            'pct_ep_fin':  round(cum_ep  / total_ep_usd * 100, 1)  if (not es_futuro and total_ep_usd > 0) else None,
            'es_futuro':   es_futuro,
        })

    # ── Projections: continue actual curves into future months ───────────────────
    def _months_diff(a: str, b: str) -> int:
        """Positive calendar-month distance from a to b."""
        ay, am = int(a[:4]), int(a[5:7])
        by_, bm = int(b[:4]), int(b[5:7])
        return (by_ - ay) * 12 + (bm - am)

    def _avg_recent_rate(month_dict: dict, up_to: str, n: int = 3) -> float:
        """Average of the last n months (≤ up_to) that have non-zero values."""
        recent = sorted(m for m in month_dict if m <= up_to and month_dict[m] > 0)[-n:]
        return sum(month_dict[m] for m in recent) / len(recent) if recent else 0.0

    avg_av_rate = _avg_recent_rate(av_by_month, hoy)
    avg_ep_rate = _avg_recent_rate(ep_by_month, hoy)

    # Find the bridge month: last non-future month in result, and its cumulative actuals
    bridge_month  = None
    cum_av_bridge = 0.0
    cum_ep_bridge = 0.0
    for item in result:
        if not item['es_futuro']:
            bridge_month  = item['mes']
            cum_av_bridge = float(item['cum_av_metros'] or 0)
            cum_ep_bridge = float(item['cum_ep_usd']    or 0)

    # Add projection fields to every data point
    for item in result:
        m = item['mes']
        if bridge_month is None or m < bridge_month:
            item['pct_av_fis_proy'] = None
            item['pct_ep_fin_proy'] = None
        elif m == bridge_month:
            # Bridge: projected starts where actual ends (continuity)
            item['pct_av_fis_proy'] = item['pct_av_fis']
            item['pct_ep_fin_proy'] = item['pct_ep_fin']
        else:
            # Future: linear projection from bridge using recent rate
            delta = _months_diff(bridge_month, m)
            item['pct_av_fis_proy'] = (
                round(min(cum_av_bridge + avg_av_rate * delta, total_pai) / total_pai * 100, 1)
                if avg_av_rate > 0 and total_pai > 0 else None
            )
            item['pct_ep_fin_proy'] = (
                round(min(cum_ep_bridge + avg_ep_rate * delta, total_ep_usd) / total_ep_usd * 100, 1)
                if avg_ep_rate > 0 and total_ep_usd > 0 else None
            )

    return {
        'data':       result,
        'totales': {
            'ppt_usd':    round(total_ppt_usd, 0),
            'pai_metros': round(total_pai, 1),
            'av_metros':  round(total_av, 1),
            'ep_usd':     round(total_ep_usd, 0),
        },
        'tiene_ep':      total_ep_usd > 0,
        'tiene_avance':  total_av > 0,
        'ep_filter':     ep_filter_info,
        'avg_av_rate':   round(avg_av_rate, 1),
        'avg_ep_rate':   round(avg_ep_rate, 0),
    }
