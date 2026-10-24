#!/usr/bin/env node
// 隔离的内容索引验收：生产 Electron 页面、临时资料库、本机合成 AI 服务。
// TEAMHUB_AI_BACKEND_BINARY 可指定当前项目 dist 中的冻结后端作同一流程复验。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn } = require('node:child_process')

const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-ai-assist-index-'))
const library = path.join(temp, 'library-a')
const capability = crypto.randomBytes(32).toString('hex')
const testKey = 'sk-teamhub-index-synthetic-only-not-a-real-key'
const model = 'teamhub-index-e2e'
const originalTerm = '虹桥蓝鹭原文词', updatedTerm = '青川云帆更新词', clueTerm = '星河辅助检索词'
const started = Date.now(), results = [], pageErrors = [], externalRequests = [], providerCalls = [], children = []
let api, provider, providerBase, electron, page, context, tracing = false

async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await check()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 50))
  }
  throw new Error('等待超时：' + label)
}
function pass(name) { results.push(name); console.log('PASS ' + name) }
const freePort = () => new Promise((resolve, reject) => {
  const listener = net.createServer().once('error', reject)
  listener.listen(0, '127.0.0.1', () => {
    const port = listener.address().port
    listener.close(() => resolve(port))
  })
})
async function request(route, { method = 'GET', body, ai = false } = {}) {
  return fetch(api + route, {
    method, headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(ai ? { 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': capability } : {}) },
    body: body ? JSON.stringify(body) : undefined,
  })
}
async function json(route, options) {
  const response = await request(route, options)
  assert.equal(response.status, 200, `${route}: ${await response.clone().text()}`)
  return response.json()
}
const search = (keyword, scope = {}) => json('/api/documents/search', { method: 'POST', ai: true, body: { include_content: true, keyword, ...scope } })
const index = id => json(`/api/ai/indexes/${id}`, { ai: true })
const versions = id => json(`/api/documents/${id}/versions`)
const document = id => json(`/api/documents/${id}`)
async function waitIndex(docId, versionId) {
  return until(async () => {
    const value = await index(docId)
    if (value.state === 'failed') throw new Error('索引失败：' + JSON.stringify(value))
    return value.state === 'ready' && value.version_id === versionId ? value : false
  }, '指定版本内容索引')
}
async function startProvider() {
  provider = http.createServer(async (req, res) => {
    let bytes = ''
    try {
      for await (const chunk of req) {
        bytes += chunk
        if (bytes.length > 200000) { res.writeHead(413); res.end(); return }
      }
      assert.equal(req.method, 'POST'); assert.equal(req.url, '/v1/chat/completions')
      assert.equal(req.headers.authorization, 'Bearer ' + testKey)
      const body = JSON.parse(bytes)
      assert.equal(body.model, model); assert.equal(body.stream, false)
      assert.equal(body.response_format?.type, 'json_object')
      const sent = JSON.parse(body.messages.find(message => message.role === 'user').content)
      assert.deepEqual(Object.keys(sent).sort(), ['filename', 'text'])
      const payload = JSON.parse(sent.text)
      assert(payload.units.length > 0)
      providerCalls.push(sent)
      const output = { overview: '根据本次摘录生成的检索提示。', clues: [{ unit_id: payload.units[0].id, terms: [clueTerm] }] }
      res.writeHead(200, { 'Content-Type': 'application/json' })
      res.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: JSON.stringify(output) } }] }))
    } catch (error) {
      pageErrors.push('fake provider: ' + error.message)
      res.writeHead(500); res.end('{}')
    }
  })
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve))
  providerBase = `http://127.0.0.1:${provider.address().port}/v1`
}
async function startBackend() {
  api = `http://127.0.0.1:${await freePort()}`
  let binary
  if (process.env.TEAMHUB_AI_BACKEND_BINARY) {
    binary = fs.realpathSync(path.resolve(process.env.TEAMHUB_AI_BACKEND_BINARY))
    assert(binary.startsWith(path.join(repo, 'dist') + path.sep) && fs.statSync(binary).isFile(), '冻结后端必须来自当前项目 dist')
  }
  const env = {
    PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'zh_CN.UTF-8', TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true',
    TEAMHUB_AI_CAPABILITY: capability, USER_DATA_DIR: path.join(temp, 'backend-profile'), TEAMHUB_AI_CONFIG_DIR: path.join(temp, 'ai-config'),
    LIBRARY_PATH: library, PORT: new URL(api).port, CLOUD_API_URL: providerBase, TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false', PYTHONPATH: repo,
  }
  const logFile = path.join(temp, 'personal.log'), fd = fs.openSync(logFile, 'w')
  const child = spawn(binary || path.join(repo, 'venv/bin/python'), binary ? [] : ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port], { cwd: temp, env, stdio: ['ignore', fd, fd] })
  children.push(child); fs.closeSync(fd)
  await until(async () => {
    if (child.exitCode !== null) throw new Error('个人后端退出：' + fs.readFileSync(logFile, 'utf8'))
    try { return (await fetch(api + '/api/health')).ok } catch { return false }
  }, '隔离个人后端就绪', 30000)
  assert.equal((await json('/api/settings/library')).path, library)
  console.log('个人后端：' + (binary || '隔离 venv uvicorn'))
}
async function startDesktop() {
  const bundled = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
  const { _electron } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(bundled) ? bundled : 'playwright'))
  const fixture = path.join(temp, 'fixture.json')
  fs.writeFileSync(fixture, JSON.stringify({ repo, temp, api, capability }), { mode: 0o600 })
  const env = { ...process.env, TEAMHUB_AI_FIXTURE: fixture }; delete env.ELECTRON_RUN_AS_NODE
  electron = await _electron.launch({ executablePath: path.join(repo, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), args: [path.join(repo, 'scripts/test_ai_assist_desktop_app.cjs')], env })
  page = await electron.firstWindow(); page.setDefaultTimeout(15000)
  context = electron.context()
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true }); tracing = true
  page.on('pageerror', error => pageErrors.push(error.message))
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== api) { externalRequests.push(url.href); return route.abort() }
    return route.continue()
  })
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/pending')
  const enter = page.getByRole('button', { name: '打开文件库', exact: true })
  await enter.waitFor({ state: 'visible' })
  await enter.click()
  await page.locator('.pending-page').waitFor()
}
function field(testId) {
  const root = `[data-testid=${JSON.stringify(testId)}]`
  return page.locator(`${root}:is(input,textarea),${root} input,${root} textarea`)
}
const organizeDialog = () => page.getByRole('dialog', { name: '整理资料', exact: true })
const detailDialog = () => page.getByRole('dialog', { name: '文档详情', exact: true })
const pendingCard = name => page.locator('.pending-card').filter({ has: page.getByText(name, { exact: true }) })
const libraryCard = name => page.locator('.doc-card').filter({ has: page.getByText(name, { exact: true }) })
async function capture(filename, target) {
  if (target) await target.scrollIntoViewIfNeeded()
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().iterations)).map(animation => animation.finished.catch(() => {})))
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await page.screenshot({ path: path.join(temp, filename), fullPage: true })
}
async function pendingSearch(term, total = 1) {
  const input = page.getByRole('textbox', { name: '搜索待整理资料', exact: true })
  await input.fill(term)
  const completed = page.waitForResponse(response => {
    const address = new URL(response.url())
    return address.origin === api && address.pathname === '/api/ai/preorganize/documents'
      && address.searchParams.get('search') === term
  })
  await input.press('Enter')
  const response = await completed
  assert.equal(response.status(), 200); assert.equal((await response.json()).total, total)
}
async function librarySearch(term, total = 1) {
  const input = page.locator('.smart-search-bar .inner-input')
  await input.fill(term)
  const completed = page.waitForResponse(response => response.url() === api + '/api/documents/search' && response.request().postDataJSON()?.keyword === term)
  await input.press('Enter')
  const response = await completed
  const data = await response.json()
  assert.equal(response.status(), 200); assert.equal(data.total, total)
  await page.locator('.smart-search-bar .suggestions-panel').waitFor({ state: 'hidden' })
  return data
}

async function main() {
  await startProvider(); await startBackend()
  // 提前配置测试密钥，证明配置完毕也不会触发自动外发。
  await json('/api/ai/settings', { method: 'PUT', ai: true, body: { base_url: providerBase, model, api_key: testKey } })
  await json('/api/settings/config', { method: 'PUT', body: { default_sort_by: 'name', default_sort_order: 'asc' } })
  const source = path.join(temp, '测试运行说明.txt')
  fs.writeFileSync(source, `综合运维资料\n${originalTerm}：此关键字仅在正文中。<img src="https://must-not-load.invalid/example">\n检查记录已经登记。\n`)
  // 不读取索引状态来触发排队：后台必须自行发现新收录的已保存版本。
  const doc = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: source } })
  assert(!fs.existsSync(source), '收录保留既有移动语义')
  const v1 = doc.current_version.id
  await until(async () => (await search(originalTerm, { status: 'pending' })).documents.some(item => item.id === doc.id), '后台自动索引正文')
  const originalIndex = await waitIndex(doc.id, v1)
  assert(originalIndex.units.some(unit => unit.text.includes(originalTerm) && unit.location.line))
  assert.equal(providerCalls.length, 0)
  assert.equal((await search(originalTerm, { status: 'organized' })).total, 0)
  const withoutContent = await json('/api/documents/search', { method: 'POST', body: { keyword: originalTerm } })
  assert.equal(withoutContent.total, 0)
  pass('收录 TXT 后后台自动建立带位置的正文索引；已配置 AI 也零外发，原检索契约和待整理范围保持正确')

  await startDesktop()
  await pendingSearch(originalTerm)
  await pendingCard(doc.name).getByTestId('manual-organize-' + doc.id).click()
  const card = organizeDialog().getByTestId('document-index-card')
  await card.getByText('已建立', { exact: true }).waitFor()
  assert((await card.innerText()).includes('v1'))
  await card.getByRole('button', { name: '查看原文单元', exact: true }).click()
  await card.locator('.index-unit pre').filter({ hasText: originalTerm }).waitFor()
  await capture('index-local.png', card)
  assert.equal(await card.getByTestId('index-ai-open').count(), 0, '待整理阶段不生成即将因整理变化失效的 AI 线索')
  const tagInput = organizeDialog().getByRole('combobox', { name: '查找标签', exact: true })
  await tagInput.fill('运行资料')
  await page.getByRole('option', { name: '运行资料', exact: true }).click()
  await organizeDialog().locator('.organize-tags .el-tag').filter({ hasText: '运行资料' }).waitFor()
  await organizeDialog().getByRole('button', { name: '完成整理', exact: true }).click()
  await organizeDialog().waitFor({ state: 'hidden' })
  const organized = await document(doc.id)
  assert.equal(organized.status, 'organized')
  assert.equal(organized.current_version.id, v1)
  assert.deepEqual(organized.tags.map(tag => tag.name), ['运行资料'])
  assert.equal((await search(originalTerm, { status: 'pending' })).total, 0)
  assert.equal((await search(originalTerm, { status: 'organized', tags: ['运行资料'] })).total, 1)
  assert.equal((await search(originalTerm, { status: 'organized', tags: ['不属于该资料的标签'] })).total, 0)
  assert.equal((await search(originalTerm, { status: 'organized', favorites_only: true })).total, 0)
  await page.locator('.nav-primary .nav-item').filter({ hasText: '文档库' }).click()
  await librarySearch(originalTerm)
  await libraryCard(doc.name).waitFor()
  await capture('index-library-search.png', libraryCard(doc.name))
  pass('整理界面展示当前已保存版本和原文位置；完成整理不生成版本，文档库正文搜索与状态、标签、收藏过滤一致')

  const ranked = await librarySearch(`${originalTerm} 检查记录`)
  const match = ranked.documents[0].search_match
  assert.equal(match.mode, 'lexical')
  assert.equal(match.relevance.matched_term_count, 2)
  assert(match.passages.some(item => item.source_kind === 'content' && item.location.line))
  const hitCard = libraryCard(doc.name)
  const hit = hitCard.getByTestId('search-match-excerpt')
  await hit.getByText('原文命中', { exact: true }).waitFor()
  assert((await hit.innerText()).includes(originalTerm))
  assert.equal(await hit.locator('img').count(), 0, '命中摘录不能被解释为 HTML')
  assert.equal(await hitCard.locator('.doc-info [data-testid="search-match-excerpt"]').count(), 1)
  assert((await hitCard.locator('.doc-name').boundingBox()).width > 100, '命中说明不应挤压资料名称')
  await capture('index-ranked-match.png', hit)
  const reset = page.waitForResponse(response => response.url() === api + '/api/documents/search'
    && !response.request().postDataJSON()?.keyword
    && response.request().postDataJSON()?.sort_by === 'name'
    && response.request().postDataJSON()?.sort_order === 'asc')
  const input = page.locator('.smart-search-bar .inner-input')
  await input.fill(''); await input.press('Enter'); await reset
  await librarySearch(originalTerm)
  pass('多关键词按相关程度命中原文并展示位置；摘录按纯文本显示，名称布局正常，清词恢复原浏览排序')

  await libraryCard(doc.name).click()
  const detailCard = detailDialog().getByTestId('document-index-card')
  await detailCard.getByText('已建立', { exact: true }).waitFor()
  await detailCard.locator('summary').filter({ hasText: '原文摘录补充工具' }).click()
  await detailCard.getByTestId('index-ai-open').click()
  await field('index-ai-source').waitFor()
  const preview = await field('index-ai-source').inputValue()
  assert(preview.includes(originalTerm)); assert(preview.includes('location'))
  assert(await detailCard.getByTestId('index-ai-enrich').isDisabled())
  assert.equal(providerCalls.length, 0)
  await capture('index-consent.png', detailCard.getByTestId('index-ai-consent'))
  const beforeDoc = await document(doc.id), beforeVersions = await versions(doc.id)
  await detailCard.getByTestId('index-ai-consent').click()
  assert(await detailCard.getByTestId('index-ai-consent').getByRole('checkbox').isChecked())
  await detailCard.getByTestId('index-ai-enrich').click()
  await detailCard.getByText('AI 检索线索已保存', { exact: true }).waitFor()
  await detailCard.getByText(clueTerm, { exact: true }).waitFor()
  assert.equal(providerCalls.length, 1)
  assert.deepEqual(providerCalls[0], { filename: doc.name, text: preview })
  assert.deepEqual(await document(doc.id), beforeDoc)
  assert.deepEqual(await versions(doc.id), beforeVersions)
  assert.equal((await index(doc.id)).ai.clues[0].terms[0], clueTerm)
  await capture('index-ai-saved.png', detailCard)
  await detailDialog().locator('.el-drawer__close-btn').click()
  await librarySearch(clueTerm)
  await libraryCard(doc.name).waitFor()
  await libraryCard(doc.name).getByTestId('search-match-excerpt').getByText('AI 检索线索', { exact: true }).waitFor()
  pass('详情中明确同意后仅发送展示的预览；AI 线索持久化可搜索，资料元数据、人工标签和版本不变')

  await json(`/api/documents/${doc.id}`, { method: 'PUT', body: { description: '调整分类背景，确认旧推断失效。' } })
  assert.equal((await search(clueTerm, { status: 'organized' })).total, 0, '整理元数据改变后旧 AI 推断不可继续匹配')
  assert.equal((await index(doc.id)).ai, null)
  await librarySearch(originalTerm)
  await libraryCard(doc.name).waitFor()
  pass('资料元数据变化后撤下旧 AI 线索，保留同一已保存版本的正文搜索')

  const workingPath = path.resolve(library, organized.storage_path)
  assert(workingPath.startsWith(library + path.sep), '只修改临时资料库工作副本')
  fs.writeFileSync(workingPath, `修订后的综合运维资料\n${updatedTerm}：本次更新正文。\n`)
  assert.equal((await search(updatedTerm, { status: 'organized' })).total, 0)
  assert.equal((await search(originalTerm, { status: 'organized' })).total, 1, '未保存修改不应混入已保存版本索引')
  const v2 = await json(`/api/documents/${doc.id}/versions`, { method: 'POST', body: { note: '内容索引验收第二版' } })
  assert.notEqual(v2.id, v1)
  await waitIndex(doc.id, v2.id)
  assert.equal((await search(originalTerm, { status: 'organized' })).total, 0)
  await librarySearch(updatedTerm)
  await libraryCard(doc.name).click()
  await detailDialog().getByTestId('document-index-card').getByText('已建立', { exact: true }).waitFor()
  assert((await detailDialog().getByTestId('document-index-card').innerText()).includes('v2'))
  await capture('index-version-two.png', detailDialog().getByTestId('document-index-card'))
  await detailDialog().locator('.el-drawer__close-btn').click()
  const restored = await json(`/api/documents/${doc.id}/versions/${v1}/restore`, { method: 'POST', body: {} })
  assert.equal(restored.id, v1)
  await waitIndex(doc.id, v1)
  assert.equal((await search(updatedTerm, { status: 'organized' })).total, 0)
  await librarySearch(originalTerm)
  await libraryCard(doc.name).click()
  await detailDialog().getByTestId('document-index-card').getByText('已建立', { exact: true }).waitFor()
  assert((await detailDialog().getByTestId('document-index-card').innerText()).includes('v1'))
  assert.equal((await versions(doc.id)).length, 2)
  assert(fs.readFileSync(workingPath, 'utf8').includes(originalTerm))
  await capture('index-restored.png', detailDialog().getByTestId('document-index-card'))
  pass('工作副本未保存修改不污染检索；保存 v2 后旧正文消失，恢复 v1 后原正文重新可搜且仍只有两个版本')

  assert.equal(providerCalls.length, 1, '版本更新和恢复只触发本地索引')
  assert.deepEqual(pageErrors, []); assert.deepEqual(externalRequests, [])
  assert(!fs.readFileSync(path.join(temp, 'personal.log'), 'utf8').includes(testKey))
  pass('真实 Electron file 页面无运行异常；渲染器无外部请求，版本变化零 AI 调用，日志不含测试密钥')
  await context.tracing.stop({ path: path.join(temp, 'desktop-trace.zip') }); tracing = false
}
function report(status, error) {
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  const data = { status, groups: results, providerRequests: providerCalls.length, pageErrors, externalRequests, durationSeconds: (Date.now() - started) / 1000, ...(error ? { error: error.stack } : {}) }
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify(data, null, 2))
  fs.writeFileSync(path.join(temp, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="content-index-desktop" tests="${results.length + (error ? 1 : 0)}" failures="${error ? 1 : 0}">${results.map(name => `<testcase name="${escape(name)}"/>`).join('')}${error ? `<testcase name="content index"><failure>${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  const artifacts = fs.readdirSync(temp).filter(name => /\.(png|zip)$/.test(name))
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>内容索引验收</title><h1>${escape(status)}</h1><ul>${results.map(name => `<li>${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p>仅合成资料、临时个人库和本机假 AI。未调用真实模型或操作用户资料。</p><p>${artifacts.map(name => `<a href="${escape(name)}">${escape(name)}</a>`).join(' · ')}</p></html>`)
  console.log('验收产物：' + temp)
}
main().then(() => report('passed'), async error => {
  console.error(error)
  if (page) await page.screenshot({ path: path.join(temp, 'failure.png'), fullPage: true }).catch(() => {})
  if (tracing) await context.tracing.stop({ path: path.join(temp, 'desktop-failure-trace.zip') }).catch(() => {})
  report('failed', error); process.exitCode = 1
}).finally(async () => {
  if (electron) await electron.close().catch(() => {})
  for (const child of children.reverse()) if (child.exitCode === null) child.kill('SIGTERM')
  if (provider) { provider.closeAllConnections(); await new Promise(resolve => provider.close(resolve)) }
})
