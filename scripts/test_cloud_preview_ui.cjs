#!/usr/bin/env node
// 在线预览真实浏览器与 Electron 验收：随机端口、合成账号和临时库，绝不连接日常服务。
// PLAYWRIGHT_MODULE / PLAYWRIGHT_CHROMIUM_EXECUTABLE 可指定已有 Playwright 和 Chrome。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, execFileSync } = require('node:child_process')
const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-cloud-preview-ui-'))
const python = path.join(repo, 'cloud_backend/venv/bin/python')
const password = crypto.randomBytes(24).toString('hex')
const chromeModule = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
const { chromium } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(chromeModule) ? chromeModule : 'playwright'))
const env = { ...process.env, TEAMHUB_SANDBOX: '1', DATABASE_URL: `sqlite:///${temp}/cloud.db`,
  STORAGE_DIR: path.join(temp, 'objects'), PYTHONPATH: path.join(repo, 'cloud_backend'),
  JWT_SECRET_KEY: crypto.randomBytes(40).toString('hex'), TEAMHUB_TEST_PASSWORD: password,
  CLOUD_WEB_DIST: path.join(repo, 'frontend/dist'), PREVIEW_FIXTURE_DIR: temp }
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label, timeout = 20000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) { if (await check()) return; await delay(80) }
  throw new Error(`等待超时：${label}`)
}
const port = () => new Promise((resolve, reject) => {
  const listener = net.createServer().once('error', reject)
  listener.listen(0, '127.0.0.1', () => { const value = listener.address().port; listener.close(() => resolve(value)) })
})
const observed = promise => { promise.catch(() => {}); return promise }
let service, browser, traceContext
const pages = [], failures = [], results = [], unexpectedRequests = []
const startedAt = Date.now()
function report(name) { results.push(name); console.log('PASS ' + name) }
function saveResults(status, error) {
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  const value = { status, groups: results, durationSeconds: (Date.now() - startedAt) / 1000, externalRequests: unexpectedRequests, browserErrors: failures, ...(error ? { error: error.stack } : {}) }
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify(value, null, 2))
  const failed = error ? 1 : 0
  fs.writeFileSync(path.join(temp, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="Cloud preview" tests="${results.length + failed}" failures="${failed}" time="${value.durationSeconds}">${results.map(name => `<testcase name="${escape(name)}"/>`).join('')}${error ? `<testcase name="未完成预览验收"><failure message="${escape(error.message)}">${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>在线预览验收</title><body><h1>在线预览验收：${escape(status)}</h1><p>${value.durationSeconds.toFixed(1)} 秒</p><ul>${results.map(name => `<li>通过：${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p><a href="web-trace.zip">浏览器 trace</a> · <a href="web-pdf.png">PDF 截图</a> · <a href="web-docx.png">Word 截图</a> · <a href="web-xlsx.png">Excel 截图</a> · <a href="desktop-pdf.png">桌面 PDF 截图</a> · <a href="desktop-xlsx.png">桌面 Excel 截图</a></p></body></html>`)
}

const seed = JSON.parse(execFileSync(python, ['-c', `
import json, os, pathlib, struct, zlib, zipfile
from app.database import init_db, SessionLocal
from app.models import User, Team, TeamMember, Workspace
from app.core.security import hash_password
init_db()
with SessionLocal() as db:
    users = {}; digest = hash_password(os.environ['TEAMHUB_TEST_PASSWORD'])
    for key in ('a', 'b', 'c', 'm'):
        user = User(email=key+'@preview.example', display_name='预览成员'+key, hashed_password=digest, is_superuser=key=='m')
        db.add(user); db.flush(); users[key] = user
    team = Team(name='预览验收组织', slug='preview-test'); db.add(team); db.flush()
    for key, user in users.items(): db.add(TeamMember(team_id=team.id, user_id=user.id, role='admin' if key=='m' else 'member'))
    ws = Workspace(team_id=team.id, name='预览资料'); other = Workspace(team_id=team.id, name='另一空间')
    db.add_all([ws, other]); db.commit()
    result = {'workspace': ws.id, 'other_workspace': other.id, 'users': {key: {'id':user.id, 'email':user.email} for key,user in users.items()}}
p = pathlib.Path(os.environ['PREVIEW_FIXTURE_DIR'])
def package(name, files):
    with zipfile.ZipFile(p/name, 'w', zipfile.ZIP_DEFLATED) as z:
        for key, value in files.items(): z.writestr(key, value)
package('工程说明.docx', {
 '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
 '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>',
 'word/document.xml':'<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>设备检修说明正文</w:t></w:r></w:p><w:p><w:r><w:t>&lt;img src="https://preview-untrusted.invalid/word" /&gt;</w:t></w:r></w:p><w:tbl><w:tr><w:tc><w:p><w:r><w:t>设备名称</w:t></w:r></w:p></w:tc><w:tc><w:p><w:r><w:t>断路器</w:t></w:r></w:p></w:tc></w:tr></w:tbl><w:sectPr/></w:body></w:document>'})
package('设备清册.xlsx', {
 '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/><Override PartName="/xl/worksheets/sheet2.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
 '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
 'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="设备" sheetId="1" r:id="rId1"/><sheet name="检修" sheetId="2" r:id="rId2"/></sheets></workbook>',
 'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet2.xml"/></Relationships>',
 'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>断路器清册</t></is></c><c r="B1"><v>12</v></c></row><row r="2"><c r="A2" t="inlineStr"><is><t>&lt;a href="https://preview-untrusted.invalid/xlsx"&gt;外链文本&lt;/a&gt;</t></is></c><c r="B2"><f>1+2</f><v>3</v></c></row></sheetData></worksheet>',
 'xl/worksheets/sheet2.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>检修计划第二张表</t></is></c></row></sheetData></worksheet>'})
def chunk(kind, data): return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
png = b'\\x89PNG\\r\\n\\x1a\\n'+chunk(b'IHDR',struct.pack('!2I5B',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b'\\x00\\xff\\x00\\x00\\x00\\xff\\x00\\x00\\x00\\x00\\xff\\xff\\xff\\x00'))+chunk(b'IEND',b'')
(p/'设备图片.png').write_bytes(png)
(p/'说明.txt').write_text('当前文本第二版\\n<img src="https://preview-untrusted.invalid/text">\\n<script>window.__previewUnsafe=1</script>', encoding='utf-8')
(p/'资料包.zip').write_bytes(b'Unsupported archive preview fixture')
print(json.dumps(result, ensure_ascii=False))
`], { cwd: temp, env, encoding: 'utf8', timeout: 30000 }).trim())

function pdf() {
  const streams = ['1 0 0 rg 20 20 100 100 re f BT /F1 20 Tf 20 160 Td (Page One) Tj ET',
    '0 0 1 rg 20 20 140 80 re f BT /F1 20 Tf 20 160 Td (Page Two) Tj ET']
  const objects = ['<< /Type /Catalog /Pages 2 0 R >>', '<< /Type /Pages /Kids [3 0 R 4 0 R] /Count 2 >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 6 0 R >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 240 200] /Resources << /Font << /F1 5 0 R >> >> /Contents 7 0 R >>',
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>', ...streams.map(value => `<< /Length ${Buffer.byteLength(value)} >>\nstream\n${value}\nendstream`)]
  let body = '%PDF-1.4\n', offsets = [0]
  objects.forEach((value, i) => { offsets.push(Buffer.byteLength(body)); body += `${i + 1} 0 obj\n${value}\nendobj\n` })
  const xref = Buffer.byteLength(body)
  body += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n${offsets.slice(1).map(value => String(value).padStart(10, '0') + ' 00000 n ').join('\n')}\ntrailer\n<< /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`
  return Buffer.from(body)
}
fs.writeFileSync(path.join(temp, '两页图纸.pdf'), pdf())

async function main() {
  const origin = `http://127.0.0.1:${await port()}`
  const log = fs.openSync(path.join(temp, 'cloud.log'), 'w')
  service = spawn(python, ['-m', 'uvicorn', 'main:app', '--app-dir', path.join(repo, 'cloud_backend'), '--host', '127.0.0.1', '--port', new URL(origin).port], { cwd: temp, env, stdio: ['ignore', log, log] })
  fs.closeSync(log)
  await until(async () => {
    if (service.exitCode !== null) throw new Error('临时云端退出：' + fs.readFileSync(path.join(temp, 'cloud.log'), 'utf8'))
    try { return (await fetch(origin + '/api/health')).ok } catch { return false }
  }, '临时云端启动')
  const tokens = {}
  async function request(route, token, options = {}) {
    return fetch(origin + route, { ...options, headers: { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...options.headers } })
  }
  for (const key of Object.keys(seed.users)) {
    const response = await request('/api/auth/login', null, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ email: seed.users[key].email, password }) })
    assert.equal(response.status, 200); tokens[key] = (await response.json()).access_token
  }
  const base = `/api/workspaces/${seed.workspace}`
  async function upload(filename, data) {
    const response = await request(`${base}/uploads?filename=${encodeURIComponent(filename)}&purpose=publication`, tokens.a,
      { method: 'POST', headers: { 'Content-Type': 'application/octet-stream' }, body: data })
    assert.equal(response.status, 200, await response.clone().text()); return response.json()
  }
  const documents = {}
  for (const [key, filename] of Object.entries({ pdf: '两页图纸.pdf', image: '设备图片.png', document: '工程说明.docx', spreadsheet: '设备清册.xlsx', text: '说明.txt', unsupported: '资料包.zip' })) {
    const data = key === 'text' ? Buffer.from('历史文本第一版\n') : fs.readFileSync(path.join(temp, filename))
    const uploaded = await upload(filename, data)
    const response = await request(`${base}/publications`, tokens.a, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), name: filename, original_filename: filename,
      visibility: 'restricted', access: [{ user_id: seed.users.b.id, role: 'reader' }], force_new: true, confirmed: true,
      doc_type: key === 'unsupported' ? 'archive' : 'file', upload_id: uploaded.upload_id, content_hash: uploaded.sha256 }) })
    assert.equal(response.status, 200, await response.clone().text())
    const result = await response.json()
    assert.equal(result.status, 'succeeded'); documents[key] = { id: result.document_id, version: result.version_id || result.current_version_id, filename, data: data.toString('base64') }
    const detail = await (await request(`${base}/documents/${result.document_id}`, tokens.a)).json()
    documents[key].version = detail.current_version_id
  }
  const oldTextVersion = documents.text.version
  const currentText = fs.readFileSync(path.join(temp, '说明.txt'))
  const newUpload = await upload('说明.txt', currentText)
  const updated = await request(`${base}/documents/${documents.text.id}/versions`, tokens.a, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ request_id: crypto.randomUUID(), base_version_id: oldTextVersion,
    upload_id: newUpload.upload_id, content_hash: newUpload.sha256, original_filename: '说明.txt', confirmed: true }) })
  assert.equal(updated.status, 200, await updated.clone().text())
  const textDetail = await (await request(`${base}/documents/${documents.text.id}`, tokens.a)).json()
  documents.text.version = textDetail.current_version_id; documents.text.oldVersion = oldTextVersion
  for (const [key, doc] of Object.entries(documents)) {
    const preview = await request(`${base}/documents/${doc.id}/versions/${doc.version}/preview`, tokens.b)
    assert.equal(preview.status, 200, await preview.clone().text())
    assert.equal((await preview.json()).kind, key)
  }
  for (const key of ['c', 'm']) {
    for (const suffix of ['/preview', '/preview/content']) {
      const response = await request(`${base}/documents/${documents.pdf.id}/versions/${documents.pdf.version}${suffix}`, tokens[key])
      assert.equal(response.status, 404, key + ' 不得预览受限资料')
    }
  }
  for (const route of [ `${base}/documents/${documents.pdf.id}/versions/${documents.text.version}/preview`,
    `/api/workspaces/${seed.other_workspace}/documents/${documents.pdf.id}/versions/${documents.pdf.version}/preview` ]) {
    assert.equal((await request(route, tokens.b)).status, 404)
  }
  assert([401, 403].includes((await request(`${base}/documents/${documents.pdf.id}/versions/${documents.pdf.version}/preview`, null)).status), '未登录不得预览')
  report('API所有格式、受限管理员/未授权成员/跨资料/跨空间权限')

  browser = await chromium.launch({ headless: true, executablePath: process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' })
  const context = await browser.newContext({ viewport: { width: 1500, height: 1080 }, acceptDownloads: true })
  traceContext = context
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== origin) { unexpectedRequests.push(url.href); await route.abort(); return }
    await route.continue()
  })
  await context.addInitScript(() => {
    window.__previewAudit = { activeURLs: [], revoked: [], aborted: [] }
    const create = URL.createObjectURL.bind(URL), revoke = URL.revokeObjectURL.bind(URL)
    URL.createObjectURL = blob => { const value = create(blob); window.__previewAudit.activeURLs.push(value); return value }
    URL.revokeObjectURL = value => { window.__previewAudit.revoked.push(value); window.__previewAudit.activeURLs = window.__previewAudit.activeURLs.filter(url => url !== value); return revoke(value) }
    const fetchOriginal = window.fetch.bind(window)
    window.fetch = (input, options) => {
      if (String(input).includes('/preview') && options?.signal) options.signal.addEventListener('abort', () => window.__previewAudit.aborted.push(String(input)), { once: true })
      return fetchOriginal(input, options)
    }
  })
  const page = await context.newPage(); pages.push(page)
  page.on('pageerror', error => failures.push(error.message))
  async function login(key) {
    await page.getByLabel('邮箱', { exact: true }).fill(seed.users[key].email)
    await page.getByLabel('密码', { exact: true }).fill(password)
    await page.getByRole('button', { name: '登录', exact: true }).click()
    await page.getByTestId(`shared-library-entry-${seed.workspace}`).click()
    await page.getByRole('heading', { name: '组织资料', exact: true }).waitFor()
  }
  await page.goto(origin + '/cloud.html'); await login('b')
  const dialog = page.getByTestId('cloud-file-preview')
  async function capture(filename) {
    // DOM可见可能早于弹窗淡入完成；等待有限CSS动画，避免把半透明过渡态当作证据。
    await page.evaluate(async () => {
      await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))
      const dialog = [...document.querySelectorAll('[data-testid="cloud-file-preview"]')].find(node => node.getClientRects().length)
      const scope = []
      for (let node = dialog; node && node !== document.body; node = node.parentElement) scope.push(node)
      await Promise.all(scope.flatMap(node => node.getAnimations()).filter(animation => Number.isFinite(animation.effect?.getComputedTiming().iterations)).map(animation => animation.finished.catch(() => {})))
    })
    if (await dialog.isVisible()) await dialog.screenshot({ path: path.join(temp, filename) })
    else await page.screenshot({ path: path.join(temp, filename), fullPage: true })
  }
  async function open(key) {
    await page.getByTestId('preview-current-' + documents[key].id).click()
    await dialog.waitFor({ state: 'visible' })
    await dialog.getByTestId('cloud-preview-title').waitFor()
  }
  async function close() {
    await dialog.getByTestId('preview-close').click()
    await dialog.waitFor({ state: 'hidden' })
  }
  async function download(button, expected) {
    const promise = observed(page.waitForEvent('download'))
    await button.click(); const item = await promise
    const file = path.join(temp, crypto.randomUUID() + '-download')
    await item.saveAs(file); assert.deepEqual(fs.readFileSync(file), expected)
  }
  await open('text')
  await dialog.getByTestId('preview-text').waitFor()
  assert((await dialog.getByTestId('preview-text').textContent()).includes('当前文本第二版'))
  assert((await dialog.getByTestId('preview-text').textContent()).includes('<script>'))
  assert.equal(await page.evaluate(() => window.__previewUnsafe), undefined)
  assert.equal(await dialog.locator('script,img').count(), 0)
  await download(dialog.getByTestId('preview-download'), currentText)
  await close()
  await page.getByRole('button', { name: '说明.txt', exact: true }).click()
  await page.getByTestId('preview-version-' + oldTextVersion).click()
  await dialog.getByTestId('preview-text').waitFor()
  assert.equal((await dialog.getByTestId('preview-text').textContent()).trim(), '历史文本第一版')
  await download(dialog.getByTestId('preview-download'), Buffer.from('历史文本第一版\n'))
  await close()
  // 旧有下载入口仍需按所选历史版本返回原始文件。
  await download(page.getByRole('button', { name: '下载版本1', exact: true }), Buffer.from('历史文本第一版\n'))
  await page.getByRole('button', { name: '关闭此对话框', exact: true }).click()
  report('网页当前/历史文本、HTML转义、预览下载与已有历史下载')

  await open('pdf')
  await page.waitForFunction(() => {
    const canvas = document.querySelector('[data-testid="pdf-preview-canvas"]')
    if (!canvas || !canvas.width || !canvas.height) return false
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    return data.some((v, i) => i % 4 === 3 && v === 255 && data[i - 3] > 200 && data[i - 2] < 50 && data[i - 1] < 50)
  })
  const firstCanvas = await dialog.getByTestId('pdf-preview-canvas').evaluate(canvas => canvas.toDataURL())
  assert.equal((await dialog.getByTestId('pdf-page-status').textContent()).trim(), '第 1 / 2 页')
  await dialog.getByTestId('pdf-next').click()
  await page.waitForFunction(previous => {
    const canvas = document.querySelector('[data-testid="pdf-preview-canvas"]')
    if (!canvas || canvas.toDataURL() === previous) return false
    const data = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data
    return data.some((v, i) => i % 4 === 3 && v === 255 && data[i - 3] < 50 && data[i - 2] < 50 && data[i - 1] > 200)
  }, firstCanvas)
  assert.equal((await dialog.getByTestId('pdf-page-status').textContent()).trim(), '第 2 / 2 页')
  const width = await dialog.getByTestId('pdf-preview-canvas').evaluate(canvas => canvas.width)
  await dialog.getByTestId('pdf-zoom-in').click()
  await page.waitForFunction(previous => document.querySelector('[data-testid="pdf-preview-canvas"]').width > previous, width)
  await dialog.getByTestId('pdf-zoom-out').click()
  await dialog.getByTestId('pdf-prev').click()
  await page.waitForFunction(() => document.querySelector('[data-testid="pdf-page-status"]')?.textContent.trim() === '第 1 / 2 页')
  await capture('web-pdf.png'); await close()
  report('网页PDF真实彩色canvas、前后页、放大缩小')

  await open('image')
  await page.waitForFunction(() => { const image = document.querySelector('[data-testid="preview-image"]'); return image?.complete && image.naturalWidth === 2 })
  assert(await page.evaluate(() => window.__previewAudit.activeURLs.length > 0))
  await close()
  await page.waitForFunction(() => window.__previewAudit.activeURLs.length === 0)
  await open('document')
  await dialog.getByTestId('preview-document').waitFor()
  assert((await dialog.textContent()).includes('设备检修说明正文'))
  assert((await dialog.locator('table').textContent()).includes('断路器'))
  assert.equal(await dialog.locator('img').count(), 0)
  await capture('web-docx.png'); await close()
  await open('spreadsheet')
  await dialog.getByTestId('preview-spreadsheet').waitFor()
  assert((await dialog.textContent()).includes('断路器清册'))
  assert((await dialog.locator('td').allTextContents()).includes('3'), 'Excel显示缓存公式结果')
  assert.equal(await dialog.locator('a[href*="preview-untrusted"]').count(), 0)
  await dialog.getByTestId('preview-sheet-select').selectOption('1')
  assert((await dialog.textContent()).includes('检修计划第二张表'))
  await capture('web-xlsx.png'); await close()
  await open('unsupported')
  await dialog.getByTestId('preview-download').waitFor()
  assert.match(await dialog.textContent(), /不支持|暂不|无法|下载/)
  assert.equal(await dialog.getByTestId('pdf-preview-canvas').count(), 0)
  await close()
  report('网页图片URL回收、Word正文表格、Excel换表、外链不加载、不支持格式退路')

  await open('image')
  await page.waitForFunction(() => document.querySelector('[data-testid="preview-image"]')?.naturalWidth === 2)
  const accessPath = `${base}/documents/${documents.image.id}/access`
  const access = await (await request(accessPath, tokens.a)).json()
  const removeReader = await request(accessPath, tokens.a, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: 'restricted', base_access_revision: access.access_revision, access: access.access.filter(entry => entry.user_id !== seed.users.b.id) }) })
  assert.equal(removeReader.status, 200, await removeReader.clone().text())
  const revised = await removeReader.json()
  await page.evaluate(() => window.dispatchEvent(new Event('focus')))
  await dialog.getByRole('alert').filter({ hasText: '当前账号已无法访问' }).waitFor()
  assert.equal(await dialog.getByTestId('preview-image').count(), 0, '权限撤回应清空已显示图片')
  await page.waitForFunction(() => window.__previewAudit.activeURLs.length === 0)
  await close()
  const restoreReader = await request(accessPath, tokens.a, { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ visibility: 'restricted', base_access_revision: revised.access_revision, access: access.access }) })
  assert.equal(restoreReader.status, 200, await restoreReader.clone().text())
  report('网页已开启预览在权限撤回后清空内容并回收URL')

  async function delayedPreview(action) {
    let entered, release
    const gate = new Promise(resolve => { release = resolve }), reached = new Promise(resolve => { entered = resolve })
    const url = `${origin}${base}/documents/${documents.text.id}/versions/${documents.text.version}/preview`
    const handler = async route => { const response = await route.fetch(); entered(); await gate; await route.fulfill({ response }).catch(() => {}) }
    await page.route(url, handler)
    const previousAbort = await page.evaluate(() => window.__previewAudit.aborted.length)
    await open('text'); await reached
    try {
      await action()
      await page.waitForFunction(previous => window.__previewAudit.aborted.length > previous, previousAbort)
    } finally { release(); await page.unroute(url, handler) }
    await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))))
    assert.equal(await dialog.isVisible(), false)
    assert.equal(await page.getByTestId('preview-text').count(), 0)
  }
  await delayedPreview(close)
  await delayedPreview(async () => {
    // 模拟在预览仍开启时从外部入口切换空间，执行实际选择处理。
    // Select's click handler belongs to its inner wrapper; dispatching on the outer component cannot open it.
    await page.getByTestId('shared-library-selector').locator('.el-select__wrapper').dispatchEvent('click')
    await page.getByTestId(`shared-library-option-${seed.other_workspace}`).dispatchEvent('click')
  })
  await page.getByTestId('shared-library-selector').click()
  await page.getByTestId(`shared-library-option-${seed.workspace}`).click()
  await delayedPreview(async () => {
    await page.getByRole('button', { name: '退出登录', exact: true }).dispatchEvent('click')
    await page.getByRole('heading', { name: '登录云服务', exact: true }).waitFor()
    await login('c')
  })
  report('网页关闭/空间切换/账号切换中止请求且迟到内容不回显')
  assert.deepEqual(unexpectedRequests, [], '预览不得对外访问')
  assert.deepEqual(failures, [], '网页不得出现未处理错误')
  await capture('web-final.png')
  await context.tracing.stop({ path: path.join(temp, 'web-trace.zip') }); traceContext = null
  await context.close(); await browser.close(); browser = null

  if (!process.argv.includes('--web-only')) {
    const manifest = path.join(temp, 'desktop-fixture.json')
    fs.writeFileSync(manifest, JSON.stringify({ temp, origin, seed, documents }))
    const logFile = fs.openSync(path.join(temp, 'desktop-run.log'), 'w')
    const childEnv = { ...process.env, TEAMHUB_PREVIEW_FIXTURE: manifest, TEAMHUB_TEST_PASSWORD: password }
    delete childEnv.ELECTRON_RUN_AS_NODE
    const desktop = spawn(path.join(repo, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), [path.join(repo, 'scripts/test_cloud_preview_desktop.cjs')], { cwd: repo, env: childEnv, stdio: ['ignore', logFile, logFile] })
    fs.closeSync(logFile)
    const code = await new Promise((resolve, reject) => { desktop.once('error', reject); desktop.once('exit', resolve) })
    assert.equal(code, 0, fs.readFileSync(path.join(temp, 'desktop-run.log'), 'utf8'))
    report('Electron file:// 预览与个人仓库隔离')
  }
  saveResults('passed')
  console.log('在线预览 E2E 全部通过；证据：' + temp)
}
main().then(() => cleanup(0), async error => {
  console.error(error); console.error('预览验收证据：' + temp)
  for (const [index, page] of pages.entries()) if (!page.isClosed()) await page.screenshot({ path: path.join(temp, `failure-${index}.png`), fullPage: true }).catch(() => {})
  if (traceContext) await traceContext.tracing.stop({ path: path.join(temp, 'web-failure-trace.zip') }).catch(() => {})
  saveResults('failed', error)
  await cleanup(1)
})
async function cleanup(code) {
  if (browser) await browser.close().catch(() => {})
  if (service && service.exitCode === null) service.kill('SIGTERM')
  process.exitCode = code
}
