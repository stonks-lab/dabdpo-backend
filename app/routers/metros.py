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


@router.get("/presupuesto-perforacion")
def get_presupuesto_perforacion():
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
