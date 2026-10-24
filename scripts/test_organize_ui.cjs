// 标签主导整理的桌面 UI 验收；仅访问临时库、独立 profile 和自有后端。
// 先 npm run frontend:build，再 env -u ELECTRON_RUN_AS_NODE node_modules/.bin/electron scripts/test_organize_ui.cjs。
const { app, BrowserWindow } = require('electron');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-organize-ui-'));
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
  async function seed(name, tag) {
    const source = path.join(root, name);
    fs.writeFileSync(source, `资料内容：${name}`);
    const doc = await request('/api/documents/quick-add', { source_path: source, is_folder: false });
    await request(`/api/documents/${doc.id}/tags`, [tag], 'PUT');
    return doc;
  }
  const single = await seed('单项巡检记录.txt', '已有巡检标签');
  const inFolder = await seed('交付说明.txt', '已有交付标签');
  const batchA = await seed('继电保护说明.txt', '保护装置');
  const batchB = await seed('设备清单.txt', '设备台账');
  fs.mkdirSync(path.join(libraryPath, '改造工程交付'));

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
  const dialog = `[...document.querySelectorAll('.organize-dialog')].find(e => e.getClientRects().length)`;
  const card = name => `[...document.querySelectorAll('.pending-card')].find(e => e.querySelector('.doc-name').textContent === ${JSON.stringify(name)})`;
  async function click(text, scope = 'document') {
    const buttons = `[...(${scope})?.querySelectorAll('button') || []].filter(b => b.getClientRects().length && !b.disabled)`;
    await until(() => js(`${buttons}.some(b => b.textContent.trim() === ${JSON.stringify(text)})`), `按钮 ${text}`);
    await js(`${buttons}.find(b => b.textContent.trim() === ${JSON.stringify(text)}).click()`);
  }
  async function openOrganize(doc) {
    await click('手工整理', card(doc.name));
    await until(() => js(`Boolean(${dialog})`), '整理弹窗');
  }
  async function addTag(name) {
    await js(`(${dialog}).querySelector('.organize-tags input').focus()`);
    await window.webContents.insertText(name);
    await until(() => js(`[...document.querySelectorAll('.el-select-dropdown__item')].some(e => e.getClientRects().length && e.textContent.trim() === ${JSON.stringify(name)})`), `新标签选项 ${name}`);
    // 原生按键检查回车只增加标签，不意外提交整张表单。
    window.webContents.sendInputEvent({ type: 'keyDown', keyCode: 'Enter' });
    window.webContents.sendInputEvent({ type: 'keyUp', keyCode: 'Enter' });
    await until(() => js(`Boolean(${dialog}) && [...(${dialog}).querySelectorAll('.organize-tags .el-tag')].some(e => e.textContent.trim() === ${JSON.stringify(name)})`), `回车添加标签 ${name}`);
    const point = await js(`(() => { const r = (${dialog}).querySelector('.organize-summary').getBoundingClientRect(); return { x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2) }; })()`);
    window.webContents.sendInputEvent({ type: 'mouseDown', button: 'left', clickCount: 1, ...point });
    window.webContents.sendInputEvent({ type: 'mouseUp', button: 'left', clickCount: 1, ...point });
    await until(() => js(`![...document.querySelectorAll('.el-select-dropdown')].some(e => e.getClientRects().length)`), '点击摘要收起标签选项');
  }
  async function screenshot(name) {
    await pause(300);
    fs.writeFileSync(path.join(root, name), (await window.capturePage()).toPNG());
  }
  async function chooseFolder() {
    await click('选择文件夹', dialog);
    await until(() => js(`Boolean((${dialog}).querySelector('.location-picker .el-tree-node'))`), '展开可选目录');
    const nodes = `[...(${dialog}).querySelectorAll('.location-picker .node-label')]`;
    await until(() => js(`${nodes}.some(e => e.textContent.trim() === '改造工程交付')`), '目标文件夹');
    await js(`${nodes}.find(e => e.textContent.trim() === '改造工程交付').click()`);
    await until(() => js(`!(${dialog}).querySelector('.location-picker') && (${dialog}).querySelector('.location-path')?.textContent.includes('改造工程交付')`), '已选择并收起目录');
  }
  async function pendingCount(expected) {
    await until(async () => (await request('/api/documents/pending/count')).count === expected, `待整理剩余 ${expected}`);
    await until(() => js(`document.querySelectorAll('.pending-card').length === ${expected}`), '待整理列表刷新');
    await until(() => js(`!(${dialog})`), '前一整理弹窗完成关闭动画');
  }

  await window.loadFile(path.join(repo, 'frontend/dist/index.html'), { hash: '/pending' });
  await runtimeReady;
  await click('打开文件库');
  await pendingCount(4);
  await openOrganize(single);
  assert.equal(await js(`Boolean((${dialog}).querySelector('.location-picker'))`), false, '默认不展开目录树');
  assert.ok(await js(`(${dialog}).querySelector('.organize-tags').textContent.includes('已有巡检标签')`), '保留已存在的标签');
  await addTag('主变巡检');
  assert.equal((await request(`/api/documents/${single.id}`)).status, 'pending', '回车不能触发表单提交');
  await screenshot('organize-default.png');
  await click('选择文件夹', dialog);
  await until(() => js(`Boolean((${dialog}).querySelector('.location-picker .el-tree-node'))`), '可选目录展开');
  const footerInView = await js(`(() => { const r = (${dialog}).querySelector('.el-dialog__footer').getBoundingClientRect(); return r.top >= 0 && r.bottom <= window.innerHeight; })()`);
  assert.equal(footerInView, true, '展开目录时完成/取消操作仍须在视口内');
  await screenshot('organize-folder-expanded.png');
  await click('收起', dialog);
  await chooseFolder();
  await click('取消归入文件夹', dialog);
  assert.equal(await js(`Boolean((${dialog}).querySelector('.location-picker, .location-path'))`), false, '取消目录选择恢复默认');
  await click('完成整理', dialog);
  await pendingCount(3);
  const organized = await request(`/api/documents/${single.id}`);
  assert.equal(organized.status, 'organized');
  assert.equal(organized.storage_path, single.name, '未选目录应存于资料库根目录');
  assert.deepEqual(organized.tags.map(tag => tag.name).sort(), ['已有巡检标签', '主变巡检'].sort());
  assert.ok(fs.existsSync(path.join(libraryPath, single.name)));

  await openOrganize(inFolder);
  assert.equal(await js(`Boolean((${dialog}).querySelector('.location-picker'))`), false, '新弹窗重置目录展开状态');
  await chooseFolder();
  await click('完成整理', dialog);
  await pendingCount(2);
  assert.equal((await request(`/api/documents/${inFolder.id}`)).storage_path, `改造工程交付/${inFolder.name}`);

  for (const doc of [batchA, batchB]) await js(`(${card(doc.name)}).querySelector('.el-checkbox input').click()`);
  await click('批量整理');
  await until(() => js(`Boolean(${dialog})`), '批量整理弹窗');
  assert.equal(await js(`Boolean((${dialog}).querySelector('.location-picker'))`), false);
  await addTag('检修项目');
  await click('完成 2 项整理', dialog);
  await pendingCount(0);
  for (const [doc, originalTag] of [[batchA, '保护装置'], [batchB, '设备台账']]) {
    const result = await request(`/api/documents/${doc.id}`);
    assert.equal(result.status, 'organized');
    assert.equal(result.storage_path, doc.name);
    assert.deepEqual(result.tags.map(tag => tag.name).sort(), [originalTag, '检修项目'].sort(), '批量追加共同标签应保留个体标签');
  }

  // 从工作文件创建重复内容的新版本，走真实查重接口和关闭/取消动作。
  const duplicate = await seed('重复内容待确认.txt', '待核对');
  fs.copyFileSync(path.join(libraryPath, single.name), path.join(libraryPath, duplicate.storage_path));
  await request(`/api/documents/${duplicate.id}/versions`, { note: '验收：内容与既有资料相同' });
  assert.equal((await request(`/api/documents/${duplicate.id}/check-library-duplicate`)).is_duplicate, true);
  await window.loadFile(path.join(repo, 'frontend/dist/index.html'), { hash: '/pending', query: { phase: 'duplicate' } });
  await click('打开文件库');
  await pendingCount(1);
  await openOrganize(duplicate);
  const duplicateDialog = `[...document.querySelectorAll('.dup-dialog')].find(e => e.getClientRects().length)`;
  for (const action of ['close', 'cancel']) {
    await click('完成整理', dialog);
    await until(() => js(`Boolean(${duplicateDialog})`), '重复提示出现');
    if (action === 'close') await js(`(${duplicateDialog}).querySelector('.el-dialog__headerbtn').click()`);
    else await click('取消', duplicateDialog);
    await until(() => js(`!(${duplicateDialog}) && [...(${dialog}).querySelectorAll('button')].some(b => b.textContent.trim() === '完成整理' && !b.disabled && !b.classList.contains('is-loading'))`), '关闭重复提示后整理恢复可操作');
    assert.equal((await request(`/api/documents/${duplicate.id}`)).status, 'pending');
  }
  await click('取消', dialog);
  fs.writeFileSync(path.join(root, 'runtime-errors.json'), JSON.stringify(runtimeErrors, null, 2));
  assert.deepEqual(runtimeErrors, [], '不应有运行时异常或 console.error');
  console.log(`ORGANIZE UI PASS: 自由标签回车、已有标签预填、默认根目录、可选目录选择/取消、批量保留个体标签、重复提示关闭/取消恢复。截图和日志：${root}`);
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
