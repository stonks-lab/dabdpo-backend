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
