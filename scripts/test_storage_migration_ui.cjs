// 独立存储迁移的桌面 UI 验收；仅访问临时库、独立 profile 和自有后端。
// 先 npm run frontend:build，再 env -u ELECTRON_RUN_AS_NODE node_modules/.bin/electron scripts/test_storage_migration_ui.cjs。
const { app, BrowserWindow } = require('electron');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-storage-ui-')));
const libraryPath = path.join(root, 'library');
const capability = require('node:crypto').randomBytes(32).toString('hex');
const runtimeErrors = [];
app.setPath('userData', path.join(root, 'profile'));
app.on('window-all-closed', () => {});
let backend, window;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return;
    await pause(100);
  }
  throw new Error(`等待超时：${label}`);
}

async function freePort() {
  const socket = net.createServer();
  await new Promise((resolve, reject) => {
    socket.once('error', reject);
    socket.listen(0, '127.0.0.1', resolve);
  });
  const value = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return value;
}

async function run() {
  const api = `http://127.0.0.1:${await freePort()}`;
  const instanceId = randomUUID();
  const log = fs.openSync(path.join(root, 'backend.log'), 'w');
  backend = spawn(path.join(repo, 'venv/bin/python'), [
    '-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port,
  ], {
    cwd: root,
    env: {
      PATH: process.env.PATH, HOME: process.env.HOME,
      TEAMHUB_SANDBOX: '1', TEAMHUB_INSTANCE_ID: instanceId,
      ELECTRON_APP: 'true', USER_DATA_DIR: path.join(root, 'backend-profile'),
      TEAMHUB_AI_CAPABILITY: capability,
      LIBRARY_PATH: libraryPath, TRASH_AUTO_CLEAN_DAYS: '0',
    },
    stdio: ['ignore', log, log],
  });
  fs.closeSync(log);
  let backendError;
  backend.once('error', error => { backendError = error; });
  await until(async () => {
    if (backendError) throw backendError;
    if (backend.exitCode !== null) throw new Error('隔离后端提前退出，见 backend.log');
    try {
      const health = await (await fetch(api + '/api/health')).json();
      return health.instance_id === instanceId && health.library_path === libraryPath;
    } catch { return false; }
  }, '确认自有临时后端');

  async function request(route, body, method = 'POST') {
    const response = await fetch(api + route, {
      method: body === undefined ? 'GET' : method,
      headers: { 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    assert.ok(response.ok, `${route}: ${response.status} ${JSON.stringify(result)}`);
    return result;
  }
  const source = path.join(root, '待整理样本.txt');
  fs.writeFileSync(source, '迁移后继续整理');
  const pending = await request('/api/documents/quick-add', { source_path: source });
  const other = path.join(root, '正式资料.txt');
  fs.writeFileSync(other, '版本内容保持不变');
  const organized = await request('/api/documents/quick-add', { source_path: other });
  await request(`/api/documents/${organized.id}/organize`, { tags: ['主变', '检修'] });
  const untouched = path.join(libraryPath, '未收录.txt');
  fs.writeFileSync(untouched, '不应迁移');
  const preload = path.join(root, 'preload.cjs');
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron', { apiBaseURL: ${JSON.stringify(api)}, aiCapability: ${JSON.stringify(capability)} });`);
  window = new BrowserWindow({ show: false, width: 1440, height: 1100,
    webPreferences: { preload, contextIsolation: true, nodeIntegration: false } });
  window.webContents.debugger.attach('1.3');
  window.webContents.debugger.on('message', (_event, method, params) => {
    if (method === 'Runtime.exceptionThrown') runtimeErrors.push(params.exceptionDetails);
    if (method === 'Runtime.consoleAPICalled' && params.type === 'error') {
      runtimeErrors.push(params.args.map(arg => arg.value || arg.description).join(' '));
    }
  });
  // 空白页尚未创建执行上下文，先发起订阅再加载页面，不能在导航前等确认。
  const runtimeReady = window.webContents.debugger.sendCommand('Runtime.enable');
  const js = expression => window.webContents.executeJavaScript(expression);
  const dialog = `[...document.querySelectorAll('.el-dialog')].find(e => e.getClientRects().length)`;
  const card = name => `[...document.querySelectorAll('.pending-card')].find(e => e.querySelector('.doc-name').textContent === ${JSON.stringify(name)})`;
  async function click(text, scope = 'document') {
    const buttons = `[...(${scope})?.querySelectorAll('button') || []].filter(b => b.getClientRects().length && !b.disabled)`;
    await until(() => js(`${buttons}.some(b => b.textContent.trim() === ${JSON.stringify(text)})`), `按钮 ${text}`);
    await js(`${buttons}.find(b => b.textContent.trim() === ${JSON.stringify(text)}).click()`);
  }
  await window.loadFile(path.join(repo, 'frontend/dist/index.html'), { hash: '/settings' });
  await runtimeReady;
  await click('打开文件库');
  await until(() => js(`document.body.innerText.includes('待整理路径')`), '设置页加载');
  const rows = `[...document.querySelectorAll('.settings-form .el-form-item')]`;
  async function migrate(label, destination) {
    await click('迁移', `${rows}.find(e => e.innerText.includes(${JSON.stringify(label)}))`);
    await until(() => js(`Boolean(${dialog})`), '迁移对话框');
    await js(`(() => { const input = (${dialog}).querySelector('input[placeholder]'); input.value = ${JSON.stringify(destination)}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await click('查看迁移清单', dialog);
    await until(() => js(`(${dialog}).innerText.includes('将迁移 1 项资料')`), '登记清单');
    await pause(300);
    fs.writeFileSync(path.join(root, label + '.png'), (await window.capturePage()).toPNG());
    await click('确认迁移', dialog);
    await until(() => js(`!(${dialog})`), '迁移完成');
    await until(() => js(`[...document.querySelectorAll('.storage-path-row input')].some(e => e.value === ${JSON.stringify(destination)})`), '设置路径更新');
  }
  const pendingPath = path.join(root, '独立待整理');
  const libraryNew = path.join(pendingPath, '文件库');
  await migrate('待整理路径', pendingPath);
  assert.ok(fs.existsSync(path.join(pendingPath, '待整理样本.txt')));
  await migrate('文件库路径', libraryNew);
  assert.ok(fs.existsSync(path.join(libraryNew, '正式资料.txt')));
  assert.ok(fs.existsSync(untouched));
  assert.ok(fs.existsSync(path.join(pendingPath, '待整理样本.txt')));
  const info = await request('/api/settings/library');
  assert.equal(info.path, libraryNew);
  assert.equal(info.pending_path, pendingPath);
  const doc = await request(`/api/documents/${organized.id}`);
  assert.deepEqual(doc.tags.map(t => t.name).sort(), ['主变', '检修'].sort());
  assert.equal(doc.current_version.version_number, 1);
  // 迁移之后仍可整理外部待整理资料。
  const result = await request(`/api/documents/${pending.id}/organize`, { tags: ['迁移后整理'] });
  assert.equal(result.working_path, path.join(libraryNew, '待整理样本.txt'));
  // 模拟后端重启前丢失运行时设置；旧入口沿日志定位到新库。
  assert.equal((await request('/api/settings/storage/migration')).phase, 'complete');
  assert.deepEqual(runtimeErrors, []);
  window.destroy();
  backend.kill('SIGTERM');
  await new Promise(resolve => backend.once('exit', resolve));
  const restartLog = fs.openSync(path.join(root, 'restart.log'), 'w');
  backend = spawn(path.join(repo, 'venv/bin/python'), [
    '-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port,
  ], { cwd: root, env: {
    PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1',
    ELECTRON_APP: 'true', USER_DATA_DIR: path.join(root, 'backend-profile'),
    TEAMHUB_AI_CAPABILITY: capability,
    LIBRARY_PATH: libraryPath, TRASH_AUTO_CLEAN_DAYS: '0',
  }, stdio: ['ignore', restartLog, restartLog] });
  fs.closeSync(restartLog);
  await until(async () => {
    try { return (await request('/api/settings/library')).path === libraryNew; } catch { return false; }
  }, '重启沿迁移日志恢复新库');
  assert.equal((await request('/api/settings/library')).pending_path, pendingPath);
  assert.equal((await request(`/api/documents/${organized.id}`)).current_version.version_number, 1);
  console.log('STORAGE UI PASS: 清单确认、独立待整理、文件库嵌套、未收录内容保留、标签版本及后续整理。证据：' + root);
}
async function cleanup(code) {
  if (window && !window.isDestroyed()) window.destroy();
  if (backend && backend.exitCode === null) {
    backend.kill('SIGTERM');
    await Promise.race([new Promise(resolve => backend.once('exit', resolve)), pause(3000)]);
    if (backend.exitCode === null) backend.kill('SIGKILL');
  }
  app.exit(code);
}
app.whenReady().then(run).then(() => cleanup(0), async error => {
  console.error(error);
  console.error(`验收日志：${root}`);
  try {
    if (window && !window.isDestroyed()) {
      fs.writeFileSync(path.join(root, 'failure.png'), (await window.capturePage()).toPNG());
      fs.writeFileSync(path.join(root, 'failure.txt'), await window.webContents.executeJavaScript('document.body.innerText'));
    }
    fs.writeFileSync(path.join(root, 'runtime-errors.json'), JSON.stringify(runtimeErrors, null, 2));
  } catch (diagnosticError) {
    console.error('采集诊断失败：', diagnosticError);
  } finally {
    await cleanup(1);
  }
});
