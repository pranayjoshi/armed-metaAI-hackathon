// The only bridge between the card page and the shell: four calls, no data back.
const { contextBridge, ipcRenderer } = require('electron');

const send = (channel) => (...args) => ipcRenderer.send(channel, ...args);

contextBridge.exposeInMainWorld('armedHost', {
  setVisible: (visible) => ipcRenderer.send('host:visible', visible === true),
  setInteractive: (interactive) => ipcRenderer.send('host:interactive', interactive === true),
  stop: send('host:stop'),
  openConsole: send('host:console'),
});
