from fastapi import APIRouter, Query
from typing import Optional, List
from app.data.sql_server import query_df_bi

router = APIRouter(prefix="/kpi-contratos", tags=["kpi-contratos"])

MAN_EXCLUIR = {'MAN. PREVENTIVA', 'MAN. CORRECTIVA', 'REPARACIÓN CORRECTIVA'}


def _build_where(
    fecha_col:   str,
    fecha_desde: Optional[str],
    fecha_hasta: Optional[str],
    maquinas:    List[str],
    proyectos:   List[str],
    holeids:     List[str],
    include_holeid: bool = True,
) -> tuple[str, list]:
    conds: list[str] = ["1=1"]
    params: list = []
    if fecha_desde:
        conds.append(f"{fecha_col} >= ?");  params.append(fecha_desde)
    if fecha_hasta:
        conds.append(f"{fecha_col} <= ?");  params.append(fecha_hasta)
    if maquinas:
        ph = ",".join(["?"] * len(maquinas))
        conds.append(f"MAQUINA IN ({ph})");  params.extend(maquinas)
    if proyectos:
        ph = ",".join(["?"] * len(proyectos))
        conds.append(f"PROJECTCODE IN ({ph})");  params.extend(proyectos)
    if include_holeid and holeids:
        ph = ",".join(["?"] * len(holeids))
        conds.append(f"HOLEID IN ({ph})");  params.extend(holeids)
    return " AND ".join(conds), params


def _mes_inicio(fecha: str) -> str:
    """'2025-10-15' → '2025-10-01'"""
    return fecha[:8] + "01"


@router.get("/maquinas")
def get_maquinas():
    try:
        df = query_df_bi(
            "SELECT DISTINCT MAQUINA FROM dbo.Brechas_actividades "
            "WHERE MAQUINA IS NOT NULL ORDER BY MAQUINA"
        )
        return df["MAQUINA"].dropna().tolist()
    except Exception:
        return []


@router.get("/proyectos")
def get_proyectos():
    try:
        df = query_df_bi(
            "SELECT DISTINCT PROJECTCODE FROM dbo.Brechas_actividades "
            "WHERE PROJECTCODE IS NOT NULL ORDER BY PROJECTCODE"
        )
        return df["PROJECTCODE"].dropna().tolist()
    except Exception:
        return []


@router.get("/holeids")
def get_holeids(proyectos: List[str] = Query(default=[])):
    """HOLEIDs filtrados por proyectos seleccionados (o todos si no se pasan proyectos)."""
    try:
        if proyectos:
            ph = ",".join(["?"] * len(proyectos))
            df = query_df_bi(
                f"SELECT DISTINCT HOLEID FROM dbo.Brechas_actividades "
                f"WHERE HOLEID IS NOT NULL AND PROJECTCODE IN ({ph}) ORDER BY HOLEID",
                tuple(proyectos),
            )
        else:
            df = query_df_bi(
                "SELECT DISTINCT HOLEID FROM dbo.Brechas_actividades "
                "WHERE HOLEID IS NOT NULL ORDER BY HOLEID"
            )
        return df["HOLEID"].dropna().tolist()
    except Exception:
        return []


@router.get("/disponibilidad")
def get_disponibilidad(
    fecha_desde: Optional[str] = Query(None),
    fecha_hasta: Optional[str] = Query(None),
    maquinas:    List[str]     = Query(default=[]),
    proyectos:   List[str]     = Query(default=[]),
    holeids:     List[str]     = Query(default=[]),
):
    resultado: dict = {
        "pct_maquina": None, "horas_total": 0, "horas_disponibles_maquina": 0,
        "pct_personal": None, "horas_espera_personal": 0,
        "pct_cumplimiento": None, "metros_avance": 0, "metros_pai": 0,
    }

    # ── Disponibilidad máquina y personal (Brechas_actividades) ──────────────
    try:
        where, params = _build_where("FECHA", fecha_desde, fecha_hasta, maquinas, proyectos, holeids)
        df = query_df_bi(
            f"SELECT ACTIVIDAD, SUM(DURACION) AS dur "
            f"FROM dbo.Brechas_actividades WHERE {where} GROUP BY ACTIVIDAD",
            tuple(params),
        )
        if not df.empty:
            df["dur"] = df["dur"].fillna(0)
            horas_total = float(df["dur"].sum())
            if horas_total > 0:
                horas_man      = float(df[df["ACTIVIDAD"].isin(MAN_EXCLUIR)]["dur"].sum())
                horas_disp_maq = horas_total - horas_man
                mask_persona   = df["ACTIVIDAD"].str.upper().str.contains("PERSONA", na=False)
                horas_persona  = float(df[mask_persona]["dur"].sum())
                resultado.update({
                    "pct_maquina":               round(horas_disp_maq / horas_total * 100, 1),
                    "horas_total":               round(horas_total, 1),
                    "horas_disponibles_maquina": round(horas_disp_maq, 1),
                    "pct_personal":              round((1 - horas_persona / horas_total) * 100, 1),
                    "horas_espera_personal":     round(horas_persona, 1),
                })
    except Exception:
        pass

    # ── Cumplimiento plan (Avance_Semanal vs PAI_Proyectos) ──────────────────
    try:
        # Metros avance — mismos filtros (HOLEID aplica aquí)
        where_av, params_av = _build_where(
            "FECHA", fecha_desde, fecha_hasta, maquinas, proyectos, holeids
        )
        df_av = query_df_bi(
            f"SELECT SUM(METROS_AVANCE) AS metros FROM dbo.Avance_Semanal WHERE {where_av}",
            tuple(params_av),
        )
        metros_avance = float(df_av.iloc[0]["metros"] or 0) if not df_av.empty else 0.0

        # Metros PAI — última versión por año, filtro por mes y proyecto (sin HOLEID ni MAQUINA)
        mes_desde = _mes_inicio(fecha_desde) if fecha_desde else None
        mes_hasta = _mes_inicio(fecha_hasta) if fecha_hasta else None

        pai_conds: list[str] = ["1=1"]
        pai_params: list = []
        if mes_desde:
            pai_conds.append("p.MES >= ?");  pai_params.append(mes_desde)
        if mes_hasta:
            pai_conds.append("p.MES <= ?");  pai_params.append(mes_hasta)
        if proyectos:
            ph = ",".join(["?"] * len(proyectos))
            pai_conds.append(f"p.PROJECTCODE IN ({ph})");  pai_params.extend(proyectos)

        pai_where = " AND ".join(pai_conds)
        df_pai = query_df_bi(
            f"""
            WITH latest AS (
                SELECT [AÑO_PAI], MAX([VERSION_PAI]) AS max_ver
                FROM dbo.PAI_Proyectos
                GROUP BY [AÑO_PAI]
            )
            SELECT SUM(p.METROS_PAI) AS metros
            FROM dbo.PAI_Proyectos p
            JOIN latest l ON p.[AÑO_PAI] = l.[AÑO_PAI] AND p.[VERSION_PAI] = l.max_ver
            WHERE {pai_where}
            """,
            tuple(pai_params),
        )
        metros_pai = float(df_pai.iloc[0]["metros"] or 0) if not df_pai.empty else 0.0

        pct_cumplimiento = round(metros_avance / metros_pai * 100, 1) if metros_pai > 0 else None
        resultado.update({
            "pct_cumplimiento": pct_cumplimiento,
            "metros_avance":    round(metros_avance, 1),
            "metros_pai":       round(metros_pai, 1),
        })
    except Exception:
        pass

    return resultado
