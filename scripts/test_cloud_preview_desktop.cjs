// 由 test_cloud_preview_ui.cjs 调用；仅临时个人库/profile与该父进程的临时云端。
const { app, BrowserWindow } = require('electron')
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const net = require('node:net')
const { spawn } = require('node:child_process')
assert(process.env.TEAMHUB_PREVIEW_FIXTURE, '请用 test_cloud_preview_ui.cjs 启动隔离验收')
const fixture = JSON.parse(fs.readFileSync(process.env.TEAMHUB_PREVIEW_FIXTURE, 'utf8'))
const { temp, origin, seed, documents } = fixture
const repo = path.resolve(__dirname, '..'), personal = path.join(temp, 'personal')
const capability = require('node:crypto').randomBytes(32).toString('hex')
fs.mkdirSync(personal, { recursive: true })
app.setPath('userData', path.join(temp, 'electron-profile'))
app.on('window-all-closed', () => {})
let backend, window
const errors = [], requests = []
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { if (await check()) return; await delay(80) }
  throw new Error('等待超时：' + label)
}
const port = () => new Promise((resolve, reject) => {
  const listener = net.createServer().once('error', reject)
  listener.listen(0, '127.0.0.1', () => { const value = listener.address().port; listener.close(() => resolve(value)) })
})
async function main() {
  const api = `http://127.0.0.1:${await port()}`
  let binary = null
  if (process.env.TEAMHUB_PREVIEW_BACKEND_BINARY) {
    binary = fs.realpathSync(path.resolve(process.env.TEAMHUB_PREVIEW_BACKEND_BINARY))
    assert(binary.startsWith(path.join(repo, 'dist') + path.sep) && fs.statSync(binary).isFile(), '后端二进制必须来自此项目 dist 目录')
  }
  const log = fs.openSync(path.join(temp, 'personal.log'), 'w')
  backend = spawn(binary || path.join(repo, 'venv/bin/python'), binary ? [] : ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port],
    { cwd: personal, env: { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true', TEAMHUB_AI_CAPABILITY: capability, USER_DATA_DIR: path.join(personal, 'user-data'), PORT: new URL(api).port, LIBRARY_PATH: path.join(personal, 'library'), TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false' }, stdio: ['ignore', log, log] })
  console.log('个人后端运行入口：' + (binary || 'venv uvicorn'))
  fs.closeSync(log)
  await until(async () => {
    if (backend.exitCode !== null) throw new Error('临时个人后端退出：' + fs.readFileSync(path.join(temp, 'personal.log'), 'utf8'))
    try { return (await fetch(api + '/api/health')).ok } catch { return false }
  }, '个人后端启动')
  async function request(route, method = 'GET', body) {
    const response = await fetch(api + route, { method, headers: body ? { 'Content-Type': 'application/json' } : {}, body: body ? JSON.stringify(body) : undefined })
    assert.equal(response.status, 200, await response.clone().text()); return response.json()
  }
  assert.equal((await request('/api/settings/library')).path, path.join(personal, 'library'))
  await request('/api/settings/config', 'PUT', { cloud_api_url: origin })
  await request('/api/sync/login', 'POST', { email: seed.users.b.email, password: process.env.TEAMHUB_TEST_PASSWORD })
  await request('/api/sync/workspace', 'POST', { workspace_id: seed.workspace, workspace_name: '预览资料' })
  const snapshot = async () => ({ pending: await request('/api/documents/pending/count'), docs: await request('/api/documents/search', 'POST', {}) })
  const before = await snapshot()
  const preload = path.join(temp, 'preview-preload.cjs')
  fs.writeFileSync(preload, `require('electron').contextBridge.exposeInMainWorld('electron', {apiBaseURL:${JSON.stringify(api)},aiCapability:${JSON.stringify(capability)}});`)
  window = new BrowserWindow({ show: false, width: 1500, height: 1080, webPreferences: { preload, contextIsolation: true, nodeIntegration: false } })
  // 按当前生产 CSP 验证 file:// 与 PDF Worker；不为测试放宽安全策略。
  const source = fs.readFileSync(path.join(repo, 'electron/main.js'), 'utf8')
  const csp = source.match(/const csp = isDev[\s\S]*?: "([^"]+)";/)?.[1]
  assert(csp, '未找到生产 CSP')
  window.webContents.session.webRequest.onHeadersReceived((details, callback) => callback({ responseHeaders: { ...details.responseHeaders, 'Content-Security-Policy': [csp] } }))
  window.webContents.session.webRequest.onBeforeRequest((details, callback) => {
    requests.push(details.url)
    const url = new URL(details.url)
    const external = ['http:', 'https:'].includes(url.protocol) && url.origin !== api
    if (external) errors.push('不应对外访问：' + details.url)
    callback({ cancel: external })
  })
  window.webContents.on('console-message', (_event, ...args) => {
    const detail = args[0], level = typeof detail === 'object' ? detail.level : detail
    const message = typeof detail === 'object' ? detail.message : args[1]
    if (level === 3 || level === 'error') errors.push(message)
  })
  const js = expression => window.webContents.executeJavaScript(expression)
  const selector = testId => `[data-testid=${JSON.stringify(testId)}]`
  async function wait(testId, extra = '') { await until(() => js(`!!document.querySelector(${JSON.stringify(selector(testId))})${extra}`), testId) }
  async function click(testId) { await wait(testId); await js(`document.querySelector(${JSON.stringify(selector(testId))}).click()`) }
  async function open(key) { await click('preview-current-' + documents[key].id); await wait('cloud-file-preview') }
  async function close() {
    await click('preview-close')
    await until(() => js(`![...document.querySelectorAll('[data-testid="cloud-file-preview"]')].some(e=>e.getClientRects().length)`), '预览关闭')
  }
  async function capture(filename) {
    await js(`(async () => { await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))); const dialog=[...document.querySelectorAll('[data-testid="cloud-file-preview"]')].find(node=>node.getClientRects().length); const scope=[];for(let node=dialog;node&&node!==document.body;node=node.parentElement)scope.push(node); await Promise.all(scope.flatMap(node=>node.getAnimations()).filter(animation=>Number.isFinite(animation.effect?.getComputedTiming().iterations)).map(animation=>animation.finished.catch(()=>{}))); })()`)
    const footer = await js(`(() => { const button=document.querySelector('[data-testid="preview-download"]');const rect=button.getBoundingClientRect();return {top:rect.top,bottom:rect.bottom,height:rect.height,viewportHeight:innerHeight}; })()`)
    assert(footer.height > 0 && footer.top >= 0 && footer.bottom <= footer.viewportHeight, '小屏预览下载按钮应完整显示在视口内：' + JSON.stringify(footer))
    console.log(filename + ' footer geometry：' + JSON.stringify(footer))
    await fs.promises.writeFile(path.join(temp, filename), (await window.capturePage()).toPNG())
  }
  await window.loadFile(path.join(repo, 'frontend/dist/index.html'), { hash: '/shared' })
  await until(() => js(`!!document.querySelector('.shared-page') || [...document.querySelectorAll('button')].some(b=>b.textContent.trim()==='打开文件库')`), '个人工作台')
  await js(`const enter=[...document.querySelectorAll('button')].find(b=>b.textContent.trim()==='打开文件库'); if(enter)enter.click()`)
  await wait('preview-current-' + documents.text.id)
  await js(`(() => { window.__previewURLs=[]; const create=URL.createObjectURL.bind(URL),revoke=URL.revokeObjectURL.bind(URL); URL.createObjectURL=b=>{const url=create(b);window.__previewURLs.push(url);return url}; URL.revokeObjectURL=url=>{window.__previewURLs=window.__previewURLs.filter(v=>v!==url);revoke(url)}; })()`)
  await open('text'); await wait('preview-text')
  assert((await js(`document.querySelector('[data-testid="preview-text"]').textContent`)).includes('当前文本第二版'))
  assert.equal(await js('window.__previewUnsafe'), undefined)
  await close()
  await open('pdf')
  await until(() => js(`(() => { const c=document.querySelector('[data-testid="pdf-preview-canvas"]'); if(!c||!c.width)return false;const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return p.some((v,i)=>i%4===3&&v===255&&p[i-3]>200&&p[i-2]<50&&p[i-1]<50); })()`), 'file PDF 真正渲染')
  assert.equal((await js(`document.querySelector('[data-testid="pdf-page-status"]').textContent`)).trim(), '第 1 / 2 页')
  const first = await js(`document.querySelector('[data-testid="pdf-preview-canvas"]').toDataURL()`)
  await click('pdf-next')
  await until(() => js(`(() => { const c=document.querySelector('[data-testid="pdf-preview-canvas"]');if(c.toDataURL()===${JSON.stringify(first)})return false;const p=c.getContext('2d').getImageData(0,0,c.width,c.height).data;return p.some((v,i)=>i%4===3&&v===255&&p[i-3]<50&&p[i-2]<50&&p[i-1]>200) })()`), 'PDF 第二页绘制')
  await capture('desktop-pdf.png'); await close()
  await open('image')
  await until(() => js(`(() => { const i=document.querySelector('[data-testid="preview-image"]'); return i?.complete&&i.naturalWidth===2 })()`), '个人端图片')
  await close(); await until(() => js('window.__previewURLs.length===0'), 'Blob URL 回收')
  await open('document'); await wait('preview-document')
  assert((await js(`document.querySelector('[data-testid="preview-document"]').textContent`)).includes('设备检修说明正文'))
  assert((await js(`document.querySelector('[data-testid="preview-document"] table').textContent`)).includes('断路器'))
  await close()
  await open('spreadsheet'); await wait('preview-spreadsheet')
  await js(`const select=document.querySelector('[data-testid="preview-sheet-select"]');select.value='1';select.dispatchEvent(new Event('change',{bubbles:true}))`)
  await until(() => js(`document.querySelector('[data-testid="preview-spreadsheet"]').textContent.includes('检修计划第二张表')`), '个人端Excel换表')
  await capture('desktop-xlsx.png'); await close()
  await open('image')
  await until(() => js(`document.querySelector('[data-testid="preview-image"]')?.naturalWidth===2`), '撤权前图片可见')
  const signIn = await fetch(origin + '/api/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: seed.users.a.email, password: process.env.TEAMHUB_TEST_PASSWORD }) })
  assert.equal(signIn.status, 200)
  const token = (await signIn.json()).access_token
  const accessPath = `${origin}/api/workspaces/${seed.workspace}/documents/${documents.image.id}/access`
  const accessResponse = await fetch(accessPath, { headers: { Authorization: `Bearer ${token}` } })
  assert.equal(accessResponse.status, 200)
  const grants = await accessResponse.json()
  const removed = await fetch(accessPath, { method: 'PUT', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: 'restricted', base_access_revision: grants.access_revision, access: grants.access.filter(entry => entry.user_id !== seed.users.b.id) }) })
  assert.equal(removed.status, 200)
  await js(`window.dispatchEvent(new Event('focus'))`)
  await until(() => js(`!document.querySelector('[data-testid="preview-image"]') && document.body.innerText.includes('当前账号已无法访问') && window.__previewURLs.length===0`), '代理失权focus清正文与Blob')
  await close()
  const after = await snapshot()
  assert.deepEqual(after, before, '在线预览不应收录到个人库/待整理或改变资料数量')
  assert.deepEqual(errors, [], '生产 CSP 或 Electron 控制台错误')
  assert(requests.some(url => url.includes('/api/sharing/cloud-docs/') && url.includes('/preview')), '应通过个人后端代理预览')
  assert.equal(requests.filter(url => url.startsWith(origin)).length, 0, '渲染器不得直接连接云端')
  console.log('PASS Electron file:// production CSP、PDFcanvas、文本/图片/Word/Excel、失权focus清空、代理与个人库隔离')
}
app.whenReady().then(main).then(() => cleanup(0), async error => {
  console.error(error)
  if (window && !window.isDestroyed()) await fs.promises.writeFile(path.join(temp, 'desktop-failure.png'), (await window.capturePage()).toPNG()).catch(() => {})
  cleanup(1)
})
function cleanup(code) {
  if (window && !window.isDestroyed()) window.destroy()
  if (backend && backend.exitCode === null) backend.kill('SIGTERM')
  app.exit(code)
}
