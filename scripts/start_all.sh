#!/usr/bin/env bash
# Arranca todo: servidor Minecraft + backend FastAPI
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

echo "========================================="
echo "  Mine Explorer - Arranque completo"
echo "========================================="

# 1. Servidor Minecraft
echo ""
echo "[1/2] Arrancando servidor Minecraft..."
cd "$ROOT/server"
if [[ ! -f paper.jar ]]; then
    echo "ERROR: No hay paper.jar. Ejecuta: bash scripts/setup_server.sh"
    exit 1
fi
fuser -k 25565/tcp 2>/dev/null || true
sleep 2
setsid --fork java -Xms1G -Xmx3G -jar paper.jar nogui </dev/null >/tmp/mc_server.log 2>&1 &
MC_PID=$!
echo "  Servidor MC iniciado (PID $MC_PID), puerto 25565"

# 2. Backend FastAPI
echo ""
echo "[2/2] Arrancando backend FastAPI..."
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
    echo "  Creando entorno virtual..."
    python3 -m venv .venv
    .venv/bin/pip install -q --upgrade pip
    .venv/bin/pip install -q -r requirements.txt
fi
fuser -k 8000/tcp 2>/dev/null || true
sleep 2
setsid --fork .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 </dev/null >/tmp/uvicorn_test.log 2>&1 &
API_PID=$!
echo "  Backend iniciado (PID $API_PID), puerto 8000"

sleep 4

# 3. Verificar
echo ""
echo "========================================="
echo "  Todo listo!"
echo "========================================="
echo ""
echo "  Swagger (API docs):   http://127.0.0.1:8000/docs"
echo "  Visor 3D (cueva):     http://127.0.0.1:8000/static/viewer.html"
echo ""
echo "  Logs:"
echo "    MC server:  /tmp/mc_server.log"
echo "    Backend:    /tmp/uvicorn_test.log"
echo ""
echo "  Para apagar todo:"
echo "    fuser -k 25565/tcp && fuser -k 8000/tcp"
echo ""
