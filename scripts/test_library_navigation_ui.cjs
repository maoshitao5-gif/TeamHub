// 资料分类与收藏导航验收；仅访问临时库、独立 profile 和自有后端。
// 先 npm run frontend:build，再 env -u ELECTRON_RUN_AS_NODE node_modules/.bin/electron scripts/test_library_navigation_ui.cjs。
const { app, BrowserWindow } = require('electron');
const { spawn } = require('node:child_process');
const { randomUUID } = require('node:crypto');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const net = require('node:net');
const assert = require('node:assert/strict');

const repo = path.resolve(__dirname, '..');
const root = fs.realpathSync(fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-navigation-ui-')));
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
  const pickerFile = path.join(root, '选择收录样本.txt');
  const pickerFolder = path.join(root, '选择收录文件夹');
  fs.writeFileSync(pickerFile, '路径选择收录测试');
  fs.mkdirSync(pickerFolder);
  fs.writeFileSync(path.join(pickerFolder, '内部资料.txt'), '整体文件夹测试');
  const preload = path.join(root, 'preload.cjs');
  fs.writeFileSync(preload, `let fileSelections = 0; require('electron').contextBridge.exposeInMainWorld('electron', { apiBaseURL: ${JSON.stringify(api)}, aiCapability: ${JSON.stringify(capability)}, selectFile: async () => ++fileSelections === 1 ? null : ${JSON.stringify(pickerFile)}, selectDirectory: async () => ${JSON.stringify(pickerFolder)} });`);
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

  await window.loadFile(path.join(repo, 'frontend/dist/index.html'), { hash: '/library' });
  await runtimeReady;
  await click('打开文件库');
  const listNames = () => js(`[...document.querySelectorAll('.doc-card .doc-name')].map(e => e.textContent)`);
  await until(async () => (await listNames()).includes('正式资料.txt'), '全部资料');
  assert.ok(!(await listNames()).includes('待整理样本.txt'));
  assert.ok(!(await js('document.body.innerText')).includes('资料库顶层'));
  async function nav(label) {
    const target = `[...document.querySelectorAll('.special-item')].find(e => e.textContent.trim() === ${JSON.stringify(label)})`;
    assert.ok(await js(`(${target}).getClientRects().length > 0`), '导航必须实际可见');
    await js(`(${target}).click()`);
  }
  async function menu(label) {
    await js(`document.querySelector('.doc-card .el-dropdown button').click()`);
    const item = `[...document.querySelectorAll('.el-dropdown-menu__item')].find(e => e.getClientRects().length && e.textContent.trim() === ${JSON.stringify(label)})`;
    await until(() => js(`Boolean(${item})`), label);
    await js(`(${item}).click()`);
  }
  assert.equal(await js(`document.querySelector('.scope-hint').textContent`), '所有已整理的资料都在这里');
  await js(`document.querySelector('button[aria-label="新建文件夹"]').click()`);
  await until(() => js(`Boolean(document.querySelector('.el-message-box input'))`), '侧栏新建文件夹');
  await js(`(() => { const input = document.querySelector('.el-message-box input'); input.value = '设备说明书'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('创建', `document.querySelector('.el-message-box')`);
  await until(() => js(`[...document.querySelectorAll('.node-label')].some(e => e.textContent === '设备说明书')`), '空文件夹可见');
  await until(() => js(`document.querySelector('.page-title').textContent === '设备说明书'`), '新建文件夹自动进入');
  await nav('全部资料');
  await until(async () => (await listNames()).includes('正式资料.txt'), '返回全部资料');
  await menu('收藏');
  await until(async () => (await request('/api/favorites?target_type=local')).length === 1, '收藏建立');
  await menu('归入文件夹');
  await until(() => js(`Boolean(${dialog})`), '归类弹窗');
  const addFolder = `(${dialog}).querySelector('button[title="在此目录下新建文件夹"]')`;
  await until(() => js(`Boolean(${addFolder})`), '新建分类入口');
  await js(`(${addFolder}).click()`);
  await until(() => js(`Boolean((${dialog}).querySelector('input[placeholder="文件夹名称"]'))`), '分类名称');
  await js(`(() => { const input = (${dialog}).querySelector('input[placeholder="文件夹名称"]'); input.value = '项目资料'; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
  await click('确定', dialog);
  await until(() => js(`(${dialog}).querySelector('.selected-hint').textContent.includes('项目资料')`), '新建目录选中');
  await pause(250);
  fs.writeFileSync(path.join(root, 'classify.png'), (await window.capturePage()).toPNG());
  await click('确认归入', dialog);
  await until(() => js(`!(${dialog})`), '归类完成');
  const classified = await request('/api/documents/' + organized.id);
  assert.equal(classified.storage_path, '项目资料/正式资料.txt');
  assert.equal(classified.status, 'organized');
  assert.equal((await request('/api/favorites?target_type=local')).length, 1);
  assert.ok(fs.existsSync(path.join(libraryPath, '项目资料', '正式资料.txt')));
  assert.ok(!fs.existsSync(path.join(libraryPath, '正式资料.txt')));
  await nav('我的收藏');
  await until(async () => (await listNames()).length === 1, '收藏列表');
  await until(() => js(`document.querySelector('.page-title').textContent === '我的收藏'`), '收藏标题');
  await pause(250);
  fs.writeFileSync(path.join(root, 'favorites.png'), (await window.capturePage()).toPNG());
  await js(`[...document.querySelectorAll('.node-label')].find(e => e.textContent.trim() === '项目资料').click()`);
  await until(() => js(`document.querySelector('.page-title').textContent === '项目资料'`), '分类导航');
  await menu('移出文件夹');
  await click('移出', `document.querySelector('.el-message-box')`);
  await until(async () => (await request('/api/documents/' + organized.id)).storage_path === '正式资料.txt', '移出分类');
  await until(async () => (await listNames()).length === 0, '分类结果刷新');
  await nav('我的收藏');
  await until(async () => (await listNames()).includes('正式资料.txt'), '移出后仍收藏');
  await menu('取消收藏');
  await until(async () => (await listNames()).length === 0, '取消收藏刷新');
  await nav('全部资料');
  await until(async () => (await listNames()).includes('正式资料.txt'), '取消后仍在全部资料');
  const doc = await request('/api/documents/' + organized.id);
  assert.equal(doc.status, 'organized');
  assert.equal(doc.current_version.version_number, 1);
  assert.deepEqual(doc.tags.map(t => t.name).sort(), ['主变', '检修'].sort());
  assert.ok(fs.existsSync(path.join(libraryPath, '正式资料.txt')));
  await pause(250);
  fs.writeFileSync(path.join(root, 'all-documents.png'), (await window.capturePage()).toPNG());
  // 原生选择框由 bridge 替身返回临时路径；收录和移动使用真实后端。
  await js(`window.location.hash = '/pending'`);
  await click('收录本机文件');
  await until(() => js(`Boolean(${dialog})`), '路径选择弹窗');
  assert.ok(await js(`(${dialog}).querySelector('input').readOnly`));
  await click('选择文件', dialog);
  assert.equal(await js(`(${dialog}).querySelector('input').value`), '');
  assert.ok(fs.existsSync(pickerFile), '取消选择不移动源文件');
  await click('选择文件', dialog);
  await until(() => js(`(${dialog}).querySelector('input').value === ${JSON.stringify(pickerFile)}`), '选择结果展示');
  assert.ok(fs.existsSync(pickerFile), '确认前不移动文件');
  await pause(3500);
  fs.writeFileSync(path.join(root, 'import-picker.png'), (await window.capturePage()).toPNG());
  await click('移动到待整理', dialog);
  await until(() => !fs.existsSync(pickerFile), '选择文件后收录');
  await until(() => js(`Boolean(document.querySelector('.el-drawer__close-btn'))`), '收录后详情');
  await js(`document.querySelector('.el-drawer__close-btn').click()`);
  await click('收录本机文件');
  await until(() => js(`Boolean(${dialog})`), '再次收录');
  assert.equal(await js(`(${dialog}).querySelector('input').value`), '', '再次打开清除上次选择');
  await click('选择文件夹', dialog);
  await until(() => js(`(${dialog}).querySelector('input').value === ${JSON.stringify(pickerFolder)}`), '文件夹选择结果');
  await click('移动到待整理', dialog);
  await until(() => !fs.existsSync(pickerFolder), '整体文件夹移动');
  assert.ok(fs.existsSync(path.join(libraryPath, '待整理', '选择收录文件夹', '内部资料.txt')));
  await until(() => js(`Boolean(document.querySelector('.el-drawer__close-btn'))`), '文件夹详情');
  await js(`document.querySelector('.el-drawer__close-btn').click()`);
  for (let i = 0; i < 16; i++) {
    const source = path.join(root, `分页样本${i}.txt`);
    fs.writeFileSync(source, `用于搜索分页验收 ${i}`);
    await request('/api/documents/quick-add', { source_path: source });
  }
  await js(`window.location.hash = '/library'`);
  await until(() => js(`Boolean(document.querySelector('.library-page'))`), '离开待整理');
  await js(`window.location.hash = '/pending'`);
  const pendingNames = () => js(`[...document.querySelectorAll('.pending-card .doc-name')].map(e => e.textContent)`);
  await until(async () => (await pendingNames()).length === 15, '待整理分页');
  assert.ok(!(await pendingNames()).includes('待整理样本.txt'), '目标位于首页之外');
  await js(`[...document.querySelectorAll('.el-pager li')].find(e => e.textContent.trim() === '2').click()`);
  await until(async () => (await pendingNames()).includes('待整理样本.txt'), '第二页');
  await js(`document.querySelector('.pending-card .el-checkbox input').click()`);
  async function searchPending(keyword) {
    await js(`(() => { const input = document.querySelector('input[aria-label="搜索待整理资料"]'); input.value = ${JSON.stringify(keyword)}; input.dispatchEvent(new Event('input', { bubbles: true })); })()`);
    await click('搜索', `document.querySelector('.pending-search')`);
  }
  await searchPending('待整理样本');
  await until(async () => (await pendingNames()).length === 1 && (await pendingNames())[0] === '待整理样本.txt', '跨页名称搜索');
  assert.ok(!(await js('document.body.innerText')).includes('已选 1 项'), '搜索后清除批量选择');
  await searchPending('选择收录文件夹');
  await until(async () => (await pendingNames()).length === 1 && (await pendingNames())[0] === '选择收录文件夹', '文件夹名称搜索');
  await searchPending('不存在的资料xyz');
  await until(() => js(`document.body.innerText.includes('没有符合当前条件的待整理资料')`), '无结果提示');
  await click('清除搜索', `document.querySelector('.pending-search-summary')`);
  await until(async () => (await pendingNames()).length === 15, '清除搜索恢复首页');
  await click('智能扫描');
  await until(() => js(`Boolean(document.querySelector('.analysis-result'))`), '智能扫描结果');
  await click('全选本页');
  assert.equal(await js(`[...document.querySelectorAll('button')].some(e => e.getClientRects().length && e.textContent.includes('合并为版本'))`), false, '不展示未实现的合并入口');
  await click('取消全选');
  await pause(400);
  fs.writeFileSync(path.join(root, 'pending-search.png'), (await window.capturePage()).toPNG());
  const unusedTag = await request('/api/tags', { name: '未使用标签验收' });
  await request(`/api/documents/${pending.id}/tags`, ['仅待整理标签'], 'PUT');
  await js(`window.location.hash = '/tags'`);
  const tagRow = name => `[...document.querySelectorAll('tr.el-table__row')].find(e => e.textContent.includes(${JSON.stringify(name)}))`;
  await until(() => js(`Boolean(${tagRow('主变')})`), '标签管理列表');
  assert.equal(await js(`(${tagRow('仅待整理标签')}).querySelector('.file-count-text').textContent.trim()`), '1');
  assert.ok(await js(`(${tagRow('仅待整理标签')}).querySelector('.usage-breakdown').textContent.includes('待整理 1')`));
  assert.ok(await js(`(${tagRow('未使用标签验收')}).textContent.includes('未使用，可删除')`));
  const usedDelete = `[...(${tagRow('主变')}).querySelectorAll('button')].find(b => b.textContent.trim() === '删除')`;
  assert.ok(await js(`(${usedDelete}).disabled`), '已使用标签不能点击删除');
  await js(`(${tagRow('主变')}).querySelector('input[type="checkbox"]').click()`);
  const batchDelete = `[...document.querySelectorAll('button')].find(b => b.textContent.includes('批量删除'))`;
  await js(`(${batchDelete}).click()`);
  await until(() => js(`document.body.innerText.includes('选中的标签包含已使用标签')`), '批量删除保护提示');
  assert.ok((await request('/api/tags')).some(t => t.name === '主变'));
  const unusedDelete = `[...(${tagRow('未使用标签验收')}).querySelectorAll('button')].find(b => b.textContent.trim() === '删除')`;
  await js(`(${unusedDelete}).click()`);
  await click('确定', `document.querySelector('.el-message-box')`);
  await until(async () => !(await request('/api/tags')).some(t => t.id === unusedTag.id), '未使用标签删除');
  const originalVersion = (await request(`/api/documents/${organized.id}/versions`))[0];
  const restoreWorking = path.join(libraryPath, '正式资料.txt');
  fs.writeFileSync(restoreWorking, '第二版已保存内容');
  await request(`/api/documents/${organized.id}/versions`, {});
  fs.writeFileSync(restoreWorking, '恢复前尚未保存的修改');
  await js(`window.location.hash = '/library'`);
  await until(() => js(`Boolean(document.querySelector('.doc-card'))`), '版本恢复资料入口');
  await js(`document.querySelector('.doc-card').click()`);
  await until(() => js(`[...document.querySelectorAll('button')].some(b => b.textContent.trim() === '恢复到此版本')`), '恢复历史版本入口');
  await click('恢复到此版本', `document.querySelector('.el-drawer')`);
  await until(() => fs.readFileSync(restoreWorking, 'utf8') === '版本内容保持不变', '点击恢复直接替换工作内容');
  await until(async () => (await request(`/api/documents/${organized.id}`)).current_version.id === originalVersion.id, '当前版本切换到初版');
  const restoredVersions = await request(`/api/documents/${organized.id}/versions`);
  assert.equal(restoredVersions.length, 3);
  assert.ok(restoredVersions.some(v => v.note?.includes('自动保存')));
  await until(() => js(`document.body.innerText.includes('与当前已保存版本一致')`), '恢复后状态一致');
  const versionRow = number => `[...document.querySelectorAll('.version-item')].find(e => e.querySelector('.version-number').textContent === ${JSON.stringify('v')} + ${number})`;
  assert.ok(await js(`![...(${versionRow(1)}).querySelectorAll('button')].some(b => b.textContent.trim() === '删除版本')`), '当前版本无删除入口');
  await click('删除版本', versionRow(2));
  await click('取消', `document.querySelector('.el-message-box')`);
  await until(() => js(`![...document.querySelectorAll('.el-message-box')].some(e => e.getClientRects().length)`), '取消确认框关闭');
  assert.equal((await request(`/api/documents/${organized.id}/versions`)).length, 3);
  await click('删除版本', versionRow(2));
  await click('删除版本', `document.querySelector('.el-message-box')`);
  await until(async () => (await request(`/api/documents/${organized.id}/versions`)).length === 2, '删除历史版本');
  await until(() => js(`!(${versionRow(2)})`), '历史列表刷新');
  assert.equal(fs.readFileSync(restoreWorking, 'utf8'), '版本内容保持不变');
  assert.deepEqual(runtimeErrors, []);
  console.log('NAVIGATION UI PASS: 新建分类并归入、收藏、分类移出、取消收藏、全部资料保持、版本标签保持、路径选择取消/文件/整体文件夹收录、待整理跨页搜索与清空、已使用标签删除保护、恢复版本直接生效与修改保护、历史版本删除及取消。证据：' + root);
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
