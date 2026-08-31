#!/usr/bin/env bash
# Apaga todo: servidor Minecraft + backend
echo "Apagando servidor Minecraft..."
fuser -k 25565/tcp 2>/dev/null && echo "  MC detenido" || echo "  MC ya estaba apagado"

echo "Apagando backend..."
fuser -k 8000/tcp 2>/dev/null && echo "  Backend detenido" || echo "  Backend ya estaba apagado"

echo "Listo."
