from pathlib import Path
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from app.routers import home, saldo, licitaciones, alertas, proyecciones, analisis, sourcing, upload, estado_proyectos, metros, monday, dpo, simulacion, ep

app = FastAPI(title="Tablero DAB-DPO API", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(home.router,         prefix="/api")
app.include_router(saldo.router,        prefix="/api")
app.include_router(licitaciones.router, prefix="/api")
app.include_router(alertas.router,      prefix="/api")
app.include_router(proyecciones.router, prefix="/api")
app.include_router(analisis.router,     prefix="/api")
app.include_router(sourcing.router,     prefix="/api")
app.include_router(upload.router,           prefix="/api")
app.include_router(estado_proyectos.router, prefix="/api")
app.include_router(metros.router,           prefix="/api")
app.include_router(monday.router,           prefix="/api")
app.include_router(dpo.router,              prefix="/api")
app.include_router(simulacion.router,       prefix="/api")
app.include_router(ep.router,               prefix="/api")


@app.get("/api/health")
def health():
    return {"status": "ok"}


# ── Servir frontend (build de React) ─────────────────────────────────────────
DIST = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"

if DIST.exists():
    app.mount("/assets", StaticFiles(directory=str(DIST / "assets")), name="assets")

    @app.get("/")
    def serve_root():
        return FileResponse(str(DIST / "index.html"))

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        file = DIST / full_path
        if file.exists() and file.is_file():
            return FileResponse(str(file))
        return FileResponse(str(DIST / "index.html"))
