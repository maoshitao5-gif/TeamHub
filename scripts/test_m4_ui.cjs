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
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-m4-ui-'));
app.setPath('userData', path.join(root, 'profile'));
app.on('window-all-closed', () => {});
let backend, cloud, server, window;
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
  const api = `http://127.0.0.1:${backendPort}`;
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
  // 5174 在项目 CORS 白名单中；若已占用则退出，不接管用户服务。
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(5174, '127.0.0.1', resolve);
  });
  const log = fs.openSync(path.join(root, 'backend.log'), 'w');
  backend = spawn(path.join(repo, 'venv/bin/python'), [
    '-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', String(backendPort),
  ], {
    cwd: root,
    env: { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1',
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
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron', { apiBaseURL: ${JSON.stringify(api)} });`);
  window = new BrowserWindow({ show: false, width: 1440, height: 1100,
    webPreferences: { preload, contextIsolation: true, nodeIntegration: false } });
  const js = expression => window.webContents.executeJavaScript(expression);
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
  const source = path.join(root, 'M4工程文件夹');
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, '报告.txt'), '第一版');
  const doc = await post('/api/documents', { source_path: source, name: 'M4工程文件夹', tags: ['设备', '检修'] });
  await window.loadURL('http://127.0.0.1:5174/#/library');
  await click('打开文件库');
  async function openShare() {
    await until(() => js(`![...document.querySelectorAll('.el-dialog')].some(e => e.getClientRects().length && e.textContent.includes('确认共享资料'))`), '上次确认窗已关闭');
    await until(() => js(`document.querySelector('.doc-actions .el-dropdown button') !== null`), '资料卡片');
    await js(`document.querySelector('.doc-actions .el-dropdown button').click()`);
    await until(() => js(`[...document.querySelectorAll('.el-dropdown-menu__item')].some(e => e.getClientRects().length && e.textContent.includes('发布 / 更新'))`), '共享菜单');
    await js(`[...document.querySelectorAll('.el-dropdown-menu__item')].find(e => e.getClientRects().length && e.textContent.includes('发布 / 更新')).click()`);
    await until(() => js(`document.body.innerText.includes('目标空间：M4测试空间')`), '范围预览');
  }
  await openShare();
  await until(() => js(`document.body.innerText.includes('首次发布随行标签：') && document.body.innerText.includes('设备') && document.body.innerText.includes('检修')`), '标签预览');
  assert.equal((await (await fetch(api + `/api/sharing/links/${doc.id}`)).json()).linked, false);
  await click('确认共享');
  let link;
  await until(async () => { link = await (await fetch(api + `/api/sharing/links/${doc.id}`)).json(); return link.linked; }, '发布成功');
  const v1 = link.base_version_id;
  fs.writeFileSync(path.join(root, 'library', doc.storage_path, '报告.txt'), '第二版');
  await openShare();
  await until(() => js(`document.body.innerText.includes('更新原资料')`), '更新选择');
  await click('确认共享');
  await until(async () => { link = await (await fetch(api + `/api/sharing/links/${doc.id}`)).json(); return link.base_version_id !== v1; }, '更新成功');
  const fetched = await post('/api/sharing/pull', { cloud_doc_id: link.shared_document_id, workspace_id: ws });
  assert.equal(fs.readFileSync(path.join(root, 'library', fetched.storage_path, '报告.txt'), 'utf8'), '第二版');
  await until(() => js(`![...document.querySelectorAll('.el-dialog')].some(e => e.getClientRects().length && e.textContent.includes('确认共享资料'))`), '关闭确认窗');
  await openShare();
  await js(`[...document.querySelectorAll('.el-radio')].find(e => e.textContent.includes('另建资料')).click()`);
  const originalId = link.shared_document_id;
  await click('确认共享');
  await until(async () => { link = await (await fetch(api + `/api/sharing/links/${doc.id}`)).json(); return link.shared_document_id !== originalId; }, '另建成功');
  await pause(1000);
  fs.writeFileSync(path.join(root, 'm4-share.png'), (await window.capturePage()).toPNG());
  console.log(`M4 UI PASS: 文件夹共享预览、主动确认、更新原资料、取回内容、明确另建。证据：${root}`);
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
