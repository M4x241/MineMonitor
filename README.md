# Exploracion Autonoma de Minas Subterraneas

## Objetivo del Proyecto

Desarrollar un prototipo funcional de software para el **mapeo y la exploracion autonoma** en entornos subterraneos simulados, usando:

- **Teoria de la Informacion** (Entropia de Shannon) para planificar trayectorias optimas (Next-Best-View / NBV).
- **Redes Neuronales Convolucionales 3D (3D-CNN)** para segmentacion semantica del terreno y deteccion de riesgos estructurales en tiempo real.

El agente autonomo (dron) opera dentro de una cueva simulada en Minecraft, recolectando datos volumetricos (voxeles) que alimentan al pipeline de IA.

## Stack Tecnologico

| Componente | Tecnologia | Funcion |
|---|---|---|
| **Simulador** | Minecraft Paper 26.2 (headless, sin cliente) | Genera la cueva 3D, provee LiDAR (raycast) y datos de bloques |
| **Backend API** | Python 3.14 + FastAPI + uvicorn | REST API, auth JWT, puente WebSocket al simulador |
| **Puente MC** | WebSocket (websockets 17) | Comandos: move, teleport, raycast, blocks, ground_truth |
| **BD** | SQLite (aiosqlite) | Almacena usuarios, minas, misiones, voxeles (mapa 3D) |
| **IA (futuro)** | PyTorch + 3D-CNN | Entrenamiento de modelo de segmentacion de terreno |
| **Frontend (futuro)** | Electron + Three.js | Dashboard, login, visor 3D del mapa |

## Arquitectura del Sistema

```
Electron (futuro) <---> FastAPI Backend <---> WebSocket --> Minecraft Plugin
     |                       |                                    |
  Dashboard              SQLite DB                          Paper Server
  Login                  /auth/*                           Generador de cueva
  Visor 3D               /mines/*                         ExplorerMob (VEX)
                         /missions/*                      LiDAR (raycast)
                         /mc/*                            Ground truth
```

## Estructura del Proyecto

```
Minecraft/
├── electron-app/
│   ├── main.js              # Electron main process
│   ├── preload.js           # IPC bridge (contextBridge)
│   ├── renderer/
│   │   └── index.html       # Visor 3D (Three.js + Electron IPC)
│   ├── package.json
│   └── node_modules/
├── backend/
│   ├── app/
│   │   ├── main.py           # FastAPI app, lifespan, CORS
│   │   ├── config.py         # Variables de entorno
│   │   ├── db.py             # SQLite async (aiosqlite)
│   │   ├── security.py       # JWT + bcrypt
│   │   ├── models.py         # Pydantic schemas (request/response)
│   │   ├── mc_client.py      # WebSocket client → Paper plugin
│   │   └── routers/
│   │       ├── auth.py       # POST /register, /login, GET /me
│   │       ├── mines.py      # CRUD /mines
│   │       ├── missions.py   # CRUD /missions + start/stop + /map
│   │       └── mc.py         # /mc/status, move, teleport, raycast, blocks
│   ├── db/
│   │   └── schema.sql        # Esquema SQLite corregido
│   ├── data/
│   │   └── mine_explorer.db  # Base de datos (auto-creada)
│   ├── static/
│   │   └── viewer.html       # Visor 3D web (Three.js)
│   ├── gen_fake_cave.py      # Generador de cueva fake → DB
│   ├── requirements.txt
│   └── .venv/
├── minecraft-plugin/
│   ├── src/main/java/dev/mineexplorer/
│   │   ├── MineExplorerPlugin.java   # Plugin principal + watchdog
│   │   ├── MineGenerator.java        # Generador determinista de cueva
│   │   ├── ExplorerMob.java          # Control del dron (VEX NoAI)
│   │   └── MineWsServer.java         # Servidor WebSocket + DDA raycast
│   └── build/libs/MineExplorer-0.1.0-all.jar
├── server/
│   ├── paper.jar                     # Paper 26.2 build 107
│   ├── eula.txt
│   └── server.properties
├── scripts/
│   ├── setup_server.sh       # Descarga Paper + configura
│   ├── run_server.sh         # Compila plugin + arranca server
│   ├── run_backend.sh        # Arranca FastAPI
│   ├── start_all.sh          # Arranca todo (MC + backend)
│   └── stop_all.sh           # Apaga todo
├── dbscript.js                       # Esquema SQL original del usuario
└── README.md                         # Este archivo
```

## Base de Datos (SQLite)

### Tabla `voxeles` — Mapa 3D de la cueva

| Campo | Tipo | Descripcion |
|---|---|---|
| mission_id | int (FK) | Mision a la que pertenece |
| coord_x, coord_y, coord_z | int | Posicion 3D del voxel (PK compuesta) |
| state | 0/1/2 | 0=Sin explorar, 1=Explorado, 2=Bloqueado |
| risk_label | 0/1/2/3 | 0=Seguro, 1=Riesgo medio, 2=Riesgo alto, 3=Escombros |
| confidence | float | Confianza del modelo (0-1) |
| entropy_value | float | Entropia de Shannon local (1.0 = maxima incertidumbre) |
| mission_step | int | Paso de la mision en que se registro |

### Estados de una mision

```
pending → in_progress → completed
                  ↕
                paused
                  ↓
              canceled
```

## Como Ejecutar

### 1. Arrancar todo (recomendado)

```bash
bash scripts/start_all.sh
# Visor 3D: http://127.0.0.1:8000/static/viewer.html
# Swagger:  http://127.0.0.1:8000/docs
```

### 2. Visor de escritorio (Electron)

```bash
cd electron-app && npm start
# Requiere el backend corriendo (start_all.sh primero)
```

### 3. Apagar todo

```bash
bash scripts/stop_all.sh
```

### 3. Generar cueva fake en la DB (sin Minecraft)

```bash
cd backend
python gen_fake_cave.py
# Crea ~5000 voxeles en la mision existente
```

### 4. Ver el mapa 3D

Abrir `http://127.0.0.1:8000/static/viewer.html` en el navegador.

### 5. Probar la API

```bash
TOKEN=$(curl -s -X POST http://127.0.0.1:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"identifier":"max@test.com","password":"clave12345"}' \
  | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

# Ver mapa 3D de la mision 3
curl -s http://127.0.0.1:8000/missions/3/map -H "Authorization: Bearer $TOKEN" | python3 -m json.tool

# Estado del simulador
curl -s http://127.0.0.1:8000/mc/status -H "Authorization: Bearer $TOKEN"

# Mover el dron
curl -s -X POST http://127.0.0.1:8000/mc/move \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"target":{"x":10,"y":50,"z":10},"speed":2}'
```

## Sprint Actual

**Sprint 2** (11-26 Agosto): Generacion de galerias y recoleccion de datos volumetricos.

Estado: Backend + plugin compilan. Puente WebSocket funciona (move, raycast DDA, ground truth). Chunk tickets activos para evitar UNLOAD. Cuevas generadas deterministicamente. Falta: pipeline de exploracion autonomo (Sprint 5) y 3D-CNN (Sprint 3-4).
