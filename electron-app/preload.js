const { contextBridge, ipcRenderer, webUtils } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  checkBackend: () => ipcRenderer.invoke('check-backend'),
  fetchBackend: (endpoint, options) => ipcRenderer.invoke('backend-fetch', endpoint, options),
  exportMission: (payload) => ipcRenderer.invoke('export-mission', payload),
  listDir: (dir) => ipcRenderer.invoke('list-dir', dir),
  readFile: (filePath) => ipcRenderer.invoke('read-file', filePath),
  homeDirs: () => ipcRenderer.invoke('home-dirs'),
  getPathForFile: (file) => webUtils.getPathForFile(file),
  openExternal: (url) => ipcRenderer.invoke('open-external', url),
  launchPrism: () => ipcRenderer.invoke('launch-prism'),
});
