const { contextBridge, ipcRenderer, webUtils } = require('electron');

/**
 * 预加载脚本
 *
 * 通过 contextBridge 安全地将 API 暴露给渲染进程
 * 这样前端就可以通过 window.electron 访问后端 URL 等信息
 */

// 渲染入口执行前同步取得端口，避免首批请求误发到固定的 8001。
const { apiBaseURL, aiCapability } = ipcRenderer.sendSync('get-app-config-sync');

// 暴露安全的 API 给渲染进程
contextBridge.exposeInMainWorld('electron', {
  // 获取后端 API 地址
  get apiBaseURL() {
    return apiBaseURL;
  },
  // 仅供本机个人服务认证，不写入 renderer 持久化状态。
  get aiCapability() {
    return aiCapability;
  },

  // 检查是否在 Electron 环境中
  isElectron: true,

  // 平台信息
  platform: process.platform,

  // 文件操作 API
  getPathForFile: (file) => webUtils.getPathForFile(file),
  openPath: (fullPath) => ipcRenderer.invoke('open-path', fullPath),
  openExternalLink: (url) => ipcRenderer.invoke('open-external-link', url),
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

});

console.log('[Preload] Preload script loaded');
