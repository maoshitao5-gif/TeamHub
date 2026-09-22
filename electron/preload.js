const { contextBridge, ipcRenderer } = require('electron');

/**
 * 预加载脚本
 *
 * 通过 contextBridge 安全地将 API 暴露给渲染进程
 * 这样前端就可以通过 window.electron 访问后端 URL 等信息
 */

// 从主进程获取配置
let apiBaseURL = 'http://127.0.0.1:8001'; // 默认值

ipcRenderer.on('app-config', (event, config) => {
  apiBaseURL = config.apiBaseURL;
  console.log('[Preload] Received API base URL:', apiBaseURL);
});

// 向主进程请求配置
ipcRenderer.send('get-app-config');

// 白名单：允许 invoke 的通道
const validInvokeChannels = ['open-path', 'show-item-in-folder', 'select-directory', 'select-file', 'check-is-directory', 'list-directory-contents', 'floating-window-drop-files', 'toggle-floating-window', 'download-url'];

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electron', {
  // 获取后端 API 地址
  get apiBaseURL() {
    return apiBaseURL;
  },

  // 检查是否在 Electron 环境中
  isElectron: true,

  // 平台信息
  platform: process.platform,

  // 文件操作 API
  openPath: (fullPath) => ipcRenderer.invoke('open-path', fullPath),
  showItemInFolder: (fullPath) => ipcRenderer.invoke('show-item-in-folder', fullPath),
  selectDirectory: () => ipcRenderer.invoke('select-directory'),
  selectFile: () => ipcRenderer.invoke('select-file'),
  checkIsDirectory: (filePath) => ipcRenderer.invoke('check-is-directory', filePath),
  listDirectoryContents: (dirPath) => ipcRenderer.invoke('list-directory-contents', dirPath),

  // 发送消息到主进程
  send: (channel, data) => {
    // 白名单允许的通道
    const validChannels = ['get-app-config', 'floating-window-move'];
    if (validChannels.includes(channel)) {
      ipcRenderer.send(channel, data);
    }
  },

  // 接收来自主进程的消息
  on: (channel, func) => {
    const validChannels = ['app-config', 'floating-window-files-dropped'];
    if (validChannels.includes(channel)) {
      ipcRenderer.on(channel, (event, ...args) => func(...args));
    }
  },

  // 悬浮窗相关
  toggleFloatingWindow: (enabled) => ipcRenderer.invoke('toggle-floating-window', enabled),

  // 悬浮窗拖拽文件
  dropFilesToFloatingWindow: (filePaths) => ipcRenderer.invoke('floating-window-drop-files', filePaths),

  // 安全存储（用于保存 refresh token，使用 Electron safeStorage 加密）
  storeToken: (key, value) => ipcRenderer.invoke('safe-storage-set', key, value),
  loadToken: (key) => ipcRenderer.invoke('safe-storage-get', key),
  clearToken: (key) => ipcRenderer.invoke('safe-storage-delete', key),

  // 通过预签名 URL 下载文件到本机指定路径（云仓库导入流程用）
  downloadUrl: (url, localPath) => ipcRenderer.invoke('download-url', { url, localPath }),
});

console.log('[Preload] Preload script loaded');
