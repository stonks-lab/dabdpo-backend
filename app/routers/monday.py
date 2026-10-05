import httpx
from fastapi import APIRouter, HTTPException, Query
from app.config import MONDAY_API_KEY, MONDAY_BOARD_ID

router = APIRouter(prefix="/monday", tags=["monday"])

MONDAY_API_URL = "https://api.monday.com/v2"

# ── IDs de columnas del tablero "Modificaciones de contratos" ─────────────────
COL = {
    "solicitante":        "peoplewy0t2wpv",
    "administrador":      "peopleyusw8qiq",
    "supervisor":         "multiple_person_mm4w5ynj",
    "dpo":                "multiple_person_mm6h54ac",
    "pyc":                "multiple_person_mm4wk3jv",
    "fecha_solicitud":    "date7pyrn74z",
    "estado_mod":         "color_mm3rttbr",
    "estado_solicitud":   "color_mm4vfp6f",
    "estado_supervisor":  "color_mm3rakkx",
    "estado_dpo":         "color_mm6j3scf",
    "estado_pyc":         "single_selectsoc7ojl",
    "proyecto":           "single_selectalen0fl",
    "codigo_contrato":    "single_selectfmdk7s8",
    "tipo_servicio":      "single_selecteh1292y",
    "tipo_modificacion":  "multi_selectko8443pg",
    "impacto_plazo_mes":  "numbers935kzga",
    "impacto_monto_clp":  "number72dup34q",
    "impacto_alcance":    "short_texte3gpyx8x",
    "razon":              "multi_selecttfopgedf",
    "descripcion":        "short_textv3ewzpkm",
    "area_solicitante":   "single_selecth5jzzlw",
    "saldo_vigente":      "number1rjrhm7e",
    "fecha_firma_sup":    "date_mm5wxxh1",
    "fecha_firma_dpo":    "date_mm6hb71a",
    "fecha_firma_pyc":    "date_mm5w5y5",
}

GQL_ITEMS = """
query($board: [ID!]!, $cursor: String, $limit: Int!) {
  boards(ids: $board) {
    items_page(limit: $limit, cursor: $cursor) {
      cursor
      items {
        id
        name
        group { title }
        column_values {
          id
          text
        }
      }
    }
  }
}
"""


def _headers():
    return {
        "Authorization": MONDAY_API_KEY,
        "Content-Type":  "application/json",
        "API-Version":   "2024-01",
    }


def _fetch_all_items() -> list[dict]:
    if not MONDAY_API_KEY or not MONDAY_BOARD_ID:
        raise HTTPException(status_code=503, detail="MONDAY_API_KEY o MONDAY_BOARD_ID no configurados en .env")

    items: list[dict] = []
    cursor = None
    limit  = 200

    with httpx.Client(timeout=30, verify=False) as client:
        while True:
            variables: dict = {"board": [str(MONDAY_BOARD_ID)], "limit": limit}
            if cursor:
                variables["cursor"] = cursor

            resp = client.post(
                MONDAY_API_URL,
                headers=_headers(),
                json={"query": GQL_ITEMS, "variables": variables},
            )
            if resp.status_code != 200:
                raise HTTPException(status_code=502, detail=f"Monday API error {resp.status_code}")

            data = resp.json()
            if "errors" in data:
                raise HTTPException(status_code=502, detail=str(data["errors"]))

            page = data["data"]["boards"][0]["items_page"]
            for item in page["items"]:
                cv_map = {c["id"]: (c.get("text") or "") for c in item.get("column_values", [])}
                items.append({
                    "id":                item["id"],
                    "nombre":            item["name"],
                    "grupo":             item.get("group", {}).get("title", ""),
                    "solicitante":       cv_map.get(COL["solicitante"], ""),
                    "administrador":     cv_map.get(COL["administrador"], ""),
                    "supervisor":        cv_map.get(COL["supervisor"], ""),
                    "dpo":               cv_map.get(COL["dpo"], ""),
                    "pyc":               cv_map.get(COL["pyc"], ""),
                    "fecha_solicitud":   cv_map.get(COL["fecha_solicitud"], ""),
                    "estado_mod":        cv_map.get(COL["estado_mod"], ""),
                    "estado_solicitud":  cv_map.get(COL["estado_solicitud"], ""),
                    "estado_supervisor": cv_map.get(COL["estado_supervisor"], ""),
                    "estado_dpo":        cv_map.get(COL["estado_dpo"], ""),
                    "estado_pyc":        cv_map.get(COL["estado_pyc"], ""),
                    "proyecto":          cv_map.get(COL["proyecto"], ""),
                    "codigo_contrato":   cv_map.get(COL["codigo_contrato"], ""),
                    "tipo_servicio":     cv_map.get(COL["tipo_servicio"], ""),
                    "tipo_modificacion": cv_map.get(COL["tipo_modificacion"], ""),
                    "impacto_plazo_mes": _to_float(cv_map.get(COL["impacto_plazo_mes"], "")),
                    "impacto_monto_clp": _to_float(cv_map.get(COL["impacto_monto_clp"], "")),
                    "impacto_alcance":   cv_map.get(COL["impacto_alcance"], ""),
                    "razon":             cv_map.get(COL["razon"], ""),
                    "descripcion":       cv_map.get(COL["descripcion"], ""),
                    "area_solicitante":  cv_map.get(COL["area_solicitante"], ""),
                    "saldo_vigente":     _to_float(cv_map.get(COL["saldo_vigente"], "")),
                    "fecha_firma_sup":   cv_map.get(COL["fecha_firma_sup"], ""),
                    "fecha_firma_dpo":   cv_map.get(COL["fecha_firma_dpo"], ""),
                    "fecha_firma_pyc":   cv_map.get(COL["fecha_firma_pyc"], ""),
                })

            cursor = page.get("cursor")
            if not cursor or len(page["items"]) < limit:
                break

    return items


def _to_float(v: str) -> float:
    try:
        return float(str(v).replace(",", ".").replace(" ", "")) if v else 0.0
    except ValueError:
        return 0.0


def _estado_global(item: dict) -> str:
    """Devuelve el estado más relevante para mostrar en la tabla."""
    return item["estado_mod"] or item["estado_solicitud"] or "—"


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/kpis")
def get_kpis():
    items = _fetch_all_items()

    total      = len(items)
    aprobados  = sum(1 for i in items if "aprobad" in _estado_global(i).lower())
    pendientes = sum(1 for i in items if any(p in _estado_global(i).lower() for p in ["pendiente", "revisión", "revision", "espera", "proceso"]))
    rechazados = sum(1 for i in items if "rechaz" in _estado_global(i).lower())
    monto_total = sum(i["impacto_monto_clp"] for i in items)

    por_tipo: dict[str, int] = {}
    for i in items:
        for t in (i["tipo_modificacion"] or "Sin tipo").split(","):
            t = t.strip()
            if t:
                por_tipo[t] = por_tipo.get(t, 0) + 1

    por_proyecto: dict[str, int] = {}
    for i in items:
        p = i["proyecto"] or "Sin proyecto"
        por_proyecto[p] = por_proyecto.get(p, 0) + 1

    por_estado_dpo: dict[str, int] = {}
    for i in items:
        e = i["estado_dpo"] or "Sin estado"
        por_estado_dpo[e] = por_estado_dpo.get(e, 0) + 1

    return {
        "total":          total,
        "aprobados":      aprobados,
        "pendientes":     pendientes,
        "rechazados":     rechazados,
        "sin_clasificar": total - aprobados - pendientes - rechazados,
        "monto_total":    monto_total,
        "por_tipo":       [{"tipo": k, "cantidad": v} for k, v in sorted(por_tipo.items(), key=lambda x: -x[1])],
        "por_proyecto":   [{"proyecto": k, "cantidad": v} for k, v in sorted(por_proyecto.items(), key=lambda x: -x[1])],
        "por_estado_dpo": [{"estado": k, "cantidad": v} for k, v in sorted(por_estado_dpo.items(), key=lambda x: -x[1])],
    }


@router.get("/tabla")
def get_tabla(
    tipo:      str = Query(""),
    estado:    str = Query(""),
    proyecto:  str = Query(""),
    contrato:  str = Query(""),
    area:      str = Query(""),
):
    items = _fetch_all_items()

    if tipo:
        items = [i for i in items if tipo.lower() in i["tipo_modificacion"].lower()]
    if estado:
        items = [i for i in items if estado.lower() in _estado_global(i).lower()]
    if proyecto:
        items = [i for i in items if proyecto.lower() in i["proyecto"].lower()]
    if contrato:
        items = [i for i in items if contrato.lower() in (i["codigo_contrato"] + i["nombre"]).lower()]
    if area:
        items = [i for i in items if area.lower() in i["area_solicitante"].lower()]

    return [
        {
            "id":                i["id"],
            "nombre":            i["nombre"],
            "codigo_contrato":   i["codigo_contrato"],
            "proyecto":          i["proyecto"],
            "tipo_modificacion": i["tipo_modificacion"],
            "estado":            _estado_global(i),
            "estado_dpo":        i["estado_dpo"],
            "estado_supervisor": i["estado_supervisor"],
            "estado_pyc":        i["estado_pyc"],
            "impacto_monto_clp": i["impacto_monto_clp"],
            "impacto_plazo_mes": i["impacto_plazo_mes"],
            "impacto_alcance":   i["impacto_alcance"],
            "descripcion":       i["descripcion"],
            "solicitante":       i["solicitante"],
            "administrador":     i["administrador"],
            "area_solicitante":  i["area_solicitante"],
            "fecha_solicitud":   i["fecha_solicitud"],
            "fecha_firma_sup":   i["fecha_firma_sup"],
            "fecha_firma_dpo":   i["fecha_firma_dpo"],
            "fecha_firma_pyc":   i["fecha_firma_pyc"],
            "saldo_vigente":     i["saldo_vigente"],
        }
        for i in items
    ]


@router.get("/filtros")
def get_filtros():
    items = _fetch_all_items()

    tipos: set[str] = set()
    for i in items:
        for t in (i["tipo_modificacion"] or "").split(","):
            t = t.strip()
            if t:
                tipos.add(t)

    return {
        "tipos":     sorted(tipos),
        "estados":   sorted({_estado_global(i) for i in items if _estado_global(i) != "—"}),
        "proyectos": sorted({i["proyecto"] for i in items if i["proyecto"]}),
        "contratos": sorted({i["codigo_contrato"] for i in items if i["codigo_contrato"]}),
        "areas":     sorted({i["area_solicitante"] for i in items if i["area_solicitante"]}),
    }


@router.get("/tendencia")
def get_tendencia():
    items = _fetch_all_items()
    por_mes: dict[str, int] = {}
    for i in items:
        fecha = i["fecha_solicitud"]
        if fecha and len(fecha) >= 7:
            mes = fecha[:7]
            por_mes[mes] = por_mes.get(mes, 0) + 1
    return [{"mes": k, "cantidad": v} for k, v in sorted(por_mes.items())]
