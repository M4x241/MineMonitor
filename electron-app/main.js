const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const http = require('http');

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
