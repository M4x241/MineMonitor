# Mine Explorer 3D

## Objetivo

Desarrollar un prototipo funcional de software para el mapeo y la exploración autónoma en entornos subterráneos simulados, utilizando teoría de la información (Entropía de Shannon) para la planificación de trayectorias (Next-Best-View) y redes neuronales convolucionales 3D (3D-CNN) para la segmentación semántica del terreno y la detección de riesgos estructurales.

## Stack Tecnológico

| Componente | Tecnología | Función |
|---|---|---|
| Simulador | Minecraft Paper 26.2 (headless) | Generación del entorno subterráneo, raycasting LiDAR y obtención de ground truth |
| Backend API | Python 3.14 + FastAPI + Uvicorn | REST API, autenticación JWT, lógica de exploración y puente con Minecraft |
| Puente Minecraft | WebSocket (websockets 17) | Comunicación con el plugin de Paper (movimiento, raycast, bloques) |
| Base de Datos | SQLite + aiosqlite | Almacenamiento de usuarios, minas, misiones, voxeles y checkpoints |
| IA | PyTorch + 3D-CNN | Segmentación y detección de riesgos |
| Cliente | Electron + Three.js | Aplicación de escritorio para gestión, visualización y exportación 3D |

## Arquitectura del Sistema

```
Aplicación Electron  <-->  FastAPI Backend  <-->  WebSocket  <-->  Paper Server (Minecraft)
        |                        |                                      |
     UI + Visor 3D           SQLite DB                            ExplorerMob (VEX)
                            /auth, /mines, /missions            LiDAR / Raycast
                            /mc, /exports                       Generador de cuevas
```

## Estructura del Proyecto

```
Minecraft/
├── electron-app/        # Aplicación Electron (única interfaz)
│   ├── main.js          # Proceso principal y puente IPC
│   ├── preload.js       # ContextBridge
│   └── renderer/        # Interfaz (index.html, viewer3d.js)
├── backend/             # API FastAPI
│   ├── app/             # Código de la aplicación
│   │   ├── routers/     # Endpoints REST
│   │   ├── mc_launcher.py
│   │   ├── mc_client.py
│   │   └── exporters.py
│   ├── db/              # Esquema SQLite
│   └── data/            # Datos persistentes
└── manuales/            # Documentación del proyecto
```

## Requisitos

- Node.js + npm
- Python 3.14
- Servidor Minecraft Paper 26.2 (opcional para ejecución completa)

## Cómo ejecutar el proyecto

### 1) Backend (FastAPI)

```bash
cd backend
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
# .venv\Scripts\activate   # Windows
pip install -r requirements.txt
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

### 2) Aplicación Electron

```bash
cd electron-app
npm install
npm start
```

La aplicación se conectará automáticamente al backend en `http://127.0.0.1:8000`. Toda la funcionalidad está disponible exclusivamente desde la aplicación Electron.
