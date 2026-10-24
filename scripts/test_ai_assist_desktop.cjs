#!/usr/bin/env node
// 真个人后端、生产 Electron file 页面、本机假 AI。绝不读取真实密钥或默认资料库。
// TEAMHUB_AI_BACKEND_BINARY 可指定本项目 dist 中的冻结后端，供最终交付验收。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, execFileSync } = require('node:child_process')
const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-ai-assist-'))
const library = path.join(temp, 'library-a'), secondLibrary = path.join(temp, 'library-b')
const testKey = 'sk-teamhub-synthetic-e2e-only-not-a-real-key'
const capability = crypto.randomBytes(32).toString('hex')
const model = 'teamhub-ai-e2e'
const results = [], providerCalls = [], pageErrors = [], externalRequests = [], children = []
const started = Date.now()
let provider, providerBase, api, electron, page, context, tracing = false, nextMode = 'ok', held, releaseHeld
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { const value = await check(); if (value) return value; await delay(50) }
  throw new Error('等待超时：' + label)
}
const freePort = () => new Promise((resolve, reject) => {
  const listener = net.createServer().once('error', reject)
  listener.listen(0, '127.0.0.1', () => { const port = listener.address().port; listener.close(() => resolve(port)) })
})
function pass(name) { results.push(name); console.log('PASS ' + name) }
async function request(route, { method = 'GET', body, ai = false } = {}) {
  return fetch(api + route, { method, headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(ai ? { 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': capability } : {}) }, body: body ? JSON.stringify(body) : undefined })
}
async function json(route, options) {
  const response = await request(route, options)
  assert.equal(response.status, 200, `${route}: ${await response.clone().text()}`)
  return response.json()
}
const aiCalls = () => providerCalls.filter(call => call.document).length
const hashes = docs => Object.fromEntries(docs.map(doc => {
  // 本脚本使用默认嵌套待整理区，其 storage_path 保持兼容的文件库相对路径。
  const file = path.join(library, doc.storage_path)
  assert(file.startsWith(temp + path.sep))
  return [doc.id, crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex')]
}))
async function documentState(doc) {
  return { document: await json('/api/documents/' + doc.id), versions: await json(`/api/documents/${doc.id}/versions`) }
}
async function startProvider() {
  provider = http.createServer(async (req, res) => {
    let bytes = ''
    for await (const chunk of req) { bytes += chunk; if (bytes.length > 200000) { res.writeHead(413); res.end(); return } }
    if (req.url !== '/v1/chat/completions' || req.method !== 'POST') { res.writeHead(404); res.end(); return }
    try {
      assert.equal(req.headers.authorization, 'Bearer ' + testKey)
      const body = JSON.parse(bytes)
      assert.equal(body.model, model)
      assert.equal(body.response_format?.type, 'json_object')
      assert.equal(body.stream, false)
      const user = body.messages.find(message => message.role === 'user')?.content
      let document
      try { document = JSON.parse(user) } catch { assert(user.includes('连接测试')) }
      if (document) assert.deepEqual(Object.keys(document).sort(), ['filename', 'text'])
      const mode = nextMode; nextMode = 'ok'
      providerCalls.push({ mode, document, messageCount: body.messages.length })
      const reply = () => {
        if (res.destroyed) return
        if (mode === '401' || mode === '429') { res.writeHead(Number(mode), { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: { message: 'upstream must not be echoed ' + testKey } })); return }
        const value = mode === 'malformed' ? '{not-json' : JSON.stringify({ summary: document ? `本资料包含 ${document.filename} 的设备试验记录。` : '连接成功', tags: document ? ['变压器', '试验报告', '设备管理'] : [] })
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: value } }] }))
      }
      if (mode === 'hold') { held = document; releaseHeld = () => { held = null; releaseHeld = null; reply() } } else reply()
    } catch (error) { pageErrors.push('fake provider contract: ' + error.message); res.writeHead(500); res.end('{}') }
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
  const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'zh_CN.UTF-8', TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true', TEAMHUB_AI_CAPABILITY: capability, USER_DATA_DIR: path.join(temp, 'backend-profile'), TEAMHUB_AI_CONFIG_DIR: path.join(temp, 'ai-config'), LIBRARY_PATH: library, PORT: new URL(api).port, CLOUD_API_URL: providerBase, TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false', PYTHONPATH: repo }
  const logFile = path.join(temp, 'personal.log'), fd = fs.openSync(logFile, 'w')
  const child = spawn(binary || path.join(repo, 'venv/bin/python'), binary ? [] : ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port], { cwd: temp, env, stdio: ['ignore', fd, fd] })
  children.push(child); fs.closeSync(fd)
  await until(async () => { if (child.exitCode !== null) throw new Error('个人后端退出：' + fs.readFileSync(logFile, 'utf8')); try { return (await fetch(api + '/api/health')).ok } catch { return false } }, '个人后端就绪')
  assert.equal((await json('/api/settings/library')).path, library)
  console.log('个人后端：' + (binary || '隔离 venv uvicorn'))
}
function fixtures() {
  const source = path.join(temp, 'sources'); fs.mkdirSync(source)
  const text = path.join(source, 'AI验收变压器试验.txt')
  fs.writeFileSync(text, 'AI-TXT-MARKER\nA 项目变压器试验记录，绝缘电阻测量结果已登记。\n')
  execFileSync(path.join(repo, 'venv/bin/python'), ['-c', `
import sys, zipfile
from pathlib import Path
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject
p=Path(sys.argv[1])
with zipfile.ZipFile(p/'AI验收检修说明.docx','w') as z:
 z.writestr('[Content_Types].xml','<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/></Types>')
 z.writestr('word/document.xml','<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>AI-DOCX-MARKER 设备检修说明和试验注意事项</w:t></w:r></w:p></w:body></w:document>')
w=PdfWriter();page=w.add_blank_page(600,800)
font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):w._add_object(font)})})
s=DecodedStreamObject();s.set_data(b'BT /F1 16 Tf 50 700 Td (AI-PDF-MARKER transformer test report) Tj ET')
page[NameObject('/Contents')]=w._add_object(s)
with (p/'AI验收文字报告.pdf').open('wb') as f:w.write(f)
`, source], { cwd: temp, encoding: 'utf8', timeout: 20000 })
  return fs.readdirSync(source).map(name => path.join(source, name)).sort()
}
function input(testId) {
  const root = `[data-testid=${JSON.stringify(testId)}]`
  return page.locator(`${root}:is(input,textarea),${root} input,${root} textarea`)
}
const visibleDialog = () => page.getByRole('dialog', { name: 'AI 辅助整理', exact: true })
async function openOrganize(doc) {
  const card = page.locator('.pending-card').filter({ has: page.getByText(doc.name, { exact: true }) })
  await card.getByTestId('manual-organize-' + doc.id).click()
  await page.getByTestId('pending-ai-assist').click()
  await input('ai-source-text').waitFor()
}
async function closeAI() {
  await visibleDialog().getByRole('button', { name: /^(关闭|取消等待并关闭)$/ }).click()
  await page.getByTestId('ai-assist-dialog').waitFor({ state: 'hidden' })
}
async function closeOrganize() {
  await page.getByRole('dialog', { name: '整理资料', exact: true }).getByRole('button', { name: '取消', exact: true }).click()
}
async function analyze() {
  await page.getByTestId('ai-send-consent').click()
  assert(await page.getByTestId('ai-send-consent').getByRole('checkbox').isChecked())
  await page.getByTestId('ai-analyze').click()
}
async function waitResult() { await page.getByTestId('ai-result').waitFor() }
async function capture(filename, target) {
  if (target) await target.scrollIntoViewIfNeeded()
  await page.evaluate(async () => {
    await Promise.all(document.getAnimations().filter(animation => Number.isFinite(animation.effect?.getComputedTiming().iterations)).map(animation => animation.finished.catch(() => {})))
    await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
  })
  await page.screenshot({ path: path.join(temp, filename), fullPage: true })
}
async function enterPendingAfterReload() {
  await page.reload()
  await until(() => page.evaluate(() => !!document.querySelector('.pending-page') || [...document.querySelectorAll('button')].some(button => button.textContent.trim() === '打开文件库')), '切库后的欢迎页或待整理页')
  const enter = page.getByRole('button', { name: '打开文件库', exact: true })
  if (await enter.count()) await enter.click()
  await page.locator('.pending-page').waitFor()
}
async function main() {
  await startProvider(); await startBackend()
  const untrusted = await fetch(api + '/api/ai/settings', { headers: { Origin: 'null', 'X-TeamHub-AI': '1' } })
  assert.equal(untrusted.status, 403, '仅伪造 file Origin 而没有桌面 capability 不得调用 AI')
  const modulePath = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
  const { _electron } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(modulePath) ? modulePath : 'playwright'))
  const fixture = path.join(temp, 'fixture.json'); fs.writeFileSync(fixture, JSON.stringify({ repo, temp, api, capability }), { mode: 0o600 })
  const env = { ...process.env, TEAMHUB_AI_FIXTURE: fixture }; delete env.ELECTRON_RUN_AS_NODE
  electron = await _electron.launch({ executablePath: path.join(repo, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), args: [path.join(repo, 'scripts/test_ai_assist_desktop_app.cjs')], env })
  page = await electron.firstWindow(); page.setDefaultTimeout(15000)
  context = electron.context(); await context.tracing.start({ screenshots: true, snapshots: true, sources: true }); tracing = true
  page.on('pageerror', error => pageErrors.push(error.message))
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== api) { externalRequests.push(url.href); return route.abort() }
    return route.continue()
  })
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/settings')
  if (await page.getByRole('button', { name: '打开文件库', exact: true }).count()) await page.getByRole('button', { name: '打开文件库', exact: true }).click()
  await input('ai-base-url').fill(providerBase); await input('ai-model').fill(model); await input('ai-api-key').fill(testKey)
  const saved = page.waitForResponse(response => response.url() === api + '/api/ai/settings' && response.request().method() === 'PUT')
  await page.getByTestId('ai-settings-save').click(); const saveResponse = await saved
  assert.equal(saveResponse.status(), 200, await saveResponse.text())
  const saveBody = await saveResponse.json()
  assert.equal(saveBody.has_api_key, true); assert(!JSON.stringify(saveBody).includes(testKey))
  await until(async () => (await input('ai-api-key').inputValue()) === '', '保存后清除密钥输入')
  assert(!(await page.evaluate(() => JSON.stringify({ ...localStorage }))).includes(testKey), '密钥不得进入 localStorage')
  assert(!fs.readFileSync(path.join(temp, 'ai-config/ai.json'), 'utf8').includes(testKey), 'AI配置不得存明文密钥')
  assert(!fs.readFileSync(path.join(library, '.teamhub/config.json'), 'utf8').includes(testKey), '资料库不得存 AI 密钥')
  await page.getByTestId('ai-settings-test').click()
  await page.locator('.ai-test-result').filter({ hasText: '连接成功' }).waitFor()
  assert.equal(providerCalls.length, 1); assert.equal(aiCalls(), 0)
  await capture('ai-settings.png', page.getByTestId('ai-settings-test'))
  pass('设置保存不回显密钥、不写浏览器或资料库，连接测试仅发送短测试消息')

  await page.locator('.nav-primary .nav-item').filter({ hasText: '待整理' }).click()
  for (const source of fixtures()) {
    await page.getByRole('button', { name: '收录本机文件', exact: true }).click()
    await page.getByPlaceholder('请输入本机文件或文件夹的完整路径').fill(source)
    const ingested = page.waitForResponse(response => response.url().endsWith('/api/documents/quick-add') && response.request().method() === 'POST')
    await page.getByRole('button', { name: '移动到待整理', exact: true }).click()
    assert.equal((await ingested).status(), 200)
    await page.getByRole('dialog', { name: '收录本机文件', exact: true }).waitFor({ state: 'hidden' })
    assert(!fs.existsSync(source), '收录必须保持已有移动语义')
    const detail = page.getByRole('dialog', { name: '文档详情', exact: true })
    await detail.waitFor()
    await detail.locator('.el-drawer__close-btn').click()
    await detail.waitFor({ state: 'hidden' })
  }
  const docs = (await json('/api/documents/search', { method: 'POST', body: { status: 'pending' } })).documents
  assert.equal(docs.length, 3)
  const text = docs.find(doc => doc.name.endsWith('.txt')), word = docs.find(doc => doc.name.endsWith('.docx')), pdf = docs.find(doc => doc.name.endsWith('.pdf'))
  const originalStates = Object.fromEntries(await Promise.all(docs.map(async doc => [doc.id, await documentState(doc)])))
  const originalHashes = hashes(docs)
  for (const [doc, marker] of [[word, 'AI-DOCX-MARKER'], [pdf, 'AI-PDF-MARKER']]) {
    await openOrganize(doc); assert((await input('ai-source-text').inputValue()).includes(marker))
    assert(await page.getByTestId('ai-analyze').isDisabled()); assert.equal(aiCalls(), 0)
    await closeAI(); await closeOrganize()
  }
  await openOrganize(text)
  assert((await input('ai-source-text').inputValue()).includes('AI-TXT-MARKER'))
  assert(await page.getByTestId('ai-analyze').isDisabled()); assert.equal(aiCalls(), 0)
  await capture('ai-consent.png', page.getByTestId('ai-analyze'))
  pass('通过既有收录流程移动 TXT、DOCX、文字 PDF；本机摘录和未同意状态均不调用 AI')

  await analyze(); await waitResult()
  assert.equal(aiCalls(), 1)
  const sent = providerCalls.find(call => call.document).document
  assert.equal(sent.filename, text.name); assert(sent.text.includes('AI-TXT-MARKER'))
  assert((await page.getByTestId('ai-summary').innerText()).includes(text.name))
  assert.equal(await page.locator('.ai-tag-choices input:checked').count(), 0)
  assert(await page.getByTestId('ai-apply-tags').isDisabled())
  await visibleDialog().getByText('变压器', { exact: true }).click()
  await visibleDialog().getByText('试验报告', { exact: true }).click()
  await capture('ai-suggestions.png', page.getByTestId('ai-apply-tags'))
  await page.getByTestId('ai-apply-tags').click()
  assert.deepEqual(await documentState(text), originalStates[text.id], '填入表单不能写数据库')
  const organize = page.getByRole('dialog', { name: '整理资料', exact: true })
  assert((await organize.locator('.organize-tags').innerText()).includes('变压器'))
  assert(!(await organize.locator('.organize-tags').innerText()).includes('设备管理'))
  await organize.getByRole('button', { name: '完成整理', exact: true }).click()
  await organize.waitFor({ state: 'hidden' })
  const organized = await documentState(text)
  assert.equal(organized.document.status, 'organized')
  assert(organized.document.tags.some(tag => tag.name === '变压器')); assert(organized.document.tags.some(tag => tag.name === '试验报告'))
  assert(!organized.document.tags.some(tag => tag.name === '设备管理'))
  assert.deepEqual(organized.versions, originalStates[text.id].versions)
  assert.equal(organized.document.description, originalStates[text.id].document.description)
  pass('确认后个人后端调用假 AI；建议默认不选，仅所选标签进入表单，原整理保存后生效且版本不变')

  await page.locator('.nav-primary .nav-item').filter({ hasText: '文档库' }).click()
  await page.locator('.doc-card').filter({ has: page.getByText(text.name, { exact: true }) }).click()
  await page.getByTestId('detail-ai-assist').click(); await input('ai-source-text').waitFor()
  await analyze(); await waitResult()
  await visibleDialog().getByText('设备管理', { exact: true }).click()
  await page.getByTestId('ai-apply-tags').click()
  assert.deepEqual(await documentState(text), organized, '详情中的填入动作不能隐式保存')
  const drawer = page.getByRole('dialog', { name: '文档详情', exact: true })
  assert((await drawer.locator('.tag-edit-row').innerText()).includes('变压器')); assert((await drawer.locator('.tag-edit-row').innerText()).includes('设备管理'))
  await drawer.getByRole('button', { name: '保存标签', exact: true }).click()
  await until(async () => (await json('/api/documents/' + text.id)).tags.some(tag => tag.name === '设备管理'), '详情保存标签')
  const detailSaved = await documentState(text)
  assert.equal(detailSaved.document.description, organized.document.description)
  assert.deepEqual(detailSaved.versions, organized.versions)
  await capture('ai-detail-saved.png', page.getByTestId('detail-ai-assist'))
  await drawer.locator('.el-drawer__close-btn').click()
  pass('详情保留原标签，AI 标签仍需主动保存；摘要不覆盖描述、文件或历史版本')

  await page.locator('.nav-primary .nav-item').filter({ hasText: '待整理' }).click()
  await openOrganize(word); nextMode = 'hold'; await analyze()
  await until(() => held, '假 AI 收到延迟请求')
  const busySwitch = await request('/api/settings/library', { method: 'POST', body: { path: secondLibrary } })
  assert.equal(busySwitch.status, 409, '在飞分析持有个人库会话期间不能切库')
  assert.equal((await json('/api/settings/library')).path, library)
  await closeAI(); await closeOrganize(); await openOrganize(pdf)
  assert.equal(await page.getByTestId('ai-result').count(), 0)
  assert.equal(await page.getByTestId('ai-send-consent').getByRole('checkbox').isChecked(), false)
  releaseHeld()
  await until(async () => (await json('/api/settings/library')).path === library, '延迟请求释放')
  await page.waitForLoadState('networkidle')
  assert.equal(await page.getByTestId('ai-result').count(), 0, '已关闭资料结果不能落入另一资料')
  assert((await input('ai-source-text').inputValue()).includes('AI-PDF-MARKER'))
  await closeAI(); await closeOrganize()
  pass('关闭分析后切换另一资料，迟到响应不串入新弹窗，重新打开需重新同意')

  for (const [mode, message] of [['401', '密钥无效'], ['429', '请求较多'], ['malformed', '格式不正确']]) {
    await openOrganize(word); const before = await documentState(word); const count = aiCalls()
    nextMode = mode; await analyze()
    await visibleDialog().getByText(new RegExp(message)).waitFor()
    assert.equal(aiCalls(), count + 1, '上游失败不能自动重试收费')
    assert(!(await visibleDialog().innerText()).includes(testKey), '上游原始消息不得暴露')
    assert.equal(await page.getByTestId('ai-result').count(), 0)
    assert.deepEqual(await documentState(word), before)
    await visibleDialog().getByRole('button', { name: '重新读取资料', exact: true }).click()
    await input('ai-source-text').waitFor(); assert(await page.getByTestId('ai-analyze').isDisabled())
    await analyze(); await waitResult()
    assert.deepEqual(await documentState(word), before, '恢复成功仍只读建议')
    await closeAI(); await closeOrganize()
  }
  pass('上游鉴权失败、限流、无效 JSON 均有安全提示、无自动重试；重新读取确认后恢复且资料不变')

  const prepared = await json(`/api/ai/documents/${word.id}/prepare`, { method: 'POST', body: {}, ai: true })
  const count = aiCalls()
  await json('/api/settings/library', { method: 'POST', body: { path: secondLibrary } })
  const stale = await request('/api/ai/analyze', { method: 'POST', body: { preparation_id: prepared.preparation_id, consent: true }, ai: true })
  assert.equal(stale.status, 409); assert.equal(aiCalls(), count, '切库后旧准备不得外发')
  await enterPendingAfterReload()
  assert.equal(await page.getByTestId('ai-assist-dialog').count(), 0)
  assert.equal((await json('/api/documents/search', { method: 'POST', body: {} })).total, 0)
  await json('/api/settings/library', { method: 'POST', body: { path: library } })
  await enterPendingAfterReload()
  assert.deepEqual(await documentState(word), originalStates[word.id]); assert.deepEqual(await documentState(pdf), originalStates[pdf.id])
  const currentDocs = await Promise.all(docs.map(doc => json('/api/documents/' + doc.id)))
  assert.deepEqual(hashes(currentDocs), originalHashes)
  for (const doc of docs) assert.deepEqual((await documentState(doc)).versions, originalStates[doc.id].versions)
  assert.deepEqual(pageErrors, []); assert.deepEqual(externalRequests, [])
  assert(!fs.readFileSync(path.join(temp, 'personal.log'), 'utf8').includes(testKey))
  pass('切换临时个人库阻止旧准备外发；所有工作文件哈希和版本保持一致，页面无异常且渲染器无外部请求')
  await context.tracing.stop({ path: path.join(temp, 'desktop-trace.zip') }); tracing = false
}
function report(status, error) {
  const escape = text => String(text).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  const data = { status, groups: results, providerRequests: providerCalls.map(call => ({ mode: call.mode, kind: call.document ? 'analysis' : 'connection', filename: call.document?.filename })), pageErrors, externalRequests, durationSeconds: (Date.now() - started) / 1000, ...(error ? { error: error.stack } : {}) }
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify(data, null, 2))
  fs.writeFileSync(path.join(temp, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="ai-assist-desktop" tests="${results.length + (error ? 1 : 0)}" failures="${error ? 1 : 0}">${results.map(name => `<testcase name="${escape(name)}"/>`).join('')}${error ? `<testcase name="AI assist"><failure>${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AI 辅助整理验收</title><h1>${escape(status)}</h1><ul>${results.map(name => `<li>${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p>仅使用合成资料、临时个人库及本机假 AI 服务。截图和 trace 只含无效测试密钥，未使用真实服务或账号。</p><p><a href="ai-settings.png">设置</a> · <a href="ai-consent.png">内容确认</a> · <a href="ai-suggestions.png">标签建议</a> · <a href="ai-detail-saved.png">详情保存</a> · <a href="desktop-trace.zip">trace</a></p></html>`)
  console.log('验收产物：' + temp)
}
main().then(() => report('passed'), async error => {
  console.error(error)
  if (page) await page.screenshot({ path: path.join(temp, 'failure.png'), fullPage: true }).catch(() => {})
  if (tracing) await context.tracing.stop({ path: path.join(temp, 'desktop-failure-trace.zip') }).catch(() => {})
  report('failed', error); process.exitCode = 1
}).finally(async () => {
  if (releaseHeld) releaseHeld()
  if (electron) await electron.close().catch(() => {})
  for (const child of children.reverse()) if (child.exitCode === null) child.kill('SIGTERM')
  if (provider) { provider.closeAllConnections(); await new Promise(resolve => provider.close(resolve)) }
})
