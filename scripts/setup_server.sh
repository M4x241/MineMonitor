#!/usr/bin/env bash
# Descarga el servidor Paper (gratuito, sin licencia: solo se necesita para el servidor)
# y prepara eula.txt + server.properties en modo offline (sin cliente).
set -euo pipefail

cd "$(dirname "$0")/.."
SERVER_DIR="$(pwd)/server"
mkdir -p "$SERVER_DIR/plugins"

PAPER_URL="${PAPER_URL:-}"
if [[ -z "$PAPER_URL" ]]; then
    echo ">>> Resolviendo la última build estable de Paper 26.2..."
    PAPER_URL=$(curl -s --max-time 60 "https://papermc.io/downloads/paper" \
        | grep -oE 'https://fill-data\.papermc\.io/v1/objects/[a-f0-9]{64}/paper-26\.2-[0-9]+\.jar' \
        | sort -t- -k3 -n | tail -1)
fi
echo ">>> Descargando: $PAPER_URL"
curl -L --max-time 600 -o "$SERVER_DIR/paper.jar" "$PAPER_URL"
ls -lh "$SERVER_DIR/paper.jar"

echo ">>> Aceptando EULA..."
cat > "$SERVER_DIR/eula.txt" <<'EOF'
#By changing the setting below to TRUE you are indicating your agreement to our EULA (https://aka.ms/MinecraftEULA).
eula=true
EOF

echo ">>> Configurando server.properties (offline, mundo plano de piedra)..."
cat > "$SERVER_DIR/server.properties" <<'EOF'
#Minecraft server properties
online-mode=false
level-name=mine
level-type=flat
generator-settings={"layers":[{"block":"minecraft:bedrock","height":1},{"block":"minecraft:stone","height":120}],"biome":"minecraft:plains"}
motd=Mine Explorer Simulator
difficulty=peaceful
gamemode=spectator
max-players=0
spawn-protection=0
view-distance=8
simulation-distance=6
enable-command-block=false
level-seed=1704
EOF

echo ">>> Entorno listo en $SERVER_DIR"
echo ">>> Ejecuta:  bash scripts/run_server.sh"