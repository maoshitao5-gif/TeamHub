// M5 界面冒烟：仅使用临时资料库、独立 Electron profile 和自有后端。
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
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-m5-ui-'));
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
    if (text === '确定') await until(() => js(`![...document.querySelectorAll('.el-message-box')].some(e => e.getClientRects().length)`), '确认弹窗关闭');
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
  const seed = fixture.match(/SEED_SCRIPT = """([\s\S]*?)"""/)[1] + `
for email, name, role, superuser in [("member-c@example.com", "C", "member", False), ("admin-m5@example.com", "管理员", "admin", True)]:
    member = models.User(id=str(uuid.uuid4()), email=email, hashed_password=hash_password("e2e-pass-123"), display_name=name, is_superuser=superuser)
    db.add(member)
    db.flush()
    db.add(models.TeamMember(team_id=t.id, user_id=member.id, role=role))
db.commit()
`;
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
  await post('/api/sync/workspace', { workspace_id: ws, workspace_name: 'M5测试空间' });
  const source = path.join(root, 'M5工程文件夹');
  fs.mkdirSync(source);
  fs.writeFileSync(path.join(source, '报告.txt'), '第一版');
  const doc = await post('/api/documents', { source_path: source, name: 'M5工程文件夹', tags: ['设备', '检修'] });
  const published = await post('/api/sharing/publish', { doc_id: doc.id, workspace_id: ws, visibility: 'workspace', confirmed: true });
  async function enterAs(email) {
    const throughLoginForm = email === 'member-b@example.com';
    if (throughLoginForm) await post('/api/sync/logout', {});
    else {
      await post('/api/sync/login', { email, password: 'e2e-pass-123' });
      await post('/api/sync/workspace', { workspace_id: ws, workspace_name: 'M5测试空间' });
    }
    await window.loadURL('http://127.0.0.1:5174/?actor=' + encodeURIComponent(email) + '#/library');
    await until(() => js(`document.body.innerText.includes('M5工程文件夹') || [...document.querySelectorAll('button')].some(e => e.textContent.trim() === '打开文件库')`), '个人库入口');
    if (await js(`[...document.querySelectorAll('button')].some(e => e.getClientRects().length && e.textContent.trim() === '打开文件库')`)) await click('打开文件库');
    if (throughLoginForm) {
      await js(`location.hash = '#/login'`);
      await fill('input[type="email"]', email);
      await fill('input[type="password"]', 'e2e-pass-123');
      await click('登录');
      await until(() => js(`location.hash !== '#/login' && document.body.innerText.includes('云仓库')`), '真实登录表单与空间绑定');
    }
    await until(() => js(`document.body.innerText.includes('云仓库')`), '登录恢复');
    await js(`location.hash = '#/shared'`);
    await until(() => js(`document.querySelector('.shared-doc') !== null`), '共享资料加载');
  }
  async function fill(selector, text) {
    await until(() => js(`document.querySelector(${JSON.stringify(selector)}) !== null`), selector);
    await js(`(() => { const e = document.querySelector(${JSON.stringify(selector)}); Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(e, ${JSON.stringify(text)}); e.dispatchEvent(new Event('input', { bubbles:true })); })()`);
  }
  async function tab(label) {
    await until(() => js(`[...document.querySelectorAll('[role=tab]')].some(e => e.textContent.trim() === ${JSON.stringify(label)})`), label);
    await js(`[...document.querySelectorAll('[role=tab]')].find(e => e.textContent.trim() === ${JSON.stringify(label)}).click()`);
  }
  await enterAs('member-b@example.com');
  assert.equal(await js(`document.body.innerText.includes('共享回收站')`), false);
  await click('查看详情');
  await until(() => js(`document.querySelectorAll('.tag-contribution').length === 2`), '原标签');
  assert.equal(await js(`document.querySelectorAll('.tag-contribution button').length`), 0);
  await click('收藏此共享资料');
  await until(async () => (await (await fetch(api + '/api/favorites?target_type=shared')).json()).length === 1, '共享收藏落库');
  await js(`document.querySelector('.el-drawer__close-btn').click()`);
  await until(() => js(`![...document.querySelectorAll('.el-drawer')].some(e => e.getClientRects().length)`), '收藏前关闭详情');
  await click('共享收藏');
  await until(() => js(`document.querySelector('.shared-favorite-row')?.textContent.includes('M5工程文件夹')`), '共享收藏解析');
  await click('打开资料');
  await until(() => js(`document.querySelectorAll('.tag-contribution').length === 2 && [...document.querySelectorAll('.el-drawer')].some(e => e.getClientRects().length)`), '从收藏打开同一资料');
  await fill('input[placeholder="补充一个方便查找的标签"]', 'B新增线索');
  await click('补充标签');
  await until(() => js(`document.querySelectorAll('.tag-contribution').length === 3`), 'B 标签写入');
  assert.equal(await js(`document.querySelectorAll('.tag-contribution button').length`), 1);
  await fill('input[placeholder="补充一个方便查找的标签"]', '临时标注');
  await click('补充标签');
  await until(() => js(`document.querySelectorAll('.tag-contribution').length === 4`), '额外标注');
  await js(`[...document.querySelectorAll('.tag-contribution')].find(e => e.textContent.includes('临时标注')).querySelector('button').click()`);
  await until(() => js(`document.querySelectorAll('.tag-contribution').length === 3`), '撤销本人标注');
  await enterAs('member-c@example.com');
  await js(`document.querySelector('.search-row .el-select input').focus()`);
  await fill('.search-row .el-select input', 'B新增');
  await until(() => js(`[...document.querySelectorAll('.el-select-dropdown__item')].some(e => e.textContent.includes('B新增线索'))`), 'C 的标签联想');
  await js(`[...document.querySelectorAll('.el-select-dropdown__item')].find(e => e.textContent.includes('B新增线索')).click()`);
  await until(() => js(`document.querySelectorAll('.shared-doc').length === 1 && document.body.innerText.includes('共 1 份资料')`), 'C 按 B 的标签找到资料');
  await click('查看详情');
  await until(() => js(`document.querySelectorAll('.tag-contribution').length === 3`), 'C 查看贡献');
  assert.equal(await js(`document.querySelectorAll('.tag-contribution button').length`), 0);
  fs.writeFileSync(path.join(root, 'm5-c-search.png'), (await window.capturePage()).toPNG());
  await click('取回此版本');
  await click('确认取回');
  await until(() => js(`![...document.querySelectorAll('.el-dialog')].some(e => e.getClientRects().length && e.textContent.includes('确认取回'))`), '文件夹版本取回');
  const personal = await post('/api/documents/search', {});
  assert.equal(personal.total, 2);
  const imported = personal.documents.find(item => item.id !== doc.id);
  assert.equal(fs.readFileSync(path.join(root, 'library', imported.storage_path, '报告.txt'), 'utf8'), '第一版');
  await enterAs('admin-m5@example.com');
  await click('移入共享回收站');
  await click('确定', "document.querySelector('.el-message-box')");
  await until(() => js(`document.querySelectorAll('.shared-doc').length === 0`), '正常列表隐藏');
  await click('共享收藏');
  await until(() => js(`document.querySelector('.shared-favorite-row')?.textContent.includes('暂不可访问的收藏')`), '回收后收藏不可访问');
  assert.equal(await js(`document.querySelector('.shared-favorite-row button').disabled`), true);
  await click('取消收藏');
  await until(() => js(`document.querySelectorAll('.shared-favorite-row').length === 0`), '取消共享收藏');
  await js(`[...document.querySelectorAll('.el-dialog')].find(e => e.getClientRects().length && e.textContent.includes('本空间的共享收藏')).querySelector('.el-dialog__headerbtn').click()`);
  await tab('共享回收站');
  await click('恢复资料');
  await until(() => js(`document.body.innerText.includes('共享回收站为空')`), '恢复完成');
  await tab('资料查找');
  await click('移入共享回收站');
  await click('确定', "[...document.querySelectorAll('.el-message-box')].find(e => e.getClientRects().length)");
  await until(() => js(`document.querySelectorAll('.shared-doc').length === 0`), '再次回收');
  await tab('共享回收站');
  await click('永久删除');
  await click('确定', "[...document.querySelectorAll('.el-message-box')].find(e => e.getClientRects().length)");
  await until(() => js(`document.body.innerText.includes('共享回收站为空')`), '永久删除完成');
  await tab('成员账号');
  await until(() => js(`[...document.querySelectorAll('.shared-doc')].some(e => e.querySelector('strong')?.textContent === 'B')`), '成员账号');
  await js(`[...document.querySelectorAll('.shared-doc')].find(e => e.querySelector('strong')?.textContent === 'B').querySelector('button').click()`);
  await click('确定', "[...document.querySelectorAll('.el-message-box')].find(e => e.getClientRects().length)");
  await until(() => js(`[...document.querySelectorAll('.shared-doc')].find(e => e.querySelector('strong')?.textContent === 'B').textContent.includes('已停用')`), 'B 已停用');
  await pause(800);
  fs.writeFileSync(path.join(root, 'm5-collaboration.png'), (await window.capturePage()).toPNG());
  console.log(`M5 UI PASS: B 补标签/撤销本人标注、C 用 B 标签查找、权限显示、管理员回收恢复删除、成员停用。证据：${root}`);
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
