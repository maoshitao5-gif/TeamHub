// 实际 macOS .app 产物验收，无 Vite、开发 Electron 或源码后端。
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const http = require('node:http');
const { spawn } = require('node:child_process');
const assert = require('node:assert/strict');
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-m6-packaged-'));
const repo = path.resolve(__dirname, '..');
// 系统复验可指定独立构建产物，避免覆盖用户正在运行的应用。
const binary = process.env.TEAMHUB_TEST_APP || path.join(repo, 'dist-electron/mac-arm64/TeamHub.app/Contents/MacOS/TeamHub');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
async function until(fn, label, timeout = 40000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const result = await fn(); if (result) return result; await pause(150); }
  throw new Error('等待超时: ' + label);
}
let child, socket, blocker, backendURL, closeBrowser, ended = false;
async function main() {
  const library = path.join(root, 'library'); fs.mkdirSync(library);
  blocker = http.createServer((req, res) => { res.end('unrelated service'); });
  await new Promise(resolve => { blocker.once('error', resolve); blocker.listen(8001, '127.0.0.1', resolve); });
  const probe = http.createServer();
  await new Promise(resolve => probe.listen(0, '127.0.0.1', resolve));
  const debugPort = probe.address().port;
  await new Promise(resolve => probe.close(resolve));
  const log = fs.openSync(path.join(root, 'electron.log'), 'w');
  const env = { ...process.env, TEAMHUB_SANDBOX: '1', TEAMHUB_USER_DATA_DIR: path.join(root, 'profile'), LIBRARY_PATH: library, TRASH_AUTO_CLEAN_DAYS: '0' };
  delete env.ELECTRON_RUN_AS_NODE;
  child = spawn(binary, [`--remote-debugging-port=${debugPort}`], { env, cwd: root, stdio: ['ignore', log, log] });
  fs.closeSync(log);
  child.once('exit', () => { ended = true; });
  const target = await until(async () => {
    if (ended) throw new Error('打包应用提前退出，见 electron.log');
    try { return (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find(item => item.type === 'page' && item.url.startsWith('file:') && !item.url.includes('floating-window')); } catch { return null; }
  }, '实际 file:// 页面');
  socket = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.onopen = resolve; socket.onerror = reject; });
  let id = 0; const pending = new Map(), errors = [];
  socket.onmessage = event => {
    const result = JSON.parse(event.data);
    if (result.method === 'Runtime.exceptionThrown') errors.push(result.params.exceptionDetails.text);
    const handler = pending.get(result.id);
    if (handler) { pending.delete(result.id); result.error ? handler.reject(new Error(JSON.stringify(result.error))) : handler.resolve(result.result); }
  };
  const call = (method, params = {}, sessionId) => new Promise((resolve, reject) => { const key = ++id; pending.set(key, { resolve, reject }); socket.send(JSON.stringify({ id: key, method, params, sessionId })); });
  closeBrowser = () => call('Browser.close');
  const evaluate = async expression => {
    const result = await call('Runtime.evaluate', { expression, awaitPromise: true, returnByValue: true });
    if (result.exceptionDetails) throw new Error(JSON.stringify(result.exceptionDetails));
    return result.result.value;
  };
  await call('Runtime.enable');
  await until(() => evaluate("document.body?.innerText.includes('文档库') || document.body?.innerText.includes('打开文件库')"), 'Vue 页面加载');
  backendURL = await evaluate('window.electron.apiBaseURL');
  assert.notEqual(backendURL, 'http://127.0.0.1:8001');
  const health = await evaluate(`fetch(${JSON.stringify(backendURL + '/api/health')}).then(r => r.json())`);
  assert.equal(health.library_path, library);
  assert.ok(health.instance_id);
  const aiCapability = await evaluate('window.electron.aiCapability');
  assert.match(aiCapability, /^[a-f0-9]{64}$/);
  const untrustedAI = await fetch(backendURL + '/api/ai/settings', { headers: { 'X-TeamHub-AI': '1', Origin: 'null' } });
  assert.equal(untrustedAI.status, 403, '网页 null 来源不能冒用桌面 AI 会话');
  const trustedAI = await fetch(backendURL + '/api/ai/settings', { headers: {
    'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': aiCapability, Origin: 'null',
  } });
  assert.equal(trustedAI.status, 200, '正式 IPC、preload、后端环境必须传递同一启动凭据');
  assert.equal(JSON.stringify(health).includes(aiCapability), false);
  assert.equal((await trustedAI.text()).includes(aiCapability), false);
  // Only kill the wrapper recorded by this isolated application, never a port owner.
  // PyInstaller's surviving server child must be reaped before the same-port restart.
  const statePath = path.join(root, 'profile', 'desktop-state.json');
  const state = await until(() => {
    try { const value = JSON.parse(fs.readFileSync(statePath, 'utf8')); return value.status === 'ready' && value; }
    catch { return false; }
  }, '隔离应用的就绪记录');
  assert.equal(state.pid, child.pid);
  assert.equal(state.userData, path.join(root, 'profile'));
  assert.equal(path.resolve(state.executable), path.resolve(binary));
  assert(Number.isInteger(state.backendPid) && state.backendPid > 1 && state.backendPid !== child.pid);
  process.kill(state.backendPid, 'SIGKILL');
  const recovered = await until(async () => {
    try {
      const value = await (await fetch(backendURL + '/api/health', { signal: AbortSignal.timeout(1500) })).json();
      return value.instance_id && value.instance_id !== health.instance_id && value;
    } catch { return false; }
  }, '包装进程异常退出后的原端口自动恢复', 20000);
  assert.equal(recovered.library_path, library);
  assert.equal(await evaluate('window.electron.apiBaseURL'), backendURL);
  assert.equal(await evaluate('window.electron.aiCapability'), aiCapability);
  assert.equal((await fetch(backendURL + '/api/ai/settings', { headers: {
    'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': aiCapability, Origin: 'null',
  } })).status, 200);
  fs.writeFileSync(path.join(root, 'backend-recovery.json'), JSON.stringify({
    beforeInstance: health.instance_id, afterInstance: recovered.instance_id,
    backendURL, wrapperPid: state.backendPid, recovered: true,
  }, null, 2));
  const csp = await evaluate("document.querySelector('meta[http-equiv=\"Content-Security-Policy\"]').content");
  assert.ok(csp.includes("script-src 'self';") && !csp.includes('unsafe-eval'));
  const source = path.join(root, '打包产物验收.txt'); fs.writeFileSync(source, '打包后端首版内容');
  const post = async (route, body) => {
    const response = await fetch(backendURL + route, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
    const result = await response.json(); assert.equal(response.status, 200, JSON.stringify(result)); return result;
  };
  const doc = await post('/api/documents', { source_path: source, name: '打包产物验收.txt', tags: ['M6验收'] });
  fs.writeFileSync(path.join(library, doc.storage_path), '打包后端第二版内容');
  await post(`/api/documents/${doc.id}/versions`, { note: '真实打包进程保存 v2' });
  await call('Page.reload');
  await until(() => evaluate("document.body?.innerText.includes('打开文件库') || document.body?.innerText.includes('打包产物验收.txt')"), '刷新持久化');
  await evaluate("[...document.querySelectorAll('button')].find(e => e.textContent.trim() === '打开文件库')?.click()");
  await evaluate("location.hash = '#/library'");
  await until(() => evaluate("document.body?.innerText.includes('打包产物验收.txt')"), '产物页面真实数据');
  const versions = await (await fetch(backendURL + `/api/documents/${doc.id}/versions`)).json();
  assert.equal(versions.length, 2);
  assert.equal(versions.filter(item => item.is_current).length, 1);
  // CDP 提供真实文件路径形成原生 File，经正式 preload/webUtils 和拖入处理链收录。
  async function drop(file, sessionId) {
    const params = { x: 150, y: 180, data: { items: [], files: [file], dragOperationsMask: 1 } };
    if (sessionId) { params.x = 30; params.y = 30; }
    for (const type of ['dragEnter', 'dragOver', 'drop']) await call('Input.dispatchDragEvent', { ...params, type }, sessionId);
  }
  async function moved(name, content) {
    const found = await until(async () => {
      const response = await post('/api/documents/search', { status: 'pending' });
      return response.documents.find(item => item.name === name);
    }, `拖入收录 ${name}`);
    assert.equal(fs.existsSync(path.join(root, name)), false, '源文件应移动');
    assert.equal(fs.readFileSync(path.join(library, found.storage_path), 'utf8'), content);
  }
  const dragged = path.join(root, '主窗口拖入.txt'); fs.writeFileSync(dragged, '主窗口原生拖入');
  await drop(dragged);
  await moved('主窗口拖入.txt', '主窗口原生拖入');
  const folder = path.join(root, '拖入整套资料');
  fs.mkdirSync(path.join(folder, '空目录'), { recursive: true });
  fs.writeFileSync(path.join(folder, '图纸.txt'), '整套资料原生拖入');
  await drop(folder);
  await until(() => evaluate(`[...document.querySelectorAll('.el-message-box button')].some(e => e.getClientRects().length && e.textContent.trim() === '整套收录')`), '文件夹整理方式');
  await evaluate(`[...document.querySelectorAll('.el-message-box button')].find(e => e.getClientRects().length && e.textContent.trim() === '整套收录').click()`);
  const folderDoc = await until(async () => (await post('/api/documents/search', { status: 'pending' })).documents.find(item => item.name === '拖入整套资料'), '整套文件夹拖入');
  assert.equal(folderDoc.is_folder, true);
  assert.equal(fs.existsSync(folder), false);
  assert.equal(fs.readFileSync(path.join(library, folderDoc.storage_path, '图纸.txt'), 'utf8'), '整套资料原生拖入');
  assert.ok(fs.statSync(path.join(library, folderDoc.storage_path, '空目录')).isDirectory());
  await evaluate('window.electron.toggleFloatingWindow(true)');
  const floating = await until(async () => (await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).find(item => item.url.includes('floating-window.html')), '悬浮窗创建');
  const attached = await call('Target.attachToTarget', { targetId: floating.id, flatten: true });
  await call('Runtime.enable', {}, attached.sessionId);
  await until(async () => (await call('Runtime.evaluate', { expression: "Boolean(document.querySelector('#floatingBtn'))", returnByValue: true }, attached.sessionId)).result.value, '悬浮窗加载');
  const floatingFile = path.join(root, '悬浮窗拖入.txt'); fs.writeFileSync(floatingFile, '悬浮窗原生拖入');
  await drop(floatingFile, attached.sessionId);
  await moved('悬浮窗拖入.txt', '悬浮窗原生拖入');
  // stat 真实失败时首项应给出错误提示，悬浮窗同批其余路径仍可收录。
  const inaccessible = path.join(root, '无法读取的链接');
  fs.symlinkSync(inaccessible, inaccessible);
  const nextFile = path.join(root, '失败后继续收录.txt');
  fs.writeFileSync(nextFile, '同批路径分别处理');
  await evaluate(`window.electron.dropFilesToFloatingWindow(${JSON.stringify([inaccessible, nextFile])})`);
  await until(() => evaluate("document.body.innerText.includes('处理拖入文件失败')"), '路径读取失败提示');
  await moved('失败后继续收录.txt', '同批路径分别处理');
  assert.ok(fs.lstatSync(inaccessible).isSymbolicLink(), '错误路径不得删除');
  await evaluate('window.electron.toggleFloatingWindow(false)');
  await until(async () => !(await (await fetch(`http://127.0.0.1:${debugPort}/json/list`)).json()).some(item => item.id === floating.id), '悬浮窗关闭');
  await pause(600);
  const screenshot = await call('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(path.join(root, 'packaged.png'), Buffer.from(screenshot.data, 'base64'));
  assert.deepEqual(errors, []);
  // CDP 正常关闭应用，验证 before-quit 确实回收其独立后端。
  call('Browser.close').catch(() => {}); // Chromium 可能先断开调试连接，不等待响应包。
  await until(() => ended, '应用正常退出', 15000);
  await until(async () => { try { await fetch(backendURL + '/api/health'); return false; } catch { return true; } }, '子后端退出', 10000);
  console.log('M6 PACKAGED PASS: file://、CSP、动态端口、包装进程崩溃后的原端口恢复及稳定AI凭据、真实打包后端收录/版本、刷新、主窗口文件/整体文件夹及悬浮窗原生File拖入移动、悬浮窗开关、正常退出。证据：' + root);
}
main().catch(error => { console.error(error); console.error('证据：' + root); process.exitCode = 1; }).finally(async () => {
  if (closeBrowser && !ended) await Promise.race([closeBrowser().catch(() => {}), pause(1500)]);
  if (socket) socket.close();
  if (child && !ended) { child.kill('SIGTERM'); await until(() => ended, '失败清理', 10000).catch(() => child.kill('SIGKILL')); }
  if (blocker?.listening) await new Promise(resolve => blocker.close(resolve));
});
