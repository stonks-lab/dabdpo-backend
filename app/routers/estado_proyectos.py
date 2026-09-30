from fastapi import APIRouter

router = APIRouter(prefix="/estado-proyectos", tags=["estado-proyectos"])


@router.get("/incidencias")
def get_incidencias():
    from app.data.sql_server import get_incidencias
    return get_incidencias()
