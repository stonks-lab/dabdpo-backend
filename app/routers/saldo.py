from fastapi import APIRouter, Query
from typing import Optional
from app.metrics.calculations import saldo_por_contrato, home_kpis, saldo_evolucion

router = APIRouter(prefix="/saldo", tags=["saldo"])


@router.get("/kpis")
def get_saldo_kpis():
    k = home_kpis()
    return {
        "presupuesto_total_clp": k["presupuesto_total_clp"],
        "ep_consumido_clp":      k["ep_consumido_clp"],
        "saldo_real_clp":        k["saldo_real_clp"],
        "pct_consumido":         k["pct_consumido"],
        "contratos_criticos":    k["contratos_criticos"],
        "contratos_advertencia": k["contratos_advertencia"],
    }


@router.get("/contratos")
def get_contratos(
    estado_saldo: Optional[str] = Query(None),
    area:         Optional[str] = Query(None),
    tipo:         Optional[str] = Query(None),
    estado_ctt:   Optional[str] = Query(None),
    contrato:     Optional[str] = Query(None),
    alcance:      Optional[str] = Query(None),
    proyecto:     Optional[str] = Query(None),
):
    df = saldo_por_contrato()
    if estado_saldo:
        df = df[df["Estado_Saldo"] == estado_saldo]
    if area:
        df = df[df["Area_contrato"] == area]
    if tipo:
        df = df[df["Tipo_contrato"] == tipo]
    if estado_ctt:
        df = df[df["Estado_ctt"] == estado_ctt]
    if contrato:
        codes = [c.strip() for c in contrato.split(",") if c.strip()]
        if len(codes) > 1:
            df = df[df["codigo_ctt"].isin(codes)]
        else:
            mask = (
                df["codigo_ctt"].str.contains(codes[0], case=False, na=False) |
                df["Descripcion_ctt"].str.contains(codes[0], case=False, na=False)
            )
            df = df[mask]
    if alcance and "alcance" in df.columns:
        df = df[df["alcance"] == alcance]
    if proyecto:
        from app.data.loader import load_ep
        ep = load_ep()
        proj_col = next((c for c in ep.columns if c.lower() == "proyecto"), None)
        if proj_col:
            ctts = set(ep[ep[proj_col] == proyecto]["codigo_ctt"].tolist())
            df = df[df["codigo_ctt"].isin(ctts)]

    return df.fillna("").sort_values("Pct_Saldo").to_dict("records")


@router.get("/filtros")
def get_filtros():
    df = saldo_por_contrato()
    alcances = (
        sorted(df["alcance"].dropna().replace("", float("nan")).dropna().unique().tolist())
        if "alcance" in df.columns else []
    )
    return {
        "areas":         sorted(df["Area_contrato"].dropna().unique().tolist()),
        "tipos":         sorted(df["Tipo_contrato"].dropna().unique().tolist()),
        "estados_ctt":   sorted(df["Estado_ctt"].dropna().unique().tolist()),
        "estados_saldo": ["NEGATIVO", "CRÍTICO", "ADVERTENCIA", "ATENCIÓN", "OK"],
        "alcances":      alcances,
    }


@router.get("/evolucion")
def get_evolucion():
    return saldo_evolucion()


@router.get("/timeline")
def get_timeline(
    area:         Optional[str] = Query(None),
    tipo:         Optional[str] = Query(None),
    estado_ctt:   Optional[str] = Query(None),
    contrato:     Optional[str] = Query(None),
    estado_saldo: Optional[str] = Query(None),
    alcance:      Optional[str] = Query(None),
    meses_hist:   int           = Query(12),
    meses_fut:    int           = Query(18),
    limite:       int           = Query(60),
):
    from app.metrics.calculations import saldo_timeline
    return saldo_timeline(
        area=area or '',
        tipo=tipo or '',
        estado_ctt=estado_ctt or '',
        contrato=contrato or '',
        estado_saldo=estado_saldo or '',
        alcance=alcance or '',
        meses_hist=meses_hist,
        meses_fut=meses_fut,
        limite=limite,
    )
