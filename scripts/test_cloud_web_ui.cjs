#!/usr/bin/env node
// 独立云网页真实浏览器验收：仅临时云端、合成资料；不启动个人后端或 Electron。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, execFileSync } = require('node:child_process')

let chromium
try { ({ chromium } = require(process.env.PLAYWRIGHT_MODULE || 'playwright')) }
catch { throw new Error('此验收需要 Playwright。安装 playwright，或将 PLAYWRIGHT_MODULE 指向已安装模块；不使用 Electron 替代浏览器。') }

const root = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-cloud-web-ui-'))
const python = path.join(root, 'cloud_backend/venv/bin/python')
const password = crypto.randomBytes(24).toString('hex')
const env = {
  ...process.env, TEAMHUB_SANDBOX: '1', DATABASE_URL: `sqlite:///${temp}/cloud.db`,
  STORAGE_DIR: path.join(temp, 'objects'), PYTHONPATH: path.join(root, 'cloud_backend'),
  JWT_SECRET_KEY: crypto.randomBytes(40).toString('hex'), TEAMHUB_TEST_PASSWORD: password,
  CLOUD_WEB_DIST: path.join(root, 'frontend/dist'),
}
const seed = JSON.parse(execFileSync(python, ['-c', `
import json, os
from app.database import init_db, SessionLocal
from app.models import User, Team, TeamMember, Workspace
from app.core.security import hash_password
init_db()
with SessionLocal() as db:
    users = {}
    password = hash_password(os.environ['TEAMHUB_TEST_PASSWORD'])
    for key in ('a', 'b', 'c', 'm'):
        user = User(email=key+'@cloud-ui.example', display_name={'a':'甲成员','b':'乙成员','c':'丙成员','m':'组织管理员'}[key], hashed_password=password, is_superuser=key=='m')
        db.add(user); db.flush(); users[key] = user
    team = Team(name='网页验收组织', slug='web-ui-team'); db.add(team); db.flush()
    for key, user in users.items():
        db.add(TeamMember(team_id=team.id, user_id=user.id, role='admin' if key=='m' else 'member'))
    ws = Workspace(team_id=team.id, name='项目资料'); other = Workspace(team_id=team.id, name='另一空间')
    db.add_all([ws, other]); db.commit()
    print(json.dumps({'workspace':ws.id, 'other_workspace':other.id, 'users':{key:{'id':user.id,'email':user.email} for key,user in users.items()}}))
`], { cwd: temp, env, encoding: 'utf8' }).trim())

let service, browser
const pages = []
const contexts = []
const startedAt = Date.now()
const checks = []
function record(name) { checks.push(name) }
function saveResults(status, error) {
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  const seconds = (Date.now() - startedAt) / 1000
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify({ status, checks, seconds, externalRequests: unexpectedRequests, browserErrors, ...(error ? { error: error.stack } : {}) }, null, 2))
  fs.writeFileSync(path.join(temp, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="Cloud web integration" tests="${checks.length + (error ? 1 : 0)}" failures="${error ? 1 : 0}" time="${seconds}">${checks.map(name => `<testcase name="${escape(name)}"/>`).join('')}${error ? `<testcase name="未完成云网页验收"><failure message="${escape(error.message)}">${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>真实云网页验收</title><h1>真实云网页验收：${escape(status)}</h1><p>${seconds.toFixed(1)} 秒</p><ul>${checks.map(name => `<li>${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p><a href="cloud-library-preference.png">选库记忆</a> · <a href="cloud-account-library-choice.png">换账号重新选库</a> · <a href="cloud-delivery.png">资料投递</a></p></html>`)
}
const unexpectedRequests = []
const browserErrors = []
const observe = promise => { promise.catch(() => {}); return promise }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
const availablePort = () => new Promise((resolve, reject) => {
  const server = net.createServer()
  server.on('error', reject)
  server.listen(0, '127.0.0.1', () => { const port = server.address().port; server.close(() => resolve(port)) })
})

async function main() {
  const port = await availablePort()
  const origin = `http://127.0.0.1:${port}`
  const log = fs.openSync(path.join(temp, 'cloud.log'), 'w')
  service = spawn(python, ['-m', 'uvicorn', 'main:app', '--app-dir', path.join(root, 'cloud_backend'), '--host', '127.0.0.1', '--port', String(port)], { cwd: temp, env, stdio: ['ignore', log, log] })
  fs.closeSync(log)
  let healthy = false
  for (let count = 0; count < 120; count++) {
    if (service.exitCode !== null) throw new Error(`临时云端提前退出：${fs.readFileSync(path.join(temp, 'cloud.log'), 'utf8')}`)
    try { healthy = (await fetch(`${origin}/api/health`)).ok } catch {}
    if (healthy) break
    await delay(100)
  }
  assert(healthy, '临时云服务未就绪')
  browser = await chromium.launch({ headless: true, ...(process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE ? { executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE } : {}) })

  async function actor(key) {
    const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, acceptDownloads: true })
    contexts.push(context); await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
    await context.route('**/*', async route => {
      const url = new URL(route.request().url())
      if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) {
        unexpectedRequests.push(route.request().url()); await route.abort(); return
      }
      await route.continue()
    })
    const page = await context.newPage()
    pages.push(page)
    page.on('pageerror', error => browserErrors.push(error.message))
    await page.goto(`${origin}/cloud.html`)
    await page.getByLabel('邮箱', { exact: true }).fill(seed.users[key].email)
    await page.getByLabel('密码', { exact: true }).fill(password)
    const responsePromise = observe(page.waitForResponse(response => response.url().endsWith('/api/web-auth/login') && response.request().method() === 'POST'))
    await page.getByRole('button', { name: '登录', exact: true }).click()
    const response = await responsePromise
    assert.equal(response.status(), 200)
    const token = (await response.json()).access_token
    // 首次多库登录直接点击所需资料库，不随机进入第一个库。
    await page.getByTestId(`shared-library-entry-${seed.workspace}`).click()
    await page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
    return { page, context, token }
  }

  async function search(page, value) {
    await page.getByLabel('搜索资料名称').fill(value)
    const done = observe(page.waitForResponse(response => response.url().includes('/documents/search') && new URL(response.url()).searchParams.get('q') === value))
    await page.getByRole('button', { name: '搜索', exact: true }).click()
    await done
  }

  async function assertDownload(page, button, expected) {
    const event = observe(page.waitForEvent('download'))
    await button.click()
    const download = await event
    const target = path.join(temp, crypto.randomUUID() + '.txt')
    await download.saveAs(target)
    assert.equal(fs.readFileSync(target, 'utf8'), expected)
  }

  const a = await actor('a')
  let cookies = await a.context.cookies()
  assert(cookies.some(cookie => cookie.name === 'teamhub_web_refresh' && cookie.httpOnly && cookie.sameSite === 'Strict' && cookie.path === '/api/web-auth'))
  assert.equal(await a.page.evaluate(() => Object.keys(localStorage).some(key => /token/i.test(key))), false)
  for (let count = 0; count < 2; count++) {
    const refreshed = observe(a.page.waitForResponse(response => response.url().endsWith('/api/web-auth/refresh')))
    await a.page.reload()
    assert.equal((await refreshed).status(), 200)
    await a.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  }
  // 重新定位刷新后空间，确认空间切换是服务端范围选择。
  await a.page.getByTestId('shared-library-selector').click()
  await a.page.getByRole('option', { name: '网页验收组织 / 另一空间', exact: true }).click()
  await a.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  await a.page.getByTestId('shared-library-selector').click()
  await a.page.getByRole('option', { name: '网页验收组织 / 项目资料', exact: true }).click()
  record('真实HTTP登录、Cookie恢复与双资料库切换')

  if (process.argv.includes('--races-only')) {
    // 把三个上传阶段分别停在真实网络响应处，再切换账号。
    for (const stage of ['lookup', 'upload', 'commit']) {
      const isolated = await actor('a')
      const page = isolated.page
      let release, entered
      const gate = new Promise(resolve => { release = resolve })
      const reached = new Promise(resolve => { entered = resolve })
      let intercepted = false
      const writes = []
      page.on('request', request => {
        if (request.method() === 'POST' && /\/(uploads|publications)(?:\?|$)/.test(request.url())) {
          const bearer = request.headers().authorization?.split(' ')[1]
          writes.push(bearer ? JSON.parse(Buffer.from(bearer.split('.')[1], 'base64url').toString()).sub : null)
        }
      })
      const handler = async route => {
        const request = route.request(), url = new URL(request.url())
        const matches = stage === 'lookup' ? /\/publications\/[^/]+$/.test(url.pathname)
          : stage === 'upload' ? url.pathname.endsWith('/uploads') : url.pathname.endsWith('/publications') && request.method() === 'POST'
        if (!matches || intercepted) { await route.continue(); return }
        intercepted = true
        const response = await route.fetch()
        entered()
        await gate
        await route.fulfill({ response }).catch(() => {})
      }
      await page.route('**/api/workspaces/**', handler)
      await page.getByTestId('new-publication').click()
      await page.getByRole('dialog', { name: '上传新资料', exact: true }).waitFor()
      await page.getByLabel('上传文件', { exact: true }).setInputFiles({ name: `账号阶段-${stage}.txt`, mimeType: 'text/plain', buffer: Buffer.from(stage) })
      await page.getByText('确认文件内容、目标资料与访问范围', { exact: true }).click()
      await page.getByRole('button', { name: '确认上传', exact: true }).click()
      await Promise.race([reached, delay(10000).then(() => { throw new Error(`未进入上传阶段 ${stage}`) })])
      // 定向触发真实退出处理，模拟会话在上传弹窗仍存在时被切换。
      await page.getByRole('button', { name: '退出登录', exact: true }).dispatchEvent('click')
      await page.getByRole('heading', { name: '登录云服务', exact: true }).waitFor()
      await page.getByLabel('邮箱', { exact: true }).fill(seed.users.b.email)
      await page.getByLabel('密码', { exact: true }).fill(password)
      await page.getByRole('button', { name: '登录', exact: true }).click()
      await page.getByTestId(`shared-library-entry-${seed.workspace}`).click()
      await page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
      release()
      await delay(300)
      assert(writes.every(userId => userId === seed.users.a.id), `${stage} 不得改用新账号发送旧资料`)
      assert.equal(writes.length, stage === 'lookup' ? 0 : stage === 'upload' ? 1 : 2, '账号变化后不得继续后续上传阶段')
      await isolated.context.close()
    }

    const created = []
    for (const letter of ['A', 'B']) {
      const uploaded = await a.context.request.post(`${origin}/api/workspaces/${seed.workspace}/uploads?filename=race.txt&purpose=publication`, { headers: { Authorization: `Bearer ${a.token}`, 'Content-Type': 'application/octet-stream' }, data: Buffer.from(`race-${letter}`) })
      assert.equal(uploaded.status(), 200)
      const upload = await uploaded.json()
      const response = await a.context.request.post(`${origin}/api/workspaces/${seed.workspace}/publications`, { headers: { Authorization: `Bearer ${a.token}` }, data: {
        request_id: crypto.randomUUID(), name: `竞态资料${letter}.txt`, visibility: 'restricted', confirmed: true,
        doc_type: 'file', upload_id: upload.upload_id, content_hash: upload.sha256,
        access: [{ user_id: seed.users.b.id, role: 'reader' }],
      } })
      assert.equal(response.status(), 200)
      created.push((await response.json()).document_id)
    }
    await search(a.page, '竞态资料')
    let releaseDetail, detailEntered
    const detailGate = new Promise(resolve => { releaseDetail = resolve })
    const detailReached = new Promise(resolve => { detailEntered = resolve })
    const firstDetailUrl = `${origin}/api/workspaces/${seed.workspace}/documents/${created[0]}`
    await a.page.route(firstDetailUrl, async route => {
      const response = await route.fetch(); detailEntered(); await detailGate
      await route.fulfill({ response })
    })
    await a.page.getByRole('button', { name: '竞态资料A.txt', exact: true }).click()
    await detailReached
    await a.page.getByRole('button', { name: '竞态资料B.txt', exact: true }).click()
    await a.page.getByRole('heading', { name: '竞态资料B.txt', exact: true }).waitFor()
    await a.page.getByRole('button', { name: '管理访问范围', exact: true }).click()
    const accessDialog = a.page.getByRole('dialog', { name: '管理访问范围', exact: true })
    await accessDialog.waitFor()
    releaseDetail()
    await delay(200)
    assert.equal(await a.page.getByRole('heading', { name: '竞态资料A.txt', exact: true }).count(), 0)
    await accessDialog.getByRole('combobox', { name: '乙成员的权限', exact: true }).press('ArrowDown')
    await a.page.getByRole('option', { name: '可协作', exact: true }).click()
    const updated = observe(a.page.waitForResponse(response => response.request().method() === 'PUT' && response.url().endsWith('/access')))
    await accessDialog.getByRole('button', { name: '保存访问范围', exact: true }).click()
    const updateResponse = await updated
    assert.equal(updateResponse.status(), 200, await updateResponse.text())
    assert.equal(updateResponse.url(), `${origin}/api/workspaces/${seed.workspace}/documents/${created[1]}/access`)
    for (let index = 0; index < 2; index++) {
      const response = await a.context.request.get(`${origin}/api/workspaces/${seed.workspace}/documents/${created[index]}/access`, { headers: { Authorization: `Bearer ${a.token}` } })
      const entries = (await response.json()).access
      assert.equal(entries.find(entry => entry.user_id === seed.users.b.id).role, index ? 'contributor' : 'reader')
    }
    console.log('PASS account changes at lookup/upload/commit and out-of-order document ACL target')
  }

  if (!process.argv.includes('--auth-only') && !process.argv.includes('--races-only')) {
    const v1 = '独立网页上传第一版\n'
    const v2 = '独立网页上传第二版\n'
    const name = '网页指定资料.txt'
    await a.page.getByTestId('new-publication').click()
    await a.page.getByLabel('上传文件', { exact: true }).setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(v1) })
    await a.page.getByRole('combobox', { name: '添加授权成员', exact: true }).click()
    await a.page.getByRole('option', { name: /乙成员/ }).click()
    await a.page.getByText('确认文件内容、目标资料与访问范围', { exact: true }).click()
    const publication = observe(a.page.waitForResponse(response => response.url().endsWith('/publications') && response.request().method() === 'POST'))
    await a.page.getByRole('button', { name: '确认上传', exact: true }).click()
    const pubResponse = await publication
    assert.equal(pubResponse.status(), 200, await pubResponse.text())
    const published = await pubResponse.json()
    assert.equal(published.status, 'succeeded')
    await a.page.getByRole('heading', { name, exact: true }).waitFor()
    await assertDownload(a.page, a.page.getByRole('button', { name: '下载版本1', exact: true }), v1)

    let deliveryId
    if (!process.argv.includes('--sharing')) {
      await a.page.getByRole('button', { name: '投递当前版本', exact: true }).click()
      const dialog = a.page.getByRole('dialog', { name: '投递资料', exact: true })
      await dialog.getByRole('combobox').click()
      await a.page.getByRole('option', { name: '乙成员', exact: true }).click()
      await dialog.getByRole('combobox').press('Escape')
      const sent = observe(a.page.waitForResponse(response => response.url().endsWith('/deliveries') && response.request().method() === 'POST'))
      await dialog.getByRole('button', { name: '确认发送', exact: true }).click()
      const sentResponse = await sent
      assert.equal(sentResponse.status(), 200, await sentResponse.text())
      deliveryId = (await sentResponse.json()).id
      await dialog.waitFor({ state: 'hidden' })
    }

    await a.page.getByRole('button', { name: '提交新版本', exact: true }).click()
    await a.page.getByRole('dialog', { name: '提交新版本', exact: true }).waitFor()
    await a.page.getByLabel('上传文件', { exact: true }).setInputFiles({ name, mimeType: 'text/plain', buffer: Buffer.from(v2) })
    await a.page.getByLabel('本次版本说明', { exact: true }).fill('网页更新版本')
    await a.page.getByText('确认文件内容、目标资料与访问范围', { exact: true }).click()
    const submitted = observe(a.page.waitForResponse(response => response.url().endsWith('/versions') && response.request().method() === 'POST'))
    await a.page.getByRole('button', { name: '确认提交', exact: true }).click()
    assert.equal((await submitted).status(), 200)
    await assertDownload(a.page, a.page.getByRole('button', { name: '下载版本2', exact: true }), v2)
    await a.page.getByRole('dialog', { name: '资料详情', exact: true }).getByRole('button', { name: /关闭此对话框|Close this dialog/ }).click()
    await search(a.page, name)
    await a.page.screenshot({ path: path.join(temp, 'cloud-library.png'), fullPage: true })

    const b = await actor('b')
    await search(b.page, name)
    await b.page.getByRole('button', { name, exact: true }).click()
    await b.page.getByRole('heading', { name, exact: true }).waitFor()
    assert.equal(await b.page.getByRole('button', { name: '提交新版本', exact: true }).count(), 0)
    await assertDownload(b.page, b.page.getByRole('button', { name: '下载版本2', exact: true }), v2)
    await b.page.getByRole('dialog', { name: '资料详情', exact: true }).getByRole('button', { name: /关闭此对话框|Close this dialog/ }).click()

    for (const key of ['c', 'm']) {
      const other = await actor(key)
      await search(other.page, name)
      assert.equal(await other.page.getByRole('button', { name, exact: true }).count(), 0)
      const guessed = await other.context.request.get(`${origin}/api/workspaces/${seed.workspace}/documents/${published.document_id}`, { headers: { Authorization: `Bearer ${other.token}` } })
      assert.equal(guessed.status(), 404)
    }

    if (deliveryId) {
      await b.page.getByRole('button', { name: '资料投递', exact: false }).click()
      await b.page.getByText(name, { exact: true }).first().click()
      await assertDownload(b.page, b.page.getByRole('button', { name: '下载附件', exact: true }), v1)
      await b.page.getByRole('button', { name: '关闭', exact: true }).click()
      await b.page.screenshot({ path: path.join(temp, 'cloud-delivery.png'), fullPage: true })
      await a.page.getByRole('button', { name: '资料投递', exact: false }).click()
      await a.page.getByRole('tab', { name: '已发送的资料', exact: true }).click()
      await a.page.getByText(name, { exact: true }).first().click()
      await a.page.getByRole('button', { name: '撤回投递', exact: true }).click()
      await a.page.getByRole('button', { name: '确认撤回', exact: true }).click()
      await a.page.getByText('发送者已撤回，不能继续获取附件。已保存的个人副本不受影响。').waitFor()
      await b.page.getByRole('button', { name: '刷新', exact: true }).click()
      await b.page.getByText(name, { exact: true }).first().click()
      await b.page.getByText('发送者已撤回，不能继续获取附件。已保存的个人副本不受影响。').waitFor()
      assert.equal(await b.page.getByRole('button', { name: '下载附件', exact: true }).count(), 0)
      const independent = await b.context.request.get(`${origin}/api/workspaces/${seed.workspace}/documents/${published.document_id}/versions/${published.version_id}/download`, { headers: { Authorization: `Bearer ${b.token}` } })
      assert.equal(independent.status(), 200)
      assert.equal((await independent.body()).toString(), v1)
    }
  }

  // A library preference is remembered for the server + account, not for the entire browser.
  const preference = await actor('a')
  await preference.page.getByTestId('shared-library-selector').click()
  await preference.page.getByRole('option', { name: '网页验收组织 / 另一空间', exact: true }).click()
  await preference.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  await preference.page.reload(); await preference.page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  assert((await preference.page.getByTestId('cloud-library-picker').innerText()).includes('另一空间'), 'The same account must reopen its last library')
  const saved = await preference.page.evaluate(() => Object.entries(localStorage).filter(([key]) => key.startsWith('teamhub:shared-library:')))
  assert(saved.some(([, value]) => value === seed.other_workspace), 'The preference stores a workspace ID')
  assert(saved.every(([, value]) => value === seed.workspace || value === seed.other_workspace), 'No names, credentials or document details are stored in preferences')
  await preference.page.screenshot({ path: path.join(temp, 'cloud-library-preference.png'), fullPage: true })
  await preference.page.getByRole('button', { name: '退出登录', exact: true }).click()
  await preference.page.getByRole('heading', { name: '登录云服务', exact: true }).waitFor()
  await preference.page.getByLabel('邮箱', { exact: true }).fill(seed.users.b.email)
  await preference.page.getByLabel('密码', { exact: true }).fill(password)
  await preference.page.getByRole('button', { name: '登录', exact: true }).click()
  await preference.page.getByTestId(`shared-library-entry-${seed.workspace}`).waitFor()
  await preference.page.getByTestId(`shared-library-entry-${seed.other_workspace}`).waitFor()
  assert.equal(await preference.page.getByRole('heading', { name: '组织资料', exact: true }).count(), 0, 'The next account must not inherit A\'s browsing selection')
  await preference.page.screenshot({ path: path.join(temp, 'cloud-account-library-choice.png'), fullPage: true })
  record('真实HTTP同浏览器重开沿用上次库，换账号首次重新选库')

  // 管理页复用同源 Cookie 会话，不再将令牌写入 localStorage。
  const adminContext = await browser.newContext()
  const adminPage = await adminContext.newPage(); pages.push(adminPage)
  await adminPage.goto(`${origin}/admin.html`)
  await adminPage.getByPlaceholder('admin@example.com').fill(seed.users.m.email)
  await adminPage.getByPlaceholder('请输入密码').fill(password)
  await adminPage.getByRole('button', { name: '登录', exact: true }).click()
  await adminPage.locator('.topbar-user').filter({ hasText: '组织管理员' }).waitFor()
  assert.equal(await adminPage.evaluate(() => localStorage.getItem('admin_token')), null)
  const finalDialog = a.page.getByRole('dialog', { name: '投递详情', exact: true })
  if (await finalDialog.isVisible()) await finalDialog.getByRole('button', { name: '关闭', exact: true }).click()
  await a.page.getByRole('button', { name: '退出登录', exact: true }).click()
  await a.page.getByRole('heading', { name: '登录云服务', exact: true }).waitFor()
  assert.equal((await a.context.request.get(`${origin}/api/users/me`, { headers: { Authorization: `Bearer ${a.token}` } })).status(), 401)
  assert.deepEqual(unexpectedRequests, [], '网页不得请求本地个人服务或外部接口')
  assert.deepEqual(browserErrors, [], '浏览器不得出现运行时错误')
  record('真实HTTP资料发布、版本、访问范围与投递管理回归')
  record('管理页会话与退出撤销令牌，零外部请求、零浏览器异常')
  console.log(`PASS cloud web ${process.argv.slice(2).join(' ') || 'full'} — isolated artifacts: ${temp}`)
}

main().then(() => saveResults('passed')).catch(async error => {
  for (let i = 0; i < pages.length; i++) await pages[i].screenshot({ path: path.join(temp, `failure-${i}.png`), fullPage: true }).catch(() => {})
  console.error(error.stack || error)
  console.error(`隔离现场保留：${temp}`)
  saveResults('failed', error)
  process.exitCode = 1
}).finally(async () => {
  for (let index = 0; index < contexts.length; index++) await contexts[index].tracing.stop({ path: path.join(temp, `actor-${index}-trace.zip`) }).catch(() => {})
  if (browser) await browser.close()
  if (service && service.exitCode === null) {
    service.kill('SIGTERM')
    for (let count = 0; count < 30 && service.exitCode === null; count++) await delay(100)
    if (service.exitCode === null) service.kill('SIGKILL')
  }
})
