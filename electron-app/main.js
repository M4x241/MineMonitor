const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const http = require('http');
const fs = require('fs');

const BACKEND_URL = 'http://127.0.0.1:8000';

let mainWindow;

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 800,
    minWidth: 800,
    minHeight: 600,
    title: 'Mine Explorer 3D',
    backgroundColor: '#0a0a0f',
    autoHideMenuBar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  mainWindow.loadFile(path.join(__dirname, 'renderer', 'index.html'));

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  app.quit();
});

// IPC: verificar si el backend esta corriendo
ipcMain.handle('check-backend', async () => {
  return new Promise((resolve) => {
    http.get(BACKEND_URL + '/health', (res) => {
      let data = '';
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => {
        try {
          resolve({ ok: true, data: JSON.parse(data) });
        } catch {
          resolve({ ok: false });
        }
      });
    }).on('error', () => {
      resolve({ ok: false });
    });
  });
});

// IPC: fetch generico al backend
ipcMain.handle('backend-fetch', async (event, endpoint, options) => {
  return new Promise((resolve, reject) => {
    const url = BACKEND_URL + endpoint;
    const reqOptions = {
      method: options?.method || 'GET',
      headers: {
        'Content-Type': 'application/json',
        ...(options?.headers || {}),
      },
    };

    const req = http.request(url, reqOptions, (res) => {
      let data = '';
      res.on('data', (chunk) => data += chunk);
      res.on('end', () => {
        try {
          resolve({ status: res.statusCode, data: JSON.parse(data) });
        } catch {
          resolve({ status: res.statusCode, data: data });
        }
      });
    });

    req.on('error', reject);

    if (options?.body) {
      req.write(typeof options.body === 'string' ? options.body : JSON.stringify(options.body));
    }
    req.end();
  });
});

// IPC: abrir URL en navegador externo
ipcMain.handle('open-external', async (event, url) => {
  await shell.openExternal(url);
});

// IPC: lanzar Prism Launcher
ipcMain.handle('launch-prism', async () => {
  const { spawn } = require('child_process');
  // Intentar usar el lanzador del sistema (flatpak/instalado) primero
  const cmds = [
    ['flatpak', ['run', 'org.prismlauncher.PrismLauncher']],
    ['prismlauncher', []],
    ['PrismLauncher', []],
  ];
  for (const [cmd, args] of cmds) {
    try {
      const proc = spawn(cmd, args, { detached: true, stdio: 'ignore' });
      proc.on('error', () => {});
      proc.unref();
      return { ok: true };
    } catch (e) {
      // continue
    }
  }
  const paths = [
    '/home/ghost/Downloads/PrismLauncher-Linux-x86_64.AppImage',
    '/home/ghost/Downloads/ENTREGAR/Instaladores/PrismLauncher-Linux-x86_64.AppImage',
  ];
  for (const p of paths) {
    try {
      if (fs.existsSync(p)) {
        try { fs.accessSync(p, fs.constants.X_OK); } catch { try { require('child_process').execSync('chmod +x "' + p + '"'); } catch {} }
        const proc = spawn(p, [], { detached: true, stdio: 'ignore', env: { ...process.env, APPIMAGE_EXTRACT_AND_RUN: '1' } });
        proc.on('error', (err) => {
          console.error('launch-prism error', err);
        });
        proc.unref();
        return { ok: true };
      }
    } catch (e) {
      // ignore
    }
  }
  try {
    await shell.openExternal('prismlauncher://');
  } catch (e) {}
  return { ok: true };
});

// IPC: exportar resultados de una misión a disco (descarga binaria en streaming)
const FORMAT_LABELS = {
  csv: 'CSV',
  ply: 'PLY',
  obj: 'OBJ',
  png: 'PNG',
  pdf: 'PDF',
};

// Carpeta destino: ~/Descargas/MineExplorer/mission_<id>/
function exportDirFor(missionId) {
  return path.join(app.getPath('downloads'), 'MineExplorer', `mission_${missionId}`);
}

function downloadToFile(endpoint, token, destPath) {
  return new Promise((resolve) => {
    const req = http.get(BACKEND_URL + endpoint, {
      headers: { Authorization: 'Bearer ' + (token || '') },
    }, (res) => {
      if (res.statusCode !== 200) {
        let body = '';
        res.on('data', (c) => { body += c; });
        res.on('end', () => resolve({ error: `HTTP ${res.statusCode}: ${body.slice(0, 300)}` }));
        return;
      }
      fs.mkdirSync(path.dirname(destPath), { recursive: true });
      const file = fs.createWriteStream(destPath);
      res.pipe(file);
      file.on('finish', () => file.close(() => resolve({ ok: true, path: destPath })));
      file.on('error', (err) => resolve({ error: err.message }));
    });
    req.on('error', (err) => resolve({ error: err.message }));
  });
}

ipcMain.handle('export-mission', async (event, payload) => {
  const { missionId, format, token, query } = payload || {};
  if (!missionId || !format) return { error: 'Petición de exportación inválida' };

  const dir = exportDirFor(missionId);
  const base = format === 'png'
    ? `mission_${missionId}_heatmap_entropy.png`
    : `mission_${missionId}.${format}`;
  const filePath = path.join(dir, base);

  const main = await downloadToFile(
    `/missions/${missionId}/export/${format}${query || ''}`, token, filePath,
  );
  if (main.error) return main;

  // El OBJ referencia un .mtl hermano: guárdalo también para que tenga colores.
  if (format === 'obj') {
    const mtlPath = path.join(dir, `mission_${missionId}.mtl`);
    await downloadToFile(`/missions/${missionId}/export/mtl`, token, mtlPath);
  }

  return { saved: true, path: filePath, dir };
});

// IPC: navegar el sistema de archivos desde la interfaz (sin diálogos nativos)
ipcMain.handle('list-dir', async (event, dir) => {
  const target = dir || app.getPath('downloads');
  try {
    const entries = fs.readdirSync(target, { withFileTypes: true })
      .filter((e) => !e.name.startsWith('.'))
      .map((e) => {
        const full = path.join(target, e.name);
        let size = 0;
        if (e.isFile()) { try { size = fs.statSync(full).size; } catch { size = 0; } }
        return {
          name: e.name,
          path: full,
          dir: e.isDirectory(),
          ext: e.isFile() ? path.extname(e.name).slice(1).toLowerCase() : '',
          size,
        };
      })
      .sort((a, b) => (a.dir === b.dir ? a.name.localeCompare(b.name) : (a.dir ? -1 : 1)));
    const parent = path.dirname(target);
    return { dir: target, parent: parent === target ? null : parent, entries };
  } catch (err) {
    return { error: 'No se pudo abrir la carpeta: ' + err.message };
  }
});

// IPC: leer un archivo del disco para importarlo
ipcMain.handle('read-file', async (event, filePath) => {
  try {
    const st = fs.statSync(filePath);
    if (st.size > 200 * 1024 * 1024) return { error: 'El archivo es demasiado grande (>200 MB)' };
    return {
      name: path.basename(filePath),
      ext: path.extname(filePath).slice(1).toLowerCase(),
      data: fs.readFileSync(filePath), // Buffer -> Uint8Array
    };
  } catch (err) {
    return { error: 'No se pudo leer el archivo: ' + err.message };
  }
});

// IPC: rutas de inicio para el explorador de importación
ipcMain.handle('home-dirs', async () => {
  const downloads = app.getPath('downloads');
  return { home: app.getPath('home'), downloads, start: downloads };
});
