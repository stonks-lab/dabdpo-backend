from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter(prefix="/simulacion", tags=["simulacion"])


class LineaIn(BaseModel):
    proyecto: str
    alcance: str = ''
    contrato_asignado: str
    tipo_asignacion: str
    saldo_inicial: Optional[float] = None
    moneda: Optional[str] = 'CLP'


class SimIn(BaseModel):
    nombre: str
    usuario: Optional[str] = None
    lineas: list[LineaIn]
    sim_id: Optional[int] = None


@router.get("/lista")
def get_lista():
    from app.data.sql_server import get_simulaciones
    return get_simulaciones()


@router.get("/datos/proyectos")
def get_proyectos():
    from app.data.sql_server import get_sim_proyectos
    return get_sim_proyectos()


@router.get("/datos/vendor-codes")
def get_vendor_codes(proyectos: List[str] = Query(...)):
    from app.data.sql_server import get_sim_vendor_codes
    return get_sim_vendor_codes(proyectos)


@router.get("/datos/dim-debug")
def get_dim_debug():
    from app.data.sql_server import query_df
    import pandas as pd

    result = {}
    for table in ('dbo.dim_inf_proyecto', 'dbo.dim_proyectos'):
        try:
            df = query_df(f"SELECT * FROM {table}")
            if df.empty:
                result[table] = {'status': 'vacía'}
                continue

            col_stats = {}
            for col in df.columns:
                vals = df[col].dropna().astype(str).str.strip()
                vals = vals[vals != '']
                if vals.empty:
                    col_stats[col] = {'vacios': True}
                    continue
                pct_alnum  = round(float(vals.str.match(r'^[A-Za-z0-9_]+$').mean()), 2)
                pct_spaces = round(float(vals.str.contains(r'\s', na=False).mean()), 2)
                avg_len    = round(float(vals.str.len().mean()), 1)
                avg_words  = round(float(vals.str.split().str.len().mean()), 1)
                col_stats[col] = {
                    'pct_alnum':  pct_alnum,
                    'pct_spaces': pct_spaces,
                    'avg_len':    avg_len,
                    'avg_words':  avg_words,
                    'muestra':    vals.head(3).tolist(),
                }

            result[table] = {'filas': len(df), 'columnas': col_stats}
        except Exception as e:
            result[table] = {'status': f'error: {e}'}

    return result


@router.get("/datos/contratos")
def get_contratos():
    from app.data.sql_server import get_sim_contratos
    return get_sim_contratos()


@router.get("/{sim_id}")
def get_sim(sim_id: int):
    from app.data.sql_server import get_simulacion_by_id
    sim = get_simulacion_by_id(sim_id)
    if not sim:
        raise HTTPException(404, "Simulación no encontrada")
    return sim


@router.post("/guardar")
def guardar(body: SimIn):
    from app.data.sql_server import save_simulacion
    lineas = [
        {
            'proyecto':          l.proyecto,
            'alcance':           l.alcance,
            'contrato_asignado': l.contrato_asignado,
            'tipo_asignacion':   l.tipo_asignacion,
            'saldo_inicial':     l.saldo_inicial,
            'moneda':            l.moneda,
        }
        for l in body.lineas
    ]
    return save_simulacion(body.nombre, body.usuario, lineas, body.sim_id)


@router.delete("/{sim_id}")
def eliminar(sim_id: int):
    from app.data.sql_server import delete_simulacion
    return delete_simulacion(sim_id)
