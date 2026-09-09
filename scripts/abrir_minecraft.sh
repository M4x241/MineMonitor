#!/usr/bin/env bash
# Abre Minecraft (Prism Launcher) conectado al servidor local en modo espectador
# para observar en vivo al dron explorador.
set -euo pipefail

PLAYER="${1:-Observador}"
SERVER="${2:-localhost:25565}"
INSTANCE="Explorador"

export DISPLAY="${DISPLAY:-:1}"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-1}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/1000}"
export GDK_BACKEND="${GDK_BACKEND:-wayland}"

echo "Lanzando Minecraft ($INSTANCE) como '$PLAYER' -> $SERVER ..."
flatpak run org.prismlauncher.PrismLauncher \
  -l "$INSTANCE" \
  -a "$PLAYER" \
  -o "$PLAYER" \
  -s "$SERVER"