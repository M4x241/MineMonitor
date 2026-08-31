#!/usr/bin/env bash
# Compila el plugin, lo copia a plugins/ y arranca el servidor Paper.
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
SERVER_DIR="$ROOT/server"
PLUGIN_JAR="$ROOT/minecraft-plugin/build/libs/MineExplorer-0.1.0-all.jar"

if [[ ! -f "$SERVER_DIR/paper.jar" ]]; then
    echo ">>> No hay servidor; ejecuta primero: bash scripts/setup_server.sh"
    exit 1
fi

echo ">>> Compilando el plugin..."
(cd "$ROOT/minecraft-plugin" && gradle shadowJar --no-daemon -q)

echo ">>> Copiando plugin a $SERVER_DIR/plugins/"
cp -f "$PLUGIN_JAR" "$SERVER_DIR/plugins/"

cd "$SERVER_DIR"
echo ">>> Iniciando servidor Paper (headless, sin cliente). Ctrl+C para detener."
exec java -Xms1G -Xmx3G -jar paper.jar nogui