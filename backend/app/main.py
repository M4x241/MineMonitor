from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import init_db_sync, reconcile_on_startup
from .routers import auth, exports, mc, mines, missions


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
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(mines.router)
app.include_router(missions.router)
app.include_router(exports.router)
app.include_router(mc.router)


@app.get("/health")
async def health():
    return {"status": "ok"}
