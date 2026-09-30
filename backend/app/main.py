from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from .db import init_db_sync, reconcile_on_startup
from .routers import auth, mc, mines, missions

STATIC_DIR = Path(__file__).parent.parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db_sync()
    reconcile_on_startup()
    yield


app = FastAPI(
    title="Mine Explorer API",
    description="Backend del proyecto: Exploración Autónoma de Minas Subterráneas "
                "(3D-CNN + Teoría de la Información sobre simulador Minecraft).",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(mines.router)
app.include_router(missions.router)
app.include_router(mc.router)


@app.get("/health")
async def health():
    return {"status": "ok"}


if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
