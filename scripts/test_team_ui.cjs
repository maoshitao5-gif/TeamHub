// 团队管理回归：真实本地代理 + 云服务，全部数据和 Electron profile 位于临时目录。
// npm run build --prefix frontend
// env -u ELECTRON_RUN_AS_NODE node_modules/.bin/electron scripts/test_team_ui.cjs
const { app, BrowserWindow } = require('electron');
const { spawn, spawnSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const net = require('node:net');
const assert = require('node:assert/strict');
const repo = path.resolve(__dirname, '..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-team-ui-'));
const processes = [];
const requests = [];
let server, window;
app.setPath('userData', path.join(root, 'profile'));
app.on('window-all-closed', () => {});
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(check, label, timeout = 15000) {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await pause(100);
  }
  throw new Error(`等待超时：${label}`);
}
async function unusedPort() {
  const socket = net.createServer();
  await new Promise((resolve, reject) => { socket.once('error', reject); socket.listen(0, '127.0.0.1', resolve); });
  const value = socket.address().port;
  await new Promise(resolve => socket.close(resolve));
  return value;
}
function launch(command, args, cwd, env, name) {
  const log = fs.openSync(path.join(root, name + '.log'), 'w');
  const child = spawn(command, args, { cwd, env, stdio: ['ignore', log, log] });
  fs.closeSync(log);
  processes.push(child);
  child.on('error', error => console.error(`${name}:`, error));
  return child;
}
async function run() {
  const backendPort = await unusedPort();
  const cloudPort = await unusedPort();
  const api = `http://127.0.0.1:${backendPort}`;
  const cloudUrl = `http://127.0.0.1:${cloudPort}`;
  const baseEnv = { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1' };
  const cloudEnv = { ...baseEnv, DATABASE_URL: `sqlite:///${root}/cloud.db`, STORAGE_DIR: path.join(root, 'cloud-objects'), JWT_SECRET_KEY: require('node:crypto').randomBytes(32).toString('hex') };
  const seed = `
from app.database import init_db, SessionLocal
from app.models import User
from app.core.security import hash_password
init_db()
with SessionLocal() as db:
    for name in ('a', 'b', 'c'):
        db.add(User(email=f'{name}@audit.example', display_name=f'审计成员{name.upper()}', hashed_password=hash_password('audit-pass-123')))
    db.commit()
`;
  const seeded = spawnSync(path.join(repo, 'cloud_backend/venv/bin/python'), ['-c', seed], {
    cwd: path.join(repo, 'cloud_backend'), env: cloudEnv, encoding: 'utf8', timeout: 30000,
  });
  assert.equal(seeded.status, 0, seeded.stderr);
  launch(path.join(repo, 'cloud_backend/venv/bin/python'), ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', String(cloudPort)], path.join(repo, 'cloud_backend'), cloudEnv, 'cloud');
  launch(path.join(repo, 'venv/bin/python'), ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', String(backendPort)], root,
    { ...baseEnv, LIBRARY_PATH: path.join(root, 'library'), TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false' }, 'backend');
  for (const base of [api, cloudUrl]) {
    await until(async () => { try { return (await fetch(base + '/api/health')).ok; } catch { return false; } }, base + ' 已就绪');
  }
  async function request(route, options) {
    const response = await fetch(api + route, options);
    const value = await response.json();
    assert.ok(response.ok, `${route}: ${JSON.stringify(value)}`);
    return value;
  }
  assert.equal((await request('/api/settings/library')).path, path.join(root, 'library'));
  await request('/api/settings/config', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ cloud_api_url: cloudUrl }) });

  const dist = path.join(repo, 'frontend/dist');
  server = http.createServer((req, res) => {
    const relative = decodeURIComponent(req.url.split('?')[0]);
    const file = path.resolve(dist, '.' + (relative === '/' ? '/index.html' : relative));
    if (!file.startsWith(dist + path.sep) || !fs.existsSync(file) || !fs.statSync(file).isFile()) {
      res.writeHead(404).end(); return;
    }
    res.setHeader('Content-Type', { '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.svg': 'image/svg+xml' }[path.extname(file)] || 'application/octet-stream');
    fs.createReadStream(file).pipe(res);
  });
  // 白名单测试端口；占用时直接失败，绝不接管已有服务。
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(5174, '127.0.0.1', resolve); });
  const preload = path.join(root, 'preload.cjs');
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron', { apiBaseURL: ${JSON.stringify(api)} });`);
  window = new BrowserWindow({ show: false, width: 1440, height: 1100, webPreferences: { preload, contextIsolation: true, nodeIntegration: false } });
  window.webContents.session.webRequest.onBeforeRequest((details, callback) => {
    requests.push({ method: details.method, url: details.url });
    callback({});
  });
  const js = expression => window.webContents.executeJavaScript(expression);
  const authStore = `document.querySelector('#app').__vue_app__.config.globalProperties.$pinia._s.get('auth')`;
  const visibleButtons = `[...document.querySelectorAll('button')].filter(b => b.getClientRects().length && !b.disabled)`;
  async function click(label, dialogTitle) {
    const scope = dialogTitle ? ` && b.closest('.el-dialog')?.querySelector('.el-dialog__title')?.textContent.trim() === ${JSON.stringify(dialogTitle)}` : '';
    const match = `${visibleButtons}.filter(b => !b.closest('[class*="-leave-active"], [class*="-leave-to"]') && b.textContent.trim() === ${JSON.stringify(label)}${scope}).at(-1)`;
    await until(() => js(`!!${match}`), label);
    await js(`${match}.click()`);
  }
  async function fill(placeholder, value) {
    const match = `[...document.querySelectorAll('input,textarea')].find(e => e.getClientRects().length && e.placeholder === ${JSON.stringify(placeholder)})`;
    await until(() => js(`!!${match}`), placeholder);
    await js(`(() => { const input = ${match}; input.value = ${JSON.stringify(value)}; input.dispatchEvent(new Event('input', {bubbles: true})); })()`);
  }
  async function route(hash) {
    await js(`location.hash = ${JSON.stringify(hash)}`);
    await until(() => js(`location.hash === ${JSON.stringify('#' + hash)}`), hash);
  }
  async function login(email) {
    await route('/login');
    await fill('your@email.com', email);
    await fill('请输入密码', 'audit-pass-123');
    await click('登录');
    await until(() => js(`location.hash === '#/library'`), email + ' 登录完成');
  }
  const memberRows = `[...document.querySelectorAll('.team-page .el-table__row')]`;
  async function invite(email) {
    await click('邀请成员');
    await fill('成员邮箱地址', email);
    await click('邀请');
  }
  async function switchAccountInPlace(email) {
    // 保持组件挂载，模拟另一个入口切换全局会话，验证不能只依赖路由卸载保护。
    await js(`(async () => { const auth = ${authStore}; await auth.logout(); await auth.login(${JSON.stringify(email)}, 'audit-pass-123'); })()`);
  }
  async function delayTeamResponse(route = '/api/sync/teams', method = 'POST') {
    // 请求已在真实服务器成功提交，只延迟渲染器收到响应的时刻。
    if (!window.webContents.debugger.isAttached()) window.webContents.debugger.attach('1.3');
    await js(`(() => {
      if (window.__auditTeamXHRInstalled) return;
      window.__auditTeamXHRInstalled = true; window.__auditTeamCompleted = 0;
      const originalOpen = XMLHttpRequest.prototype.open;
      XMLHttpRequest.prototype.open = function(method, url, ...args) {
        this.__auditTeamMutation = window.__auditDelayArmed && method.toUpperCase() === window.__auditDelayMethod && new URL(url, location.href).pathname === window.__auditDelayPath;
        if (this.__auditTeamMutation) window.__auditDelayArmed = false;
        return originalOpen.call(this, method, url, ...args);
      };
      const originalSend = XMLHttpRequest.prototype.send;
      XMLHttpRequest.prototype.send = function(...args) {
        if (this.__auditTeamMutation) this.addEventListener('loadend', () => { window.__auditTeamCompleted++; }, {once: true});
        return originalSend.apply(this, args);
      };
    })()`);
    await js(`window.__auditDelayArmed = true; window.__auditDelayMethod = ${JSON.stringify(method)}; window.__auditDelayPath = ${JSON.stringify(route)}`);
    const completed = await js('window.__auditTeamCompleted');
    let paused, protocolError;
    const listener = (_event, event, params) => {
      if (event !== 'Fetch.requestPaused') return;
      if (!paused && params.request.method === method) paused = params;
      else window.webContents.debugger.sendCommand('Fetch.continueRequest', { requestId: params.requestId }).catch(error => { protocolError = error; });
    };
    window.webContents.debugger.on('message', listener);
    await window.webContents.debugger.sendCommand('Fetch.enable', { patterns: [{ urlPattern: api + route, requestStage: 'Response' }] });
    return {
      async createdTeam() {
        await until(() => { if (protocolError) throw protocolError; return paused; }, '团队创建响应已暂停');
        assert.equal(paused.responseStatusCode, 200);
        const response = await window.webContents.debugger.sendCommand('Fetch.getResponseBody', { requestId: paused.requestId });
        return JSON.parse(response.base64Encoded ? Buffer.from(response.body, 'base64').toString('utf8') : response.body);
      },
      async release() {
        const requestStart = requests.length;
        await window.webContents.debugger.sendCommand('Fetch.continueRequest', { requestId: paused.requestId });
        await until(() => js(`window.__auditTeamCompleted > ${completed}`), '迟到响应完成处理');
        // loadend 已触发；下一帧前 Axios 的 promise 回调已执行。
        await js('new Promise(resolve => requestAnimationFrame(() => resolve()))');
        await window.webContents.debugger.sendCommand('Fetch.disable');
        window.webContents.debugger.removeListener('message', listener);
        assert.equal(requests.slice(requestStart).filter(r => r.url === api + '/api/sync/status').length, 0, '旧操作不能在新会话或已卸载页面恢复会话');
      },
    };
  }

  await window.loadURL('http://127.0.0.1:5174/#/login');
  await click('打开文件库');
  await login('a@audit.example');
  await js(`location.hash = '/shared'`);
  await until(() => js(`!!document.querySelector('.onboarding-page')`), '无团队引导');
  assert.ok(await js(`document.body.innerText.includes('请联系团队管理员')`));
  await click('刷新加入状态');
  await until(() => js(`document.body.innerText.includes('尚未加入团队')`), '刷新加入状态');
  assert.equal(requests.filter(r => /join-requests|teams\/discover/.test(r.url)).length, 0, '引导不得调用已停用接口');

  await click('创建团队和工作空间');
  await fill('如：产品研发部', '审计初始团队');
  await fill('如：product-dev', 'audit-first');
  await fill('如：默认工作空间', '审计初始空间');
  await click('一键创建');
  await until(() => js(`location.hash === '#/shared'`), '引导创建成功');
  const firstTeam = (await request('/api/sync/teams'))[0];
  const firstWorkspaces = await request(`/api/sync/teams/${firstTeam.id}/workspaces`);
  assert.equal(firstWorkspaces.length, 1);
  assert.equal(firstWorkspaces[0].name, '审计初始空间');
  assert.equal((await request('/api/sync/status')).workspace_id, firstWorkspaces[0].id);

  const beforeMembers = requests.filter(r => r.method === 'GET' && r.url.endsWith(`/teams/${firstTeam.id}/members`)).length;
  await route('/team');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('a@audit.example'))`), '成员 DTO 显示');
  assert.equal(requests.filter(r => r.method === 'GET' && r.url.endsWith(`/teams/${firstTeam.id}/members`)).length - beforeMembers, 1, '进入页面只加载一次成员');
  await click('创建团队');
  await fill('我的团队', '审计第二团队');
  await fill('my-team（小写字母和连字符）', 'audit-second');
  await click('创建', '创建团队');
  await until(() => js(`document.querySelector('.team-card.is-current')?.innerText.includes('审计第二团队')`), '新建团队自动选中');
  const secondTeam = (await request('/api/sync/teams')).find(t => t.slug === 'audit-second');
  await click('新建工作空间');
  await fill('工作空间名称', '审计第二空间');
  await click('创建', '新建工作空间');
  await until(() => js(`[...document.querySelectorAll('.ws-item')].some(e => e.innerText.includes('审计第二空间'))`), '团队页创建空间');
  assert.equal((await request('/api/sync/status')).workspace_id, (await request(`/api/sync/teams/${secondTeam.id}/workspaces`))[0].id);
  await invite('b@audit.example');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('b@audit.example'))`), '邀请已有账号');
  await js(`${memberRows}.find(r => r.innerText.includes('b@audit.example')).querySelector('button').click()`);
  await click('确定');
  await until(() => js(`!${memberRows}.some(r => r.innerText.includes('b@audit.example'))`), '移除成员');
  assert.equal((await request(`/api/sync/teams/${secondTeam.id}/members`)).length, 1);
  await invite('b@audit.example');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('b@audit.example'))`), '再次邀请');
  await click('退出登录');
  await until(() => js(`document.body.innerText.includes('请先登录云服务账号')`), '登出');
  await login('b@audit.example');
  await route('/team');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('b@audit.example'))`), '普通成员进入团队');
  await invite('c@audit.example');
  await until(() => js(`document.body.innerText.includes('需要管理员权限才能邀请成员')`), '权限失败原样显示');
  assert.equal((await request(`/api/sync/teams/${secondTeam.id}/members`)).length, 2, '失败请求不得添加成员');
  await click('取消');
  await click('新建工作空间');
  await fill('工作空间名称', '不应创建的空间');
  await click('创建', '新建工作空间');
  await until(() => js(`document.body.innerText.includes('权限不足')`), '普通成员不能创建空间');
  assert.equal((await request(`/api/sync/teams/${secondTeam.id}/workspaces`)).length, 1);
  await click('取消');

  await switchAccountInPlace('a@audit.example');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('a@audit.example'))`), 'A重新进入团队');
  const delayedTeam = await delayTeamResponse();
  await click('创建团队');
  await fill('我的团队', 'A迟到团队');
  await fill('my-team（小写字母和连字符）', 'audit-late-a');
  await click('创建', '创建团队');
  const lateTeam = await delayedTeam.createdTeam();
  await switchAccountInPlace('b@audit.example');
  await until(() => js(`document.querySelector('.user-email')?.innerText === 'b@audit.example'`), '保持团队页切换为B');
  await delayedTeam.release();
  assert.equal(await js(`${authStore}.currentTeam.id`), secondTeam.id, 'A迟到响应不能把B切换到A的团队');
  assert.equal(requests.filter(r => r.url.includes(`/teams/${lateTeam.id}/workspaces`)).length, 0);

  const unmountedTeam = await delayTeamResponse();
  await click('创建团队');
  await fill('我的团队', '离开页面后的团队');
  await fill('my-team（小写字母和连字符）', 'audit-left-page');
  await click('创建', '创建团队');
  const leftTeam = await unmountedTeam.createdTeam();
  await route('/library');
  await until(() => js(`!document.querySelector('.team-page')`), '团队页已卸载');
  await unmountedTeam.release();
  assert.equal(requests.filter(r => r.url.includes(`/teams/${leftTeam.id}/workspaces`)).length, 0, '页面卸载后不能继续切换团队');
  assert.equal(await js('location.hash'), '#/library');

  await switchAccountInPlace('c@audit.example');
  await route('/onboarding');
  await until(() => js(`!!document.querySelector('.onboarding-page')`), 'C进入未加入团队引导');
  const delayedOnboarding = await delayTeamResponse();
  await click('创建团队和工作空间');
  await fill('如：产品研发部', 'C迟到团队');
  await fill('如：product-dev', 'audit-late-c');
  await fill('如：默认工作空间', '不应继续创建的空间');
  await click('一键创建');
  const lateOnboardingTeam = await delayedOnboarding.createdTeam();
  await switchAccountInPlace('b@audit.example');
  await delayedOnboarding.release();
  assert.equal(requests.filter(r => r.method === 'POST' && r.url.includes(`/teams/${lateOnboardingTeam.id}/workspaces`)).length, 0, '新账号不能续做旧账号的引导创建');
  assert.equal(await js(`!!document.querySelector('.choose-panel')`), true, '账号切换后清空部分创建状态');
  await route('/team');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('b@audit.example'))`), '回到团队页');

  await switchAccountInPlace('a@audit.example');
  const lateSelection = await delayTeamResponse(`/api/sync/teams/${firstTeam.id}/workspaces`, 'GET');
  await js(`void (window.__auditSelection = ${authStore}.switchTeam(${JSON.stringify(firstTeam)}))`);
  await lateSelection.createdTeam();
  await switchAccountInPlace('b@audit.example');
  const selectedByB = await js(`${authStore}.currentWorkspace.id`);
  await lateSelection.release();
  await js('window.__auditSelection');
  assert.equal(await js(`${authStore}.currentWorkspace.id`), selectedByB, '旧团队列表响应不能改写B的空间');
  assert.equal((await request('/api/sync/status')).workspace_id, selectedByB, '旧选择不能向后端绑定A的空间');

  const lateRestore = await delayTeamResponse('/api/sync/status', 'GET');
  await js(`void (window.__auditRestore = ${authStore}.restoreSession())`);
  await lateRestore.createdTeam();
  await switchAccountInPlace('a@audit.example');
  await lateRestore.release();
  await js('window.__auditRestore');
  assert.equal(await js(`${authStore}.user.email`), 'a@audit.example', '旧恢复会话响应不能恢复B的账号');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('a@audit.example'))`), '最终A成员表');
  await route('/onboarding');
  await until(() => js(`!!document.querySelector('.choose-panel')`), '引导页刷新入口');
  await request('/api/sync/logout', { method: 'POST' });
  await click('刷新加入状态');
  await until(() => js(`location.hash === '#/login'`), '真实会话失效后引导重新登录');
  assert.ok(await js(`document.body.innerText.includes('登录状态已失效，请重新登录')`));
  await login('a@audit.example');
  await route('/team');
  await until(() => js(`${memberRows}.some(r => r.innerText.includes('a@audit.example'))`), '重新登录后恢复团队');

  assert.equal(await js(`localStorage.getItem('cloud_access_token')`), null);
  assert.equal(await js(`localStorage.getItem('cloud_refresh_token')`), null);
  assert.equal(requests.filter(r => r.url.startsWith(cloudUrl)).length, 0, '桌面渲染器不得直连云服务');
  assert.equal(requests.filter(r => /join-requests|teams\/discover/.test(r.url)).length, 0);
  await until(() => js(`!document.querySelector('.el-message')`), '提示消退后截图');
  fs.writeFileSync(path.join(root, 'team.png'), (await window.capturePage()).toPNG());
  fs.writeFileSync(path.join(root, 'result.json'), JSON.stringify({ status: 'passed', checks: ['登录经本地代理', '无团队引导不调用停用接口', '引导创建团队和空间', '成员列表不重复请求', '团队页创建并选中团队', '创建并绑定空间', '邀请/移除已有账号', '普通成员邀请和创建空间被拒绝', '团队创建迟到响应不污染新账号', '卸载页面后迟到响应不继续切换', '引导创建迟到响应不跨账号继续创建空间', '旧团队列表响应不能绑定新账号空间', '旧会话恢复响应不能覆盖新登录', '真实会话失效后引导重新登录', '渲染器不保存令牌/不直连云端'] }, null, 2));
  console.log(`Team UI PASS: 团队与工作空间完整代理流程、权限拒绝、无停用接口请求。证据: ${root}`);
}
async function cleanup(code) {
  if (window && !window.isDestroyed()) window.destroy();
  for (const child of processes) {
    if (child.exitCode === null && !child.signalCode) {
      child.kill('SIGTERM');
      await Promise.race([new Promise(resolve => child.once('exit', resolve)), pause(3000)]);
      if (child.exitCode === null && !child.signalCode) child.kill('SIGKILL');
    }
  }
  if (server?.listening) await new Promise(resolve => server.close(resolve));
  app.exit(code);
}
app.whenReady().then(run).then(() => cleanup(0), async error => {
  console.error(error);
  console.error(`验证日志：${root}`);
  if (window && !window.isDestroyed() && window.webContents.getURL()) {
    fs.writeFileSync(path.join(root, 'failure.png'), (await window.capturePage()).toPNG());
    fs.writeFileSync(path.join(root, 'failure.txt'), await window.webContents.executeJavaScript('document.body.innerText'));
  }
  await cleanup(1);
});
