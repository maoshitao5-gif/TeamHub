// M4 界面冒烟：仅使用临时资料库、独立 Electron profile 和自有后端。
// 先 npm run build --prefix frontend，再使用本地 Electron 执行本脚本。
const { app, BrowserWindow } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const assert = require('node:assert/strict');
const repo = path.resolve(__dirname, '..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-delivery-ui-'));
const capability = require('node:crypto').randomBytes(32).toString('hex');
app.setPath('userData', path.join(root, 'profile'));
app.on('window-all-closed', () => {});
let backend, recipientBackend, cloud, server, window, senderWindow, recipientWindow;
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 12000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) {
    if (await check()) return;
    await pause(100);
  }
  throw new Error(`等待超时：${label}`);
}
async function port() {
  const socket = net.createServer();
  await new Promise(resolve => socket.listen(0, '127.0.0.1', resolve));
  const value = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return value;
}
async function run() {
  const backendPort = await port();
  let api = `http://127.0.0.1:${backendPort}`;
  server = http.createServer((req, res) => {
    const relative = decodeURIComponent(req.url.split('?')[0]);
    const file = path.resolve(repo, 'frontend/dist', '.' + (relative === '/' ? '/index.html' : relative));
    if (!file.startsWith(path.join(repo, 'frontend/dist') + path.sep) || !fs.existsSync(file)) {
      res.writeHead(404).end(); return;
    }
    const types = { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' };
    res.setHeader('Content-Type', types[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  // 使用随机端口，并把这个受管测试界面的精确 origin 传给个人服务。
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(0, '127.0.0.1', resolve);
  });
  const frontendURL = `http://127.0.0.1:${server.address().port}/`;
  const log = fs.openSync(path.join(root, 'backend.log'), 'w');
  backend = spawn(path.join(repo, 'venv/bin/python'), [
    '-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', String(backendPort),
  ], {
    cwd: root,
    env: { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1',
      ELECTRON_APP: 'true', USER_DATA_DIR: path.join(root, 'backend-profile'),
      TEAMHUB_AI_CAPABILITY: capability, TEAMHUB_DEV_FRONTEND_URL: frontendURL,
      LIBRARY_PATH: path.join(root, 'library'), TRASH_AUTO_CLEAN_DAYS: '0' },
    stdio: ['ignore', log, log],
  });
  fs.closeSync(log);
  await until(async () => {
    try { return (await fetch(api + '/api/health')).ok; } catch { return false; }
  }, '独立后端');
  const library = await (await fetch(api + '/api/settings/library')).json();
  assert.equal(library.path, path.join(root, 'library'));
  const preload = path.join(root, 'preload.cjs');
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron', { apiBaseURL: ${JSON.stringify(api)}, aiCapability: ${JSON.stringify(capability)} });`);
  window = new BrowserWindow({ show: false, width: 1440, height: 1100,
    webPreferences: { preload, contextIsolation: true, nodeIntegration: false } });
  const js = expression => window.webContents.executeJavaScript(expression).catch(error=>{console.error('UI expression failed:',expression);throw error});
  const visibleButtons = `[...document.querySelectorAll('button')].filter(b => b.getClientRects().length && !b.disabled)`;
  async function click(text, scope = 'document') {
    const query = scope === 'document' ? visibleButtons : `[...${scope}.querySelectorAll('button')].filter(b => !b.disabled)`;
    await until(() => js(`${query}.some(b => b.textContent.trim() === ${JSON.stringify(text)})`), text);
    await js(`${query}.find(b => b.textContent.trim() === ${JSON.stringify(text)}).click()`);
  }
  async function inputPrompt(value, confirm) {
    await until(() => js(`[...document.querySelectorAll('.el-message-box input')].some(e => e.getClientRects().length)`), '可见输入框');
    await js(`(() => { const input = [...document.querySelectorAll('.el-message-box input')].find(e => e.getClientRects().length);
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(input, ${JSON.stringify(value)});
      input.dispatchEvent(new Event('input', {bubbles:true})); })()`);
    await click(confirm, "[...document.querySelectorAll('.el-message-box input')].find(e => e.getClientRects().length).closest('.el-message-box')");
  }
  const countVersions = count => until(() => js(`document.querySelectorAll('.version-item').length === ${count}`), `${count} 个历史版本`);
  async function closeDrawer() {
    await js(`document.querySelector('.el-drawer__close-btn').click()`);
    await until(() => js(`!document.querySelector('.el-drawer__body .version-item')`), '关闭详情');
  }
  const cloudPort = await port();
  const cloudUrl = `http://127.0.0.1:${cloudPort}`;
  const cloudEnv = { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1',
    DATABASE_URL: `sqlite:///${root}/cloud.db`, STORAGE_DIR: path.join(root, 'cloud-objects'), JWT_SECRET_KEY: require('node:crypto').randomBytes(32).toString('hex') };
  const fixture = fs.readFileSync(path.join(repo, 'backend/tests/test_m4_sharing_e2e.py'), 'utf8');
  const seed = fixture.match(/SEED_SCRIPT = """([\s\S]*?)"""/)[1];
  const seeded = spawnSync(path.join(repo, 'cloud_backend/venv/bin/python'), ['-c', seed], {
    cwd: path.join(repo, 'cloud_backend'), env: cloudEnv, encoding: 'utf8', timeout: 30000 });
  assert.equal(seeded.status, 0, seeded.stderr);
  const ws = seeded.stdout.trim().split('\n').at(-1);
  const cloudLog = fs.openSync(path.join(root, 'cloud.log'), 'w');
  cloud = spawn(path.join(repo, 'cloud_backend/venv/bin/python'), ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(cloudPort)], {
    cwd: path.join(repo, 'cloud_backend'), env: cloudEnv, stdio: ['ignore', cloudLog, cloudLog] });
  fs.closeSync(cloudLog);
  await until(async () => { try { return (await fetch(cloudUrl + '/api/health')).ok; } catch { return false; } }, '共享服务');
  const configFile = path.join(root, 'library/.teamhub/config.json');
  const config = fs.existsSync(configFile) ? JSON.parse(fs.readFileSync(configFile, 'utf8')) : {};
  fs.mkdirSync(path.dirname(configFile), { recursive: true });
  fs.writeFileSync(configFile, JSON.stringify({ ...config, cloud_api_url: cloudUrl }));
  async function post(route, body) {
    const response = await fetch(api + route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const value = await response.json();
    assert.ok(response.ok, JSON.stringify(value));
    return value;
  }
  await post('/api/sync/login', { email: 'e2e@example.com', password: 'e2e-pass-123' });
  await post('/api/sync/workspace', { workspace_id: ws, workspace_name: 'M4测试空间' });

  const source = path.join(root, '投递验收.txt');
  fs.writeFileSync(source, '固定投递内容');
  const doc = await post('/api/documents', {source_path:source,name:'投递验收',tags:['验收']});
  await window.loadURL(frontendURL + '#/library');
  await click('打开文件库');
  await until(()=>js(`!!document.querySelector('.doc-card')`),'资料卡片');
  await js(`document.querySelector('.doc-card').click()`);
  await click('投递给成员');
  await until(()=>js(`!!document.querySelector('.el-dialog .el-select')`),'投递表单');
  await js(`document.querySelector('.el-dialog .el-select input').click()`);
  await until(()=>js(`[...document.querySelectorAll('.el-select-dropdown__item')].some(e=>e.getClientRects().length&&e.textContent.trim()==='B')`),'收件人B');
  await js(`[...document.querySelectorAll('.el-select-dropdown__item')].find(e=>e.getClientRects().length&&e.textContent.trim()==='B').click()`);
  await js(`document.querySelector('.el-dialog').click()`);
  await click('确认发送');
  let sent;
  await until(async()=>{const r=await(await fetch(api+`/api/deliveries?workspace_id=${ws}&box=outbox`)).json();sent=r.items?.[0];return !!sent},'已发送');
  // A 与 B 使用各自的个人资料库，避免把发送者现有文件误当作收件者查重结果。
  const senderApi = api;
  senderWindow = window;
  api = `http://127.0.0.1:${await port()}`;
  const recipientCapability = require('node:crypto').randomBytes(32).toString('hex');
  const recipientLibrary = path.join(root, 'recipient-library');
  const recipientLog = fs.openSync(path.join(root, 'recipient-backend.log'), 'w');
  recipientBackend = spawn(path.join(repo, 'venv/bin/python'), [
    '-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port,
  ], { cwd: root, env: { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1',
    ELECTRON_APP: 'true', USER_DATA_DIR: path.join(root, 'recipient-profile'),
    TEAMHUB_AI_CAPABILITY: recipientCapability, TEAMHUB_DEV_FRONTEND_URL: frontendURL,
    LIBRARY_PATH: recipientLibrary, TRASH_AUTO_CLEAN_DAYS: '0',
  }, stdio: ['ignore', recipientLog, recipientLog] });
  fs.closeSync(recipientLog);
  await until(async () => {
    if (recipientBackend.exitCode !== null) throw new Error('收件人隔离后端提前退出');
    try { return (await fetch(api + '/api/health')).ok; } catch { return false; }
  }, '收件人独立后端');
  assert.equal((await (await fetch(api + '/api/settings/library')).json()).path, recipientLibrary);
  const configured = await fetch(api + '/api/settings/config', { method: 'PUT',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cloud_api_url: cloudUrl }) });
  assert.equal(configured.status, 200);
  const recipientPreload = path.join(root, 'recipient-preload.cjs');
  fs.writeFileSync(recipientPreload, `require('electron').contextBridge.exposeInMainWorld('electron', { apiBaseURL: ${JSON.stringify(api)}, aiCapability: ${JSON.stringify(recipientCapability)} });`);
  recipientWindow = new BrowserWindow({ show: false, width: 1440, height: 1100,
    webPreferences: { preload: recipientPreload, contextIsolation: true, nodeIntegration: false } });
  window = recipientWindow;
  await post('/api/sync/login',{email:'member-b@example.com',password:'e2e-pass-123'});
  await post('/api/sync/workspace', { workspace_id: ws, workspace_name: 'M4测试空间' });
  await window.loadURL(frontendURL + '#/deliveries');
  if(await js(`document.body.innerText.includes('打开文件库')`))await click('打开文件库');
  await until(()=>js(`document.body.innerText.includes('投递验收.txt')`),'B收到资料');
  await until(()=>js(`!!document.querySelector('.delivery-table .el-table__row')`),'投递行已渲染');
  await js(`document.querySelector('.delivery-table .el-table__row').click()`);
  await click('收录到待整理');
  await until(()=>js(`document.body.innerText.includes('已收录到待整理')`),'主动收录');
  assert.equal((await (await fetch(api + '/api/documents/pending/count')).json()).count, 1);
  const received = await post('/api/documents/search', { status: 'pending' });
  assert.equal(received.documents.length, 1);
  assert.equal(fs.readFileSync(path.join(recipientLibrary, received.documents[0].storage_path), 'utf8'), '固定投递内容');
  window = senderWindow;
  api = senderApi;
  await post('/api/sync/login',{email:'e2e@example.com',password:'e2e-pass-123'});
  await window.loadURL(frontendURL + '#/deliveries');
  await until(() => js(`document.querySelector('#tab-outbox') !== null || document.body.innerText.includes('打开文件库')`), '返回发送者工作台');
  if (await js(`document.body.innerText.includes('打开文件库')`)) await click('打开文件库');
  await until(()=>js(`document.querySelector('#tab-outbox')!==null`),'投递导航');
  await js(`document.querySelector('#tab-outbox').click()`);
  await until(()=>js(`!!document.querySelector('.delivery-table .el-table__row')`),'已发送列表');
  await until(()=>js(`!!document.querySelector('.delivery-table .el-table__row')`),'投递行已渲染');
  await js(`document.querySelector('.delivery-table .el-table__row').click()`);
  await click('撤回投递');await click('确认撤回');
  await until(()=>js(`document.body.innerText.includes('发送者已撤回')`),'撤回完成');
  cloud.kill('SIGTERM');
  await until(async()=>{try{return !(await fetch(cloudUrl+'/api/health')).ok}catch{return true}},'云端已停止');
  await window.loadURL(frontendURL + '#/library');
  await until(()=>js(`!!document.querySelector('.doc-card')`),'离线文档库');
  fs.writeFileSync(path.join(root,'delivery.png'),(await window.capturePage()).toPNG());
  console.log(`C6 Desktop UI PASS: 本地资料发送、B收件/主动收录、整次撤回、云端停止后个人库仍可用。证据: ${root}`);
}
app.whenReady().then(run).then(() => cleanup(0), async error => {
  console.error(error);
  console.error(`验证日志：${root}`);
  if (window && !window.isDestroyed() && window.webContents.getURL()) {
    fs.writeFileSync(path.join(root, 'failure.png'), (await window.capturePage()).toPNG());
    fs.writeFileSync(path.join(root, 'failure.txt'), await window.webContents.executeJavaScript('document.body.innerText'));
  }
  cleanup(1);
});
async function cleanup(code) {
  if (window && !window.isDestroyed()) window.destroy();
  for (const view of [senderWindow, recipientWindow]) if (view && !view.isDestroyed()) view.destroy();
  if (recipientBackend && recipientBackend.exitCode === null) {
    recipientBackend.kill('SIGTERM');
    await Promise.race([new Promise(resolve => recipientBackend.once('exit', resolve)), pause(3000)]);
    if (recipientBackend.exitCode === null) recipientBackend.kill('SIGKILL');
  }
  if (backend && backend.exitCode === null) {
    backend.kill('SIGTERM');
    await Promise.race([new Promise(resolve => backend.once('exit', resolve)), pause(3000)]);
    if (backend.exitCode === null) backend.kill('SIGKILL');
  }
  if (cloud && cloud.exitCode === null) {
    cloud.kill('SIGTERM');
    await Promise.race([new Promise(resolve => cloud.once('exit', resolve)), pause(3000)]);
    if (cloud.exitCode === null) cloud.kill('SIGKILL');
  }
  if (server?.listening) await new Promise(resolve => server.close(resolve));
  app.exit(code);
}
