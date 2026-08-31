#!/usr/bin/env bash
# Arranca el backend FastAPI (auth + CRUD + puente WebSocket a Minecraft).
set -euo pipefail

cd "$(dirname "$0")/../backend"
VENV=".venv"
if [[ ! -d "$VENV" ]]; then
    echo ">>> Creando entorno virtual..."
    python3 -m venv "$VENV"
    "$VENV/bin/pip" install -q --upgrade pip
    "$VENV/bin/pip" install -q -r requirements.txt
fi

PORT="${PORT:-8000}"
echo ">>> Backend en http://127.0.0.1:$PORT  (docs: /docs)"
exec "$VENV/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$PORT" "$@"