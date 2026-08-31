const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  checkBackend: () => ipcRenderer.invoke('check-backend'),
  fetchBackend: (endpoint, options) => ipcRenderer.invoke('backend-fetch', endpoint, options),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
});
