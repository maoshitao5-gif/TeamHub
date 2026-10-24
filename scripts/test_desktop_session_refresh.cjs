#!/usr/bin/env node
// 真云端 + 真个人服务 + 生产 file:// 页面。所有账号、令牌、资料和数据库仅位于临时目录。
// TEAMHUB_REFRESH_API_ONLY=1 可只跑真实 HTTP；TEAMHUB_REFRESH_BACKEND_BINARY 可指定本项目 dist 中的冻结后端。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, execFileSync } = require('node:child_process')
const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-session-refresh-'))
const cloudPython = path.join(repo, 'cloud_backend/venv/bin/python')
const personalPython = path.join(repo, 'venv/bin/python')
const password = crypto.randomBytes(24).toString('hex')
const cloudEnv = { ...process.env, TEAMHUB_SANDBOX: '1', DATABASE_URL: `sqlite:///${temp}/cloud.db`, STORAGE_DIR: path.join(temp, 'cloud-objects'), PYTHONPATH: path.join(repo, 'cloud_backend'), JWT_SECRET_KEY: crypto.randomBytes(40).toString('hex'), TEAMHUB_TEST_PASSWORD: password }
const library = path.join(temp, 'personal-library'), configFile = path.join(library, '.teamhub/config.json')
const results = [], rotations = [], browserErrors = [], externalRequests = [], children = []
const startedAt = Date.now()
let electron, page, origin, api
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await delay(60) }
  throw new Error(`等待超时：${label}`)
}
const port = () => new Promise((resolve, reject) => { const server = net.createServer().once('error', reject); server.listen(0, '127.0.0.1', () => { const value = server.address().port; server.close(() => resolve(value)) }) })
const digest = token => crypto.createHash('sha256').update(token).digest('hex').slice(0, 12)
function pass(name) { results.push(name); console.log('PASS ' + name) }
function py(source) { return execFileSync(cloudPython, ['-c', source], { cwd: temp, env: cloudEnv, encoding: 'utf8', timeout: 30000 }).trim().split('\n').at(-1) }
function config() { return JSON.parse(fs.readFileSync(configFile, 'utf8')) }
function expireAccess(expiredToken) {
  const before = config(), next = { ...before, cloud_access_token: expiredToken }
  // 只有该进程创建的测试库可被修改；不得把默认用户库当作过期注入目标。
  assert(configFile.startsWith(temp + path.sep)); fs.writeFileSync(configFile, JSON.stringify(next), { mode: 0o600 })
  return before
}
async function request(base, route, { method = 'GET', body, token, bytes } = {}) {
  const response = await fetch(base + route, { method, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...(body ? { 'Content-Type': 'application/json' } : bytes ? { 'Content-Type': 'application/octet-stream' } : {}) }, body: body ? JSON.stringify(body) : bytes })
  return response
}
async function json(base, route, options) {
  const response = await request(base, route, options)
  assert.equal(response.status, 200, `${route}: ${await response.clone().text()}`)
  return response.json()
}
async function start(command, args, env, label) {
  const logFile = path.join(temp, label + '.log'), fd = fs.openSync(logFile, 'w')
  const child = spawn(command, args, { cwd: temp, env, stdio: ['ignore', fd, fd] }); fs.closeSync(fd); children.push(child)
  const base = label === 'cloud' ? origin : api
  await until(async () => { if (child.exitCode !== null) throw new Error(`${label} exited: ${fs.readFileSync(logFile, 'utf8')}`); try { return (await fetch(base + '/api/health')).ok } catch { return false } }, label + ' service')
}
async function snapshotLocal() {
  return { docs: await json(api, '/api/documents/search', { method: 'POST', body: {} }), pending: await json(api, '/api/documents/pending/count') }
}
async function main() {
  origin = `http://127.0.0.1:${await port()}`; api = `http://127.0.0.1:${await port()}`
  const seed = JSON.parse(py(`
import json, os
from app.database import init_db, SessionLocal
from app.models import User, Team, TeamMember, Workspace
from app.core.security import hash_password, create_access_token
from datetime import timedelta
init_db()
with SessionLocal() as db:
    u=User(email='refresh-'+__import__('uuid').uuid4().hex+'@test.example',display_name='续期验收成员',hashed_password=hash_password(os.environ['TEAMHUB_TEST_PASSWORD']))
    db.add(u);db.flush();t=Team(name='续期验收组织',slug='refresh-'+__import__('uuid').uuid4().hex);db.add(t);db.flush()
    db.add(TeamMember(team_id=t.id,user_id=u.id,role='admin'));w=Workspace(team_id=t.id,name='续期验收资料库');db.add(w);db.commit()
    print(json.dumps({'user_id':u.id,'email':u.email,'workspace_id':w.id,'expired_access':create_access_token({'sub':u.id},timedelta(seconds=-120))}))
`))
  await start(cloudPython, ['-m', 'uvicorn', 'main:app', '--app-dir', path.join(repo, 'cloud_backend'), '--host', '127.0.0.1', '--port', new URL(origin).port], cloudEnv, 'cloud')
  let session = await json(origin, '/api/auth/login', { method: 'POST', body: { email: seed.email, password } })
  // 先直接检验真实服务契约：旧实现第一轮即因缺少新 refresh_token 失败，模拟响应无法掩盖。
  const seen = new Set([session.refresh_token])
  for (let round = 1; round <= 3; round++) {
    const oldRefresh = session.refresh_token
    session = await json(origin, '/api/auth/refresh', { method: 'POST', body: { refresh_token: oldRefresh } })
    assert.equal(typeof session.refresh_token, 'string', `真实云端第 ${round} 轮续期必须返回新的刷新令牌`)
    assert(session.refresh_token && !seen.has(session.refresh_token), '不能复用已撤销的刷新令牌'); seen.add(session.refresh_token)
    assert.equal((await request(origin, '/api/auth/refresh', { method: 'POST', body: { refresh_token: oldRefresh } })).status, 401, '旧刷新令牌不能重放')
    await json(origin, '/api/users/me', { token: session.access_token })
    rotations.push({ phase: 'cloud-http', round, refreshFingerprint: digest(session.refresh_token) })
  }
  pass('真实云端连续 3 轮原子轮换，旧令牌重放被拒绝')
  const content = Buffer.from('连续续期后仍可读取的工程资料正文。\n')
  const base = `/api/workspaces/${seed.workspace_id}`
  const uploaded = await json(origin, `${base}/uploads?filename=${encodeURIComponent('续期验收共享资料.txt')}&purpose=publication`, { method: 'POST', token: session.access_token, bytes: content })
  const published = await json(origin, `${base}/publications`, { method: 'POST', token: session.access_token, body: { request_id: crypto.randomUUID(), name: '续期验收共享资料.txt', original_filename: '续期验收共享资料.txt', doc_type: 'file', visibility: 'workspace', confirmed: true, upload_id: uploaded.upload_id, content_hash: uploaded.sha256 } })
  assert.equal(published.status, 'succeeded')
  const cloudBefore = await json(origin, `${base}/documents/${published.document_id}`, { token: session.access_token })
  let binary
  if (process.env.TEAMHUB_REFRESH_BACKEND_BINARY) { binary = fs.realpathSync(path.resolve(process.env.TEAMHUB_REFRESH_BACKEND_BINARY)); assert(binary.startsWith(path.join(repo, 'dist') + path.sep), '冻结后端必须来自本项目 dist') }
  const capability = crypto.randomBytes(32).toString('hex')
  const personalEnv = { PATH: process.env.PATH, HOME: process.env.HOME, TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true', TEAMHUB_AI_CAPABILITY: capability, USER_DATA_DIR: path.join(temp, 'personal-user-data'), LIBRARY_PATH: library, PYTHONPATH: repo, PORT: new URL(api).port, TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false' }
  await start(binary || personalPython, binary ? [] : ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port], personalEnv, 'personal')
  assert.equal((await json(api, '/api/settings/library')).path, library)
  await json(api, '/api/settings/config', { method: 'PUT', body: { cloud_api_url: origin } })
  await json(api, '/api/sync/login', { method: 'POST', body: { email: seed.email, password } })
  await json(api, '/api/sync/workspace', { method: 'POST', body: { workspace_id: seed.workspace_id, workspace_name: '续期验收资料库' } })
  const localSource = path.join(temp, '续期验收个人资料.txt'); fs.writeFileSync(localSource, '个人资料始终独立可读。')
  const localDoc = await json(api, '/api/documents', { method: 'POST', body: { source_path: localSource, name: '续期验收个人资料.txt', tags: ['续期验收'] } })
  const localBefore = await snapshotLocal()
  const searchRoute = `/api/sync/docs/search?workspace_id=${seed.workspace_id}&q=${encodeURIComponent('续期验收')}`
  for (let round = 1; round <= 3; round++) {
    const before = expireAccess(seed.expired_access)
    const found = await json(api, searchRoute)
    assert.equal(found.total, 1); assert.equal(found.items[0].id, published.document_id)
    const after = config()
    assert.notEqual(after.cloud_refresh_token, before.cloud_refresh_token, `个人代理第 ${round} 轮必须保存新刷新令牌`)
    assert.notEqual(after.cloud_access_token, seed.expired_access)
    rotations.push({ phase: 'personal-proxy', round, refreshFingerprint: digest(after.cloud_refresh_token) })
  }
  pass('真实个人代理连续 3 次过期 access 自动续期并搜索，令牌写回临时库')
  if (process.env.TEAMHUB_REFRESH_API_ONLY !== '1') {
    const modulePath = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
    const { _electron } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(modulePath) ? modulePath : 'playwright'))
    const fixtureFile = path.join(temp, 'electron-fixture.json')
    fs.writeFileSync(fixtureFile, JSON.stringify({ temp, api, repo, capability }), { mode: 0o600 })
    const electronEnv = { ...process.env, TEAMHUB_REFRESH_FIXTURE: fixtureFile }; delete electronEnv.ELECTRON_RUN_AS_NODE
    electron = await _electron.launch({ executablePath: path.join(repo, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), args: [path.join(repo, 'scripts/test_desktop_session_refresh_app.cjs')], env: electronEnv })
    const context = electron.context(); await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
    const initializeAudit = () => {
      // 观测生产投递轮询的排队与取消，保留真实时间间隔和 HTTP。
      window.__refreshTimerAudit = { scheduled: 0, fired: 0, live: [] }
      const schedule = window.setTimeout.bind(window), cancel = window.clearTimeout.bind(window), active = new Set()
      window.setTimeout = (fn, ms, ...args) => {
        const watched = typeof fn === 'function' && /deliveries\/unread-count/.test(String(fn))
        if (!watched) return schedule(fn, ms, ...args)
        window.__refreshTimerAudit.scheduled++
        const id = schedule(() => { active.delete(id); window.__refreshTimerAudit.live = [...active]; window.__refreshTimerAudit.fired++; fn(...args) }, ms)
        active.add(id); window.__refreshTimerAudit.live = [...active]; return id
      }
      window.clearTimeout = id => { active.delete(id); window.__refreshTimerAudit.live = [...active]; return cancel(id) }
    }
    page = await electron.firstWindow(); page.setDefaultTimeout(12000)
    await page.addInitScript(initializeAudit)
    page.on('pageerror', error => browserErrors.push(error.message))
    await context.route('**/*', async route => { const url = new URL(route.request().url()); if (['http:', 'https:'].includes(url.protocol) && url.origin !== api) { externalRequests.push(url.href); return route.abort() } return route.continue() })
    // 重新加载确保所有观测在生产代码启动前装好。
    await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/shared')
    assert(await page.evaluate(() => !!window.__refreshTimerAudit), '应在生产代码启动前装好轮询观测')
    if (await page.getByRole('button', { name: '打开文件库', exact: true }).count()) await page.getByRole('button', { name: '打开文件库', exact: true }).click()
    await page.getByTestId('preview-current-' + published.document_id).waitFor()
    expireAccess(seed.expired_access)
    await page.getByPlaceholder('按名称查找', { exact: true }).fill('续期验收')
    await page.getByTestId('preview-current-' + published.document_id).waitFor()
    await until(() => config().cloud_access_token !== seed.expired_access, 'file 页面触发透明续期')
    await page.getByTestId('preview-current-' + published.document_id).click()
    await page.getByTestId('preview-text').waitFor(); assert((await page.getByTestId('preview-text').innerText()).includes(content.toString('utf8').trim()))
    await page.getByTestId('preview-close').click()
    await page.waitForLoadState('networkidle')
    await page.screenshot({ path: path.join(temp, 'desktop-refreshed.png'), fullPage: true })
    pass('生产 Electron file 页面闲置过期后透明续期，搜索及预览正常')
    const activeRefresh = config().cloud_refresh_token
    assert.equal((await request(origin, '/api/auth/logout', { method: 'POST', body: { refresh_token: activeRefresh } })).status, 204)
    expireAccess(seed.expired_access)
    const terminal = page.waitForResponse(response => response.url().startsWith(api) && response.status() === 401)
    await page.getByPlaceholder('按名称查找', { exact: true }).fill('续期验收共享')
    const terminalResponse = await terminal, terminalBody = await terminalResponse.json()
    assert.equal(terminalBody.detail?.error?.code || terminalBody.error?.code || terminalBody.detail?.code, 'CLOUD_SESSION_EXPIRED')
    await page.getByTestId('cloud-session-expired').waitFor(); await page.getByTestId('cloud-session-login').waitFor()
    assert.equal(await page.getByTestId('preview-current-' + published.document_id).count(), 0, '终态清空旧共享资料')
    assert(!config().cloud_access_token && !config().cloud_refresh_token, '终态必须清除旧凭据')
    await page.screenshot({ path: path.join(temp, 'desktop-sign-in-required.png'), fullPage: true })
    // 生产代码应取消已经排队的真实轮询；无需等待真实 10 秒间隔。
    await until(() => page.evaluate(() => window.__refreshTimerAudit.live.length === 0), '取消云端轮询')
    const audit = await page.evaluate(() => ({ ...window.__refreshTimerAudit }))
    assert(audit.scheduled > 0, '必须实际观测到生产轮询排队后再验证取消')
    await page.evaluate(() => { window.dispatchEvent(new Event('focus')); window.dispatchEvent(new Event('focus')) })
    await json(api, `/api/documents/${localDoc.id}`); await json(api, '/api/documents/pending/count')
    assert.deepEqual(await page.evaluate(() => window.__refreshTimerAudit.live), [], '失效会话不得重新安排投递轮询')
    pass('刷新令牌撤销后明确重登录、清旧资料与凭据并停止云端轮询')
    await page.getByTestId('cloud-session-login').click()
    await page.getByPlaceholder('your@email.com').fill(seed.email)
    await page.getByPlaceholder('请输入密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByTestId('preview-current-' + published.document_id).waitFor()
    assert.equal(await page.getByTestId('cloud-session-expired').count(), 0)
    await page.locator('.nav-primary .nav-item').filter({ hasText: '文档库' }).click()
    await page.getByText('续期验收个人资料.txt', { exact: true }).first().waitFor()
    await page.screenshot({ path: path.join(temp, 'desktop-local-library-intact.png'), fullPage: true })
    pass('重新登录回到原云仓库，个人资料在终态与恢复后均独立可读')
    fs.writeFileSync(path.join(temp, 'polling-audit.json'), JSON.stringify(audit, null, 2))
    await context.tracing.stop({ path: path.join(temp, 'desktop-trace.zip') })
  } else {
    const activeRefresh = config().cloud_refresh_token
    assert.equal((await request(origin, '/api/auth/logout', { method: 'POST', body: { refresh_token: activeRefresh } })).status, 204)
    expireAccess(seed.expired_access)
    const response = await request(api, searchRoute), body = await response.json()
    assert.equal(response.status, 401); assert.equal(body.detail?.error?.code || body.error?.code || body.detail?.code, 'CLOUD_SESSION_EXPIRED')
    assert(!config().cloud_access_token && !config().cloud_refresh_token)
    pass('真实刷新令牌撤销后返回统一会话失效并清凭据')
  }
  assert.deepEqual(await snapshotLocal(), localBefore, '续期和终态不能改变个人资料或版本')
  const currentLogin = await json(origin, '/api/auth/login', { method: 'POST', body: { email: seed.email, password } })
  assert.deepEqual(await json(origin, `${base}/documents/${published.document_id}`, { token: currentLogin.access_token }), cloudBefore, '续期不能改变云资料与版本')
  assert.deepEqual(browserErrors, []); assert.deepEqual(externalRequests, [])
  pass('个人库和云资料内容、数量、版本不变，无外部请求或页面异常')
}
function save(status, error) {
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  const value = { status, groups: results, rotations, browserErrors, externalRequests, durationSeconds: (Date.now() - startedAt) / 1000, ...(error ? { error: error.stack } : {}) }
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify(value, null, 2))
  fs.writeFileSync(path.join(temp, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="desktop-session-refresh" tests="${results.length + (error ? 1 : 0)}" failures="${error ? 1 : 0}">${results.map(name => `<testcase name="${escape(name)}"/>`).join('')}${error ? `<testcase name="session-refresh"><failure>${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>连续续期验收</title><h1>${escape(status)}</h1><ul>${results.map(name => `<li>${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p>本报告仅含合成测试账号资料；令牌只记录不可用的短哈希。</p><p><a href="desktop-refreshed.png">透明续期</a> · <a href="desktop-sign-in-required.png">重新登录提示</a> · <a href="desktop-local-library-intact.png">个人资料完整</a> · <a href="desktop-trace.zip">trace</a></p></html>`)
  console.log('验收产物：' + temp)
}
main().then(() => save('passed'), async error => { console.error(error); if (page) await page.screenshot({ path: path.join(temp, 'failure.png'), fullPage: true }).catch(() => {}); save('failed', error); process.exitCode = 1 }).finally(async () => { if (electron) await electron.close().catch(() => {}); for (const child of children.reverse()) { if (child.exitCode === null) child.kill('SIGTERM') } })
