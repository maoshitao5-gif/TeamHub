const { app, BrowserWindow, ipcMain, Menu, shell, dialog } = require('electron');
const path = require('path');
const fs = require('fs');
const { pathToFileURL } = require('url');
const { randomBytes, timingSafeEqual } = require('crypto');
const { CHALLENGE_PATH, validateFrontendURL, challengeResponse } = require('./utils/dev-frontend');
const BackendManager = require('./backend-manager');
const PathHelper = require('./utils/path-helper');
const PortFinder = require('./utils/port-finder');
const { isDirectory, listDirectoryContents } = require('./utils/file-access');

// 独立数据目录用于并行个人库与隔离验收；必须在 app ready 前设置。
if (process.env.TEAMHUB_USER_DATA_DIR) {
  app.setPath('userData', path.resolve(process.env.TEAMHUB_USER_DATA_DIR));
}
let creatingWindow = false;
let quitting = false;
let mainWindow = null;
let floatingWindow = null;
let backendManager = null;
let pathHelper = null;
let backendPort = null;
let desktopMounted = false;
let verifiedDevFrontendURL = null;

// 仅当前受管窗口的顶层入口可访问桌面能力；hash 路由不改变入口身份。
function isTrustedRenderer(event, windows = [mainWindow, floatingWindow]) {
  try {
    const window = windows.find(candidate => candidate && !candidate.isDestroyed() &&
      candidate.webContents === event?.sender);
    if (!window || !event.senderFrame || event.senderFrame !== event.sender.mainFrame) return false;
    const expected = window === mainWindow
      ? (app.isPackaged ? pathToFileURL(path.join(__dirname, '../frontend/dist/index.html')).href : verifiedDevFrontendURL)
      : pathToFileURL(path.join(__dirname, app.isPackaged
        ? '../frontend/dist/floating-window.html' : '../frontend/floating-window.html')).href;
    if (!expected) return false;
    const actual = new URL(event.senderFrame.url);
    actual.hash = '';
    return actual.href === expected;
  } catch { return false; }
}

function requireTrustedRenderer(event, windows) {
  if (!isTrustedRenderer(event, windows)) throw new Error('不可信的桌面调用来源');
}

function writeDesktopState(status, error) {
  const userData = app.getPath('userData');
  const target = path.join(userData, 'desktop-state.json');
  try {
    fs.mkdirSync(userData, { recursive: true });
    // 旧实例退出时不能覆盖同一目录中新实例的就绪记录。
    if (status === 'stopped' && fs.existsSync(target)) {
      const previous = JSON.parse(fs.readFileSync(target, 'utf8'));
      if (previous.pid !== process.pid) return;
    }
    const temporary = target + '.' + process.pid + '.tmp';
    fs.writeFileSync(temporary, JSON.stringify({ status, error, pid: process.pid,
      executable: process.execPath, userData, port: backendPort,
      backendPid: backendManager?.backendProcess?.pid,
      instanceId: backendManager?.instance, updatedAt: new Date().toISOString() }));
    fs.renameSync(temporary, target);
  } catch (failure) { console.error('[Main] Cannot write desktop startup state:', failure.message); }
}

async function createWindow() {
  if (creatingWindow || mainWindow || quitting) return;
  creatingWindow = true;
  desktopMounted = false;
  try {
    writeDesktopState('starting');
    console.log('[Main] Creating application window...');

    pathHelper = new PathHelper();
    if (pathHelper.isDevelopment()) {
      verifiedDevFrontendURL = null;
      const frontendURL = validateFrontendURL(process.env.TEAMHUB_DEV_FRONTEND_URL);
      await waitForFrontend(frontendURL, 10000, process.env.TEAMHUB_DEV_FRONTEND_SECRET);
      verifiedDevFrontendURL = frontendURL;
    }
    const userDataPath = pathHelper.getUserDataPath();
    console.log(`[Main] User data path: ${userDataPath}`);

    pathHelper.ensureDirectories();

    if (!backendManager) {
    backendPort = await PortFinder.findAvailablePort(8001, 8010);
    if (!backendPort) {
      throw new Error('No available port found for backend server');
    }
    console.log(`[Main] Using port: ${backendPort}`);

    backendManager = new BackendManager(backendPort, userDataPath, pathHelper);
    backendManager.onReady = () => {
      if (mainWindow && desktopMounted && !creatingWindow) writeDesktopState('ready');
    };
    await backendManager.start();
    }

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

    mainWindow.once('ready-to-show', () => {
      mainWindow.show();
      mainWindow.focus();
      console.log('[Main] Application window ready');
    });

    mainWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
    mainWindow.webContents.on('will-navigate', event => event.preventDefault());
    mainWindow.webContents.on('will-redirect', event => event.preventDefault());
    const isDev = pathHelper.isDevelopment();

    if (isDev) {
      console.log(`[Main] Loading verified frontend from: ${verifiedDevFrontendURL}`);
      await mainWindow.loadURL(verifiedDevFrontendURL);
      mainWindow.webContents.openDevTools();
    } else {
      const indexPath = path.join(__dirname, '../frontend/dist/index.html');
      console.log(`[Main] Loading frontend from: ${indexPath}`);
      await mainWindow.loadFile(indexPath);
    }

    mainWindow.on('closed', () => {
      mainWindow = null;
      desktopMounted = false;
      writeDesktopState('window-closed');
    });

    if (!isDev) {
      Menu.setApplicationMenu(null);
    }

    // 文档加载完成不等于 Vue 已挂载；启动器还需要真实界面就绪信号。
    const mountedDeadline = Date.now() + 5000;
    let mounted = false;
    while (Date.now() < mountedDeadline) {
      mounted = await mainWindow.webContents.executeJavaScript("Boolean(document.querySelector('#app')?.childElementCount)");
      if (mounted) break;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    if (!mounted) throw new Error('TeamHub 界面未能完成加载');
    desktopMounted = true;
    mainWindow.show();
    writeDesktopState('ready');
    console.log('[Main] Window created successfully');
  } catch (error) {
    writeDesktopState('error', error.message);
    console.error('[Main] Error creating window:', error);
    dialog.showErrorBox('TeamHub 启动失败', `${error.message}\n\n后端日志：${path.join(app.getPath('userData'), 'logs', 'backend.log')}`);
    app.quit();
  } finally { creatingWindow = false; }
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

  floatingWindow.webContents.setWindowOpenHandler(() => ({ action: 'deny' }));
  floatingWindow.webContents.on('will-redirect', event => event.preventDefault());

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

async function waitForFrontend(url, timeout = 30000, secret) {
  const frontendURL = validateFrontendURL(url);
  if (!/^[a-f0-9]{64}$/.test(secret || '')) {
    throw new Error('缺少桌面开发服务凭据，请使用 npm run electron:dev');
  }
  const axios = require('axios');
  const localAxios = axios.create({ proxy: false });
  const startTime = Date.now();
  const checkInterval = 500;

  console.log('[Main] Waiting for frontend server to start...');

  while (Date.now() - startTime < timeout) {
    try {
      const challenge = randomBytes(32).toString('hex');
      const target = new URL(CHALLENGE_PATH, frontendURL);
      target.searchParams.set('challenge', challenge);
      const response = await localAxios.get(target.href, {
        timeout: 1000, maxRedirects: 0, responseType: 'text',
        maxContentLength: 128, validateStatus: status => status === 200,
      });
      const expected = challengeResponse(secret, challenge);
      if (response.status !== 200 || typeof response.data !== 'string' ||
          !/^[a-f0-9]{64}$/.test(response.data) ||
          !timingSafeEqual(Buffer.from(response.data), Buffer.from(expected))) {
        throw new Error('Unverified frontend');
      }
      console.log('[Main] Frontend server is ready');
      return true;
    } catch (error) {
      // 继续等待
    }
    await new Promise(resolve => setTimeout(resolve, Math.min(checkInterval, timeout)));
  }

  throw new Error('Frontend server failed to start within timeout period');
}

ipcMain.on('get-app-config-sync', event => {
  event.returnValue = isTrustedRenderer(event) && backendManager
    ? { apiBaseURL: backendManager.getBackendURL(), aiCapability: backendManager.aiCapability }
    : {};
});

ipcMain.on('get-app-config', (event) => {
  if (!isTrustedRenderer(event)) return;
  event.reply('app-config', {
    apiBaseURL: backendManager ? backendManager.getBackendURL() : 'http://127.0.0.1:8001'
  });
});

ipcMain.handle('open-path', async (event, fullPath) => {
  requireTrustedRenderer(event);
  return await shell.openPath(fullPath);
});

// 参考链接仅由主界面的显式点击调用；弹窗和页面导航仍保持默认拒绝。
ipcMain.handle('open-external-link', async (event, address) => {
  try {
    requireTrustedRenderer(event, [mainWindow]);
    if (typeof address !== 'string' || address.length > 8192 ||
        address !== address.trim() || /[\u0000-\u001f\u007f-\u009f]/.test(address) ||
        !/^https?:\/\//i.test(address)) {
      throw new Error('Invalid external-link address');
    }
    const target = new URL(address);
    if (!['https:', 'http:'].includes(target.protocol) || !target.hostname ||
        target.username || target.password || target.href.length > 8192) {
      throw new Error('Unsupported external-link address');
    }
    await shell.openExternal(target.href);
    return { success: true };
  } catch {
    throw new Error('无法打开此链接，请检查网址后重试');
  }
});

ipcMain.handle('show-item-in-folder', (event, fullPath) => {
  requireTrustedRenderer(event);
  shell.showItemInFolder(fullPath);
});

ipcMain.handle('select-directory', async event => {
  requireTrustedRenderer(event);
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openDirectory']
  });
  if (result.canceled) return null;
  return result.filePaths[0] || null;
});

ipcMain.handle('select-file', async event => {
  requireTrustedRenderer(event);
  const result = await dialog.showOpenDialog(mainWindow, {
    properties: ['openFile']
  });
  if (result.canceled) return null;
  return result.filePaths[0] || null;
});

ipcMain.handle('check-is-directory', (event, filePath) => {
  requireTrustedRenderer(event);
  return isDirectory(filePath);
});
ipcMain.handle('list-directory-contents', (event, dirPath) => {
  requireTrustedRenderer(event);
  return listDirectoryContents(dirPath);
});

app.whenReady().then(async () => {
  const { session } = require('electron');
  await session.defaultSession.setProxy({
    proxyBypassRules: '127.0.0.1,localhost'
  });
  console.log('[Main] Proxy bypass configured for localhost');

  // XHR/fetch 重定向后会重新进入此钩子；桌面凭据不能被带到其他端口或外部站点。
  session.defaultSession.webRequest.onBeforeSendHeaders((details, callback) => {
    const requestHeaders = { ...details.requestHeaders };
    let localBackend = false;
    try {
      const target = new URL(details.url);
      const backend = new URL(backendManager?.getBackendURL());
      localBackend = target.origin === backend.origin && !target.username && !target.password;
    } catch { /* 服务尚未就绪时不允许发送桌面凭据。 */ }
    if (!localBackend) {
      for (const key of Object.keys(requestHeaders)) {
        if (key.toLowerCase() === 'x-teamhub-ai-capability') delete requestHeaders[key];
      }
    }
    callback({ requestHeaders });
  });

  // 设置 CSP
  const isDev = process.env.NODE_ENV === 'development' || !app.isPackaged;
  const csp = isDev
    ? "default-src 'self' http://localhost:* http://127.0.0.1:*; script-src 'self' 'unsafe-eval' http://localhost:* http://127.0.0.1:*; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://localhost:* http://127.0.0.1:*; connect-src 'self' http://localhost:* http://127.0.0.1:* ws://localhost:*; font-src 'self' data:"
    : "default-src 'self'; script-src 'self' 'unsafe-inline'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://127.0.0.1:*; connect-src 'self' http://127.0.0.1:*; font-src 'self' data:";

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [csp]
      }
    });
  });
  console.log(`[Main] CSP configured (${isDev ? 'development' : 'production'} mode)`);

  createWindow();
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('will-quit', () => writeDesktopState('stopped'));

ipcMain.handle('toggle-floating-window', async (event, enabled) => {
  requireTrustedRenderer(event, [mainWindow]);
  console.log(`[Main] toggle-floating-window: enabled=${enabled}`);
  if (enabled) {
    createFloatingWindow();
  } else {
    closeFloatingWindow();
  }
});

ipcMain.handle('floating-window-drop-files', async (event, filePaths) => {
  requireTrustedRenderer(event);
  console.log(`[Main] floating-window-drop-files: ${filePaths.length} files`);
  if (mainWindow && !mainWindow.isDestroyed()) {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    mainWindow.webContents.send('floating-window-files-dropped', filePaths);
  }
  return { success: true };
});

ipcMain.on('floating-window-move', (event, movement) => {
  if (!isTrustedRenderer(event, [floatingWindow])) return;
  const { deltaX, deltaY } = movement || {};
  if (!Number.isFinite(deltaX) || !Number.isFinite(deltaY)) return;
  if (floatingWindow && !floatingWindow.isDestroyed()) {
    const [x, y] = floatingWindow.getPosition();
    floatingWindow.setPosition(x + deltaX, y + deltaY);
  }
});

app.on('activate', () => {
  if (!mainWindow) {
    createWindow();
  } else {
    if (mainWindow.isMinimized()) mainWindow.restore();
    mainWindow.show();
    mainWindow.focus();
    if (desktopMounted) writeDesktopState('ready');
  }
});

app.on('before-quit', async (event) => {
  if (backendManager) {
    event.preventDefault();
    if (quitting) return;
    quitting = true;
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
