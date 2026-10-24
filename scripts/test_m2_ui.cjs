// M2 界面冒烟：仅使用临时资料库、独立 Electron profile 和自有后端。
// 先 npm run build --prefix frontend，再使用本地 Electron 执行本脚本。
const { app, BrowserWindow } = require('electron');
const { spawn } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const assert = require('node:assert/strict');
const repo = path.resolve(__dirname, '..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-m2-ui-'));
app.setPath('userData', path.join(root, 'profile'));
app.on('window-all-closed', () => {});
let backend, server, window;
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
  async function importPath(source) {
    await click('收录本机文件');
    const dialog = `[...document.querySelectorAll('.el-dialog')].find(e => e.getClientRects().length && e.textContent.includes('收录本机文件'))`;
    await until(() => js(`Boolean((${dialog})?.querySelector('input'))`), '收录路径输入');
    await js(`(() => { const input = (${dialog}).querySelector('input'); input.value = ${JSON.stringify(source)}; input.dispatchEvent(new Event('input', { bubbles:true })); })()`);
    await click('移动到待整理', dialog);
  }
  const countVersions = count => until(() => js(`document.querySelectorAll('.version-item').length === ${count}`), `${count} 个历史版本`);
  async function closeDrawer() {
    await js(`document.querySelector('.el-drawer__close-btn').click()`);
    await until(() => js(`!document.querySelector('.el-drawer__body .version-item')`), '关闭详情');
  }
  await window.loadURL('http://127.0.0.1:5174/#/pending');
  await click('打开文件库');
  const source = path.join(root, 'M2演示.txt');
  fs.writeFileSync(source, '第一版');
  await importPath(source);
  await countVersions(1);
  const results = await (await fetch(api + '/api/documents/search', {
    method: 'POST', headers: {'Content-Type':'application/json'}, body: JSON.stringify({status:'pending'}),
  })).json();
  const doc = results.documents[0];
  assert.equal(fs.existsSync(source), false, '收录成功后源位置不再保留');
  fs.writeFileSync(path.join(root, 'library', doc.storage_path), '第二版');
  await click('保存新版本');
  await countVersions(2);
  await click('恢复到此版本');
  await until(async () => (await (await fetch(api + `/api/documents/${doc.id}/versions`)).json()).find(v => v.is_current)?.version_number === 1, '当前版本切换v1');
  await countVersions(2);
  assert.equal(fs.readFileSync(path.join(root, 'library', doc.storage_path), 'utf8'), '第一版', '恢复必须同时替换当前工作内容');
  const v1Scope = `[...document.querySelectorAll('.version-item')].find(e => e.querySelector('.version-number').textContent === 'v1')`;
  await click('导出此版本', v1Scope);
  const exportDir = path.join(root, 'export');
  await inputPrompt(exportDir, '导出');
  await until(() => fs.existsSync(path.join(exportDir, 'M2演示.txt')), '导出内容');
  assert.equal(fs.readFileSync(path.join(exportDir, 'M2演示.txt'), 'utf8'), '第一版');
  const versions = await (await fetch(api + `/api/documents/${doc.id}/versions`)).json();
  assert.equal(versions.length, 2);
  assert.deepEqual(versions.filter(v => v.is_current).map(v => v.version_number), [1]);
  assert.equal(fs.readFileSync(versions.find(v => v.version_number === 2).file_path, 'utf8'), '第二版');
  fs.writeFileSync(path.join(root, 'm2-versions.png'), (await window.capturePage()).toPNG());
  await closeDrawer();
  fs.writeFileSync(source, '第一版');
  await importPath(source);
  await countVersions(2);
  assert.equal(fs.readFileSync(source, 'utf8'), '第一版', '重复内容收录保留源文件');
  const repeated = await (await fetch(api + '/api/documents/search', {
    method:'POST', headers:{'Content-Type':'application/json'}, body:'{}',
  })).json();
  assert.equal(repeated.total, 1);
  console.log(`M2 UI PASS: 移动收录、保存 v2、直接恢复 v1、导出 v1、重复收录打开既有资料。证据：${root}`);
}
app.whenReady().then(run).then(() => cleanup(0), async error => {
  console.error(error);
  console.error(`验证日志：${root}`);
  if (window && !window.isDestroyed()) {
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
  if (server?.listening) await new Promise(resolve => server.close(resolve));
  app.exit(code);
}
