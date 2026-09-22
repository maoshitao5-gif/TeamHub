const { app, BrowserWindow, ipcMain, Menu, shell, dialog, safeStorage } = require('electron');
const path = require('path');
const fs = require('fs');
const https = require('https');
const http = require('http');
const BackendManager = require('./backend-manager');
const PathHelper = require('./utils/path-helper');
const PortFinder = require('./utils/port-finder');

let mainWindow = null;
let floatingWindow = null;
let backendManager = null;
let pathHelper = null;
let backendPort = null;
const tokenStore = {};
let encryptionAvailable = false;

async function createWindow() {
  try {
    console.log('[Main] Creating application window...');

    pathHelper = new PathHelper();
    const userDataPath = pathHelper.getUserDataPath();
    console.log(`[Main] User data path: ${userDataPath}`);

    pathHelper.ensureDirectories();

    backendPort = await PortFinder.findAvailablePort(8001, 8010);
    if (!backendPort) {
      throw new Error('No available port found for backend server');
    }
    console.log(`[Main] Using port: ${backendPort}`);

    backendManager = new BackendManager(backendPort, userDataPath, pathHelper);
    await backendManager.start();

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
      show: false
    });

    const isDev = pathHelper.isDevelopment();

    if (isDev) {
      const frontendPorts = [5173, 5174, 5175, 5176, 5177];
      let frontendURL = null;

      for (const port of frontendPorts) {
        const url = `http://localhost:${port}`;
        try {
          console.log(`[Main] Checking frontend at: ${url}`);
          const available = await waitForFrontend(url, 2000);
          if (available) {
            frontendURL = url;
            break;
          }
        } catch (error) {
          continue;
        }
      }

      if (!frontendURL) {
        console.error('[Main] Frontend server not found on any port. Trying default 5173...');
        frontendURL = 'http://localhost:5173';
        await waitForFrontend(frontendURL, 30000);
      }

      console.log(`[Main] Loading frontend from: ${frontendURL}`);
      mainWindow.loadURL(frontendURL);
      mainWindow.webContents.openDevTools();
    } else {
      const indexPath = path.join(__dirname, '../frontend/dist/index.html');
      console.log(`[Main] Loading frontend from: ${indexPath}`);
      mainWindow.loadFile(indexPath);
    }

    mainWindow.once('ready-to-show', () => {
      mainWindow.show();
      mainWindow.focus();
      console.log('[Main] Application window ready');
    });

    mainWindow.on('closed', () => {
      mainWindow = null;
    });

    if (!isDev) {
      Menu.setApplicationMenu(null);
    }

    console.log('[Main] Window created successfully');
  } catch (error) {
    console.error('[Main] Error creating window:', error);
    app.quit();
  }
}

function createFloatingWindow() {
  if (floatingWindow) {
    console.log('[Main] Floating window already exists');
    return;
  }

  if (!pathHelper) {
    pathHelper = new PathHelper();
  }

  const isDev = pathHelper.isDevelopment();
  const preloadScriptPath = path.join(__dirname, 'preload.js');
  const floatingPath = isDev
    ? path.join(__dirname, '../frontend/floating-window.html')
    : path.join(__dirname, '../frontend/dist/floating-window.html');

  console.log(`[Main] Creating floating window...`);
  console.log(`[Main]   HTML path: ${floatingPath}`);
  console.log(`[Main]   Preload path: ${preloadScriptPath}`);

  if (!fs.existsSync(floatingPath)) {
    console.error(`[Main] Floating window HTML not found: ${floatingPath}`);
    return;
  }

  floatingWindow = new BrowserWindow({
    width: 80,
    height: 80,
    x: 0,
    y: 0,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    skipTaskbar: true,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    closable: true,
    focusable: true,
    hasShadow: false,
    fullscreenable: false,
    roundedCorners: false,
    thickFrame: false,
    backgroundColor: '#00000000',
    show: false,
    webPreferences: {
      preload: preloadScriptPath,
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: true,
      backgroundThrottling: false,
      offscreen: false
    }
  });

  // 加载页面
  floatingWindow.loadFile(floatingPath).then(() => {
    console.log('[Main] Floating window HTML loaded');
  }).catch(err => {
    console.error('[Main] Failed to load floating window:', err);
  });

  // 页面加载完成后显示并定位
  floatingWindow.once('ready-to-show', () => {
    const { screen } = require('electron');
    const primaryDisplay = screen.getPrimaryDisplay();
    const { width, height } = primaryDisplay.workAreaSize;
    const x = width - 100;
    const y = height - 100;
    
    floatingWindow.setPosition(x, y);
    floatingWindow.show();
    floatingWindow.setAlwaysOnTop(true, 'normal');
    
    // Windows 透明窗口拖放修复：确保窗口可以接收鼠标/拖放事件
    if (process.platform === 'win32') {
      // 使用 setIgnoreMouseEvents(false, {forward: true}) 允许拖放穿透
      floatingWindow.setIgnoreMouseEvents(false);
    }
    
    console.log(`[Main] Floating window shown at (${x}, ${y})`);
  });

  floatingWindow.on('closed', () => {
    console.log('[Main] Floating window closed');
    floatingWindow = null;
  });

  floatingWindow.webContents.on('will-navigate', (event) => {
    event.preventDefault();
  });

  // 启用拖放文件
  floatingWindow.webContents.on('dom-ready', () => {
    console.log('[Main] Floating window DOM ready');
    
    // Windows: 注入脚本启用拖放
    if (process.platform === 'win32') {
      floatingWindow.webContents.executeJavaScript(`
        // 确保整个文档都是可拖放目标
        document.body.style.position = 'fixed';
        document.body.style.top = '0';
        document.body.style.left = '0';
        document.body.style.width = '100%';
        document.body.style.height = '100%';
        console.log('[Floating] Drag-drop fix applied');
      `).catch(err => {
        console.error('[Main] Failed to inject drag-drop fix:', err);
      });
    }
  });
}

function closeFloatingWindow() {
  if (floatingWindow) {
    floatingWindow.close();
    floatingWindow = null;
  }
}

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

ipcMain.on('get-app-config', (event) => {
  event.reply('app-config', {
    apiBaseURL: backendManager ? backendManager.getBackendURL() : 'http://127.0.0.1:8001'
  });
});

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

ipcMain.handle('check-is-directory', async (event, filePath) => {
  try {
    const stats = fs.statSync(filePath);
    return stats.isDirectory();
  } catch (error) {
    console.error('[Main] Error checking if path is directory:', error);
    return false;
  }
});

ipcMain.handle('list-directory-contents', async (event, dirPath) => {
  try {
    const entries = fs.readdirSync(dirPath, { withFileTypes: true });
    const files = [];
    const folders = [];
    
    for (const entry of entries) {
      const fullPath = path.join(dirPath, entry.name);
      if (entry.isDirectory()) {
        folders.push({ name: entry.name, path: fullPath });
      } else if (entry.isFile()) {
        files.push({ name: entry.name, path: fullPath });
      }
    }
    
    return { files, folders };
  } catch (error) {
    console.error('[Main] Error listing directory contents:', error);
    return { files: [], folders: [] };
  }
});

app.whenReady().then(async () => {
  const { session } = require('electron');
  await session.defaultSession.setProxy({
    proxyBypassRules: '127.0.0.1,localhost'
  });
  console.log('[Main] Proxy bypass configured for localhost');

  // 设置 CSP
  const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
  const csp = isDev
    ? "default-src 'self' http://localhost:* http://127.0.0.1:*; script-src 'self' 'unsafe-eval' http://localhost:* http://127.0.0.1:*; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://localhost:* http://127.0.0.1:*; connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:*; font-src 'self' data:"
    : "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://127.0.0.1:*; connect-src 'self' http://127.0.0.1:*; font-src 'self' data:";

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [csp]
      }
    });
  });
  console.log(`[Main] CSP configured (${isDev ? 'development' : 'production'} mode)`);

  encryptionAvailable = safeStorage.isEncryptionAvailable();

  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

ipcMain.handle('toggle-floating-window', async (event, enabled) => {
  console.log(`[Main] toggle-floating-window: enabled=${enabled}`);
  if (enabled) {
    createFloatingWindow();
  } else {
    closeFloatingWindow();
  }
});

ipcMain.handle('floating-window-drop-files', async (event, filePaths) => {
  console.log(`[Main] floating-window-drop-files: ${filePaths.length} files`);
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    mainWindow.webContents.send('floating-window-files-dropped', filePaths);
  }
  return { success: true };
});

ipcMain.on('floating-window-move', (event, { deltaX, deltaY }) => {
  if (floatingWindow && !floatingWindow.isDestroyed()) {
    const [x, y] = floatingWindow.getPosition();
    floatingWindow.setPosition(x + deltaX, y + deltaY);
  }
});

ipcMain.handle('safe-storage-set', (event, key, value) => {
  try {
    if (encryptionAvailable) {
      tokenStore[key] = safeStorage.encryptString(value).toString('base64');
    } else {
      tokenStore[key] = value;
    }
    return true;
  } catch (e) {
    console.error('[SafeStorage] Storage failed:', e);
    return false;
  }
});

ipcMain.handle('safe-storage-get', (event, key) => {
  try {
    const stored = tokenStore[key];
    if (!stored) return null;
    if (encryptionAvailable) {
      return safeStorage.decryptString(Buffer.from(stored, 'base64'));
    }
    return stored;
  } catch (e) {
    console.error('[SafeStorage] Read failed:', e);
    return null;
  }
});

ipcMain.handle('safe-storage-delete', (event, key) => {
  delete tokenStore[key];
  return true;
});

ipcMain.handle('download-url', async (event, { url, localPath }) => {
  return new Promise((resolve) => {
    try {
      fs.mkdirSync(path.dirname(localPath), { recursive: true });
      const fileStream = fs.createWriteStream(localPath);

      const protocol = url.startsWith('https') ? https : http;
      const req = protocol.get(url, (res) => {
        if (res.statusCode !== 200) {
          fileStream.close();
          fs.unlink(localPath, () => {});
          resolve({ ok: false, error: `HTTP ${res.statusCode}` });
          return;
        }
        res.pipe(fileStream);
        fileStream.on('finish', () => {
          fileStream.close();
          resolve({ ok: true, localPath });
        });
        fileStream.on('error', (err) => {
          fileStream.close();
          fs.unlink(localPath, () => {});
          resolve({ ok: false, error: err.message });
        });
      });
      req.on('error', (err) => {
        fileStream.close();
        fs.unlink(localPath, () => {});
        resolve({ ok: false, error: err.message });
      });
    } catch (err) {
      resolve({ ok: false, error: err.message });
    }
  });
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});

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
