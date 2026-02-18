const { app, BrowserWindow, ipcMain, Menu, shell, dialog } = require('electron');
const path = require('path');
const BackendManager = require('./backend-manager');
const PathHelper = require('./utils/path-helper');
const PortFinder = require('./utils/port-finder');

let mainWindow = null;
let backendManager = null;
let pathHelper = null;
let backendPort = null;

/**
 * 创建应用主窗口
 */
async function createWindow() {
  try {
    console.log('[Main] Creating application window...');

    // 1. 初始化路径管理器
    pathHelper = new PathHelper();
    const userDataPath = pathHelper.getUserDataPath();
    console.log(`[Main] User data path: ${userDataPath}`);

    // 2. 创建必要的目录
    pathHelper.ensureDirectories();

    // 3. 查找可用端口
    backendPort = await PortFinder.findAvailablePort(8001, 8010);
    if (!backendPort) {
      throw new Error('No available port found for backend server');
    }
    console.log(`[Main] Using port: ${backendPort}`);

    // 4. 启动后端服务
    backendManager = new BackendManager(backendPort, userDataPath, pathHelper);
    await backendManager.start();

    // 5. 创建浏览器窗口
    mainWindow = new BrowserWindow({
      width: 1280,
      height: 800,
      minWidth: 1024,
      minHeight: 768,
      title: 'TeamHub',
      icon: path.join(__dirname, '../build/icon.png'),
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
        enableRemoteModule: false,
        webSecurity: true
      },
      backgroundColor: '#ffffff',
      show: false // 先不显示，等加载完成后再显示
    });

    // 6. 加载前端页面
    const isDev = pathHelper.isDevelopment();

    if (isDev) {
      // 开发模式：加载 Vite 开发服务器
      // 尝试多个端口，因为 Vite 会在端口被占用时自动切换
      const frontendPorts = [5173, 5174, 5175, 5176, 5177];
      let frontendURL = null;

      for (const port of frontendPorts) {
        const url = `http://localhost:${port}`;
        try {
          console.log(`[Main] Checking frontend at: ${url}`);
          const available = await waitForFrontend(url, 2000); // 2秒超时
          if (available) {
            frontendURL = url;
            break;
          }
        } catch (error) {
          // 继续尝试下一个端口
          continue;
        }
      }

      if (!frontendURL) {
        console.error('[Main] Frontend server not found on any port. Trying default 5173...');
        frontendURL = 'http://localhost:5173';
        await waitForFrontend(frontendURL, 30000); // 30秒超时
      }

      console.log(`[Main] Loading frontend from: ${frontendURL}`);
      mainWindow.loadURL(frontendURL);

      // 打开开发者工具
      mainWindow.webContents.openDevTools();
    } else {
      // 生产模式：加载打包的前端文件
      const indexPath = path.join(__dirname, '../frontend/dist/index.html');
      console.log(`[Main] Loading frontend from: ${indexPath}`);
      mainWindow.loadFile(indexPath);
    }

    // 7. 窗口加载完成后显示
    mainWindow.once('ready-to-show', () => {
      mainWindow.show();
      mainWindow.focus();
      console.log('[Main] Application window ready');
    });

    // 8. 处理窗口关闭事件
    mainWindow.on('closed', () => {
      mainWindow = null;
    });

    // 9. 设置应用菜单（可选：移除默认菜单或自定义）
    if (!isDev) {
      Menu.setApplicationMenu(null); // 生产模式移除默认菜单
    }

    console.log('[Main] Window created successfully');
  } catch (error) {
    console.error('[Main] Error creating window:', error);
    app.quit();
  }
}

/**
 * 等待前端服务启动
 */
async function waitForFrontend(url, timeout = 30000) {
  const axios = require('axios');
  const localAxios = axios.create({ proxy: false });
  const startTime = Date.now();
  const checkInterval = 500;

  console.log('[Main] Waiting for frontend server to start...');

  while (Date.now() - startTime < timeout) {
    try {
      await localAxios.get(url, { timeout: 1000 });
      console.log('[Main] Frontend server is ready');
      return true;
    } catch (error) {
      // 继续等待
    }
    await new Promise(resolve => setTimeout(resolve, checkInterval));
  }

  throw new Error('Frontend server failed to start within timeout period');
}

/**
 * 处理来自渲染进程的配置请求
 */
ipcMain.on('get-app-config', (event) => {
  event.reply('app-config', {
    apiBaseURL: backendManager ? backendManager.getBackendURL() : 'http://127.0.0.1:8001'
  });
});

/**
 * 文件操作 IPC handlers
 */
ipcMain.handle('open-path', async (event, fullPath) => {
  return await shell.openPath(fullPath);
});

ipcMain.handle('show-item-in-folder', (event, fullPath) => {
  shell.showItemInFolder(fullPath);
});

ipcMain.handle('select-directory', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });
  if (result.canceled) return null;
  return result.filePaths[0] || null;
});

ipcMain.handle('select-file', async () => {
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile']
  });
  if (result.canceled) return null;
  return result.filePaths[0] || null;
});

/**
 * 应用就绪时创建窗口
 */
app.whenReady().then(async () => {
  // 绕过本地代理，避免 127.0.0.1/localhost 请求走系统代理
  const { session } = require('electron');
  await session.defaultSession.setProxy({
    proxyBypassRules: '127.0.0.1,localhost'
  });
  console.log('[Main] Proxy bypass configured for localhost');
  createWindow();
});

/**
 * 所有窗口关闭时的处理（macOS 除外）
 */
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

/**
 * macOS 下重新激活应用时创建窗口
 */
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

/**
 * 应用退出前清理资源
 */
app.on('before-quit', async (event) => {
  if (backendManager) {
    console.log('[Main] Stopping backend before quit...');
    event.preventDefault();

    try {
      await backendManager.stop();
    } catch (error) {
      console.error('[Main] Error stopping backend:', error);
    }

    backendManager = null;
    app.quit();
  }
});

/**
 * 处理未捕获的异常
 */
process.on('uncaughtException', (error) => {
  console.error('[Main] Uncaught exception:', error);
});

process.on('unhandledRejection', (error) => {
  console.error('[Main] Unhandled rejection:', error);
});

console.log('[Main] Electron main process started');
console.log(`[Main] Platform: ${process.platform}`);
console.log(`[Main] Electron version: ${process.versions.electron}`);
console.log(`[Main] Node version: ${process.versions.node}`);
console.log(`[Main] App packaged: ${app.isPackaged}`);
