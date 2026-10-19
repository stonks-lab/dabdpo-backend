from fastapi import APIRouter

router = APIRouter(prefix="/metros", tags=["metros"])


@router.get("/pai")
def get_pai():
    from app.data.sql_server import get_pai_proyectos
    return get_pai_proyectos()


@router.get("/avance")
def get_avance():
    from app.data.sql_server import get_avance_semanal
    return get_avance_semanal()


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
