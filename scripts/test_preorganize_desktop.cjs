#!/usr/bin/env node
// 真实 Hermes、生产页面、临时资料库和本机模拟模型；不接触真实资料或收费服务。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, spawnSync } = require('node:child_process')
const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-ai-assist-preorganize-'))
const library = path.join(temp, 'library')
const capability = crypto.randomBytes(32).toString('hex')
const testKey = 'sk-synthetic-preorganization-not-a-real-key'
const model = 'deepseek-preorganization-e2e'
const privateTerm = 'NEVER_SEND_OTHER_DOCUMENT_CONTENT_72E4'
const documents = [], calls = [], results = [], errors = [], external = [], children = []
const modes = new Map()
let api, provider, providerBase, desktop, page, context, tracing = false
const delay = ms => new Promise(resolve => setTimeout(resolve, ms))
async function until(check, label, timeout = 60000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await check()
    if (value) return value
    await delay(80)
  }
  throw new Error('等待超时：' + label)
}
function pass(name) { results.push(name); console.log('PASS ' + name) }
const freePort = () => new Promise((resolve, reject) => {
  const server = net.createServer().once('error', reject)
  server.listen(0, '127.0.0.1', () => {
    const port = server.address().port
    server.close(error => error ? reject(error) : resolve(port))
  })
})
async function request(route, { method = 'GET', body, protectedAI = true } = {}) {
  return fetch(api + route, { method, signal: AbortSignal.timeout(30000), headers: {
    ...(body ? { 'Content-Type': 'application/json' } : {}),
    ...(protectedAI ? { 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': capability } : {})
  }, body: body ? JSON.stringify(body) : undefined })
}
async function json(route, options) {
  const response = await request(route, options)
  assert([200, 201, 202].includes(response.status), `${route}: ${await response.clone().text()}`)
  return response.json()
}
const pre = (route, options) => json('/api/ai/preorganize' + route, options)
function toolResults(messages) {
  const names = new Map(messages.flatMap(message => message.tool_calls || [])
    .map(call => [call.id, call.function.name]))
  return messages.filter(message => message.role === 'tool').map(message => {
    const raw = typeof message.content === 'string' ? message.content :
      message.content?.find(part => part.type === 'text')?.text
    let value
    try { value = JSON.parse(raw) } catch { value = { text: raw } }
    return { name: names.get(message.tool_call_id), value }
  })
}
function objectKind(document) {
  return document.testKind || (document.is_folder ? 'folder' : document.name.endsWith('.png') ? 'image' : 'text')
}
function assertRedPixels(messages) {
  const images = messages.flatMap(message => Array.isArray(message.content) ? message.content : [])
    .filter(part => part.type === 'image_url')
  assert.equal(images.length, 1, '原图像素必须实际送达模拟多模态模型')
  const matched = images[0].image_url.url.match(/^data:(image\/(png|jpeg));base64,/)
  assert(matched, '图像数据必须带真实 PNG 或 JPEG MIME')
  const checked = spawnSync(path.join(repo, 'venv/bin/python'), ['-c',
    'import sys,base64,json;from io import BytesIO;from PIL import Image;v=json.load(sys.stdin);im=Image.open(BytesIO(base64.b64decode(v["data"])));assert im.format==v["format"];im=im.convert("RGB");r,g,b=im.getpixel((im.width//2,im.height//2));assert r>200 and g<60 and b<60,(im.size,(r,g,b))'],
  { input: JSON.stringify({ data: images[0].image_url.url.split(',', 2)[1],
      format: matched[2] === 'png' ? 'PNG' : 'JPEG' }), encoding: 'utf8', timeout: 5000 })
  assert.equal(checked.status, 0, '模拟模型须收到可解码的红色画面：' + checked.stderr)
  return matched[1]
}
function stream(res, name, args) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream' })
  const frame = (delta, finish_reason = null) => res.write(`data: ${JSON.stringify({
    id: 'preorg-' + calls.length, object: 'chat.completion.chunk', created: 1, model,
    choices: [{ index: 0, delta, finish_reason }]
  })}\n\n`)
  frame({ role: 'assistant' })
  frame({ tool_calls: [{ index: 0, id: 'call-' + calls.length, type: 'function',
    function: { name, arguments: JSON.stringify(args) } }] })
  frame({}, 'tool_calls')
  res.write(`data: ${JSON.stringify({ id: 'preorg-' + calls.length, model, choices: [],
    usage: { prompt_tokens: 100, completion_tokens: 40, total_tokens: 140 } })}\n\n`)
  res.end('data: [DONE]\n\n')
}
async function startProvider() {
  provider = http.createServer(async (req, res) => {
    try {
      let content = ''
      for await (const chunk of req) { content += chunk; assert(content.length < 24 * 1024 * 1024) }
      assert.equal(req.url, '/v1/chat/completions')
      assert.equal(req.headers.authorization, 'Bearer ' + testKey)
      const body = JSON.parse(content)
      assert.equal(body.model, model)
      assert.equal(body.stream, true)
      assert(!content.includes(privateTerm), '未授权资料正文不得外发')
      const target = documents.find(document => content.includes(document.id))
      assert(target, '模型请求应有明确的单份资料范围')
      for (const other of documents) {
        if (other.id !== target.id) assert(!content.includes(other.id), '每份任务只授权目标资料')
      }
      const tools = body.tools.map(tool => tool.function.name)
      const cardTask = tools.includes('submit_document_card')
      const kind = objectKind(target)
      assert(cardTask || tools.includes('propose_organization'))
      assert(!tools.includes('create_draft'))
      assert(content.includes(`name: teamhub-${kind}-understanding`), '自动装载已保存对象对应的 Skill：' + kind)
      calls.push({ document_id: target.id, tools, kind })
      if (modes.get(target.id) === '429') {
        res.writeHead(429, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ error: { message: '不得回显的模拟服务信息 ' + testKey } }))
        return
      }
      const executed = toolResults(body.messages)
      if (target.is_folder && !executed.some(item => item.name === 'list_folder_members')) {
        stream(res, 'list_folder_members', { document_id: target.id })
        return
      }
      if (kind === 'archive') {
        assert(tools.includes('list_archive_members') && tools.includes('read_archive_member'))
        assert(!tools.includes('read_folder_member'), '压缩包必须使用自身的成员工具')
        const inventory = executed.find(item => item.name === 'list_archive_members')
        if (!inventory) {
          stream(res, 'list_archive_members', { document_id: target.id })
          return
        }
        assert.equal(inventory.value.total_members, target.testMembers.length + (target.testNested ? 1 : 0))
        for (const member of target.testMembers) {
          const listed = inventory.value.members.find(item => item.member_path === member.path)
          assert(listed?.readable, '可读成员应准确出现在清单：' + member.path)
          assert.equal(listed.object_kind, member.kind)
          if (member.kind === 'image' && !executed.some(item => item.name === 'read_object_skill')) {
            stream(res, 'read_object_skill', { kind: 'image' })
            return
          }
          if (!executed.some(item => item.name === 'read_archive_member' && item.value.member_path === member.path)) {
            stream(res, 'read_archive_member', { document_id: target.id, member_path: member.path,
              action: member.kind === 'image' ? 'visual' : 'text' })
            return
          }
        }
        if (target.testNested) {
          const nested = inventory.value.members.find(item => item.member_path === '未展开附件.zip')
          assert(nested && !nested.readable, '嵌套归档保持明确的不可递归状态')
        }
      }
      if (cardTask && !executed.some(item => item.name === 'read_skill_reference')) {
        stream(res, 'read_skill_reference', { method: 'text' })
        return
      }
      const readings = executed.filter(item => ['read_document', 'read_visual', 'read_folder_member', 'read_archive_member'].includes(item.name))
      if (!readings.length) {
        stream(res, target.is_folder ? 'read_folder_member' : kind === 'image' ? 'read_visual' : 'read_document',
          { document_id: target.id, ...(target.is_folder ? { member_path: '设计/说明.txt', action: 'text' } : {}) })
        return
      }
      const sourceIds = [...new Set(
        JSON.stringify(readings.map(item => item.value)).match(/"source_id":"S\d+"/g)?.map(value => value.split('"')[3]) || []
      )]
      assert(sourceIds.length, JSON.stringify(readings))
      if (kind === 'image' || target.testMembers?.some(member => member.kind === 'image')) {
        calls.at(-1).imageMediaType = assertRedPixels(body.messages)
      }
      if (modes.get(target.id) === 'text-end-once') {
        calls.at(-1).readCalls = readings.length
        const correcting = body.messages.some(message => message.role === 'user' &&
          typeof message.content === 'string' && message.content.includes('TeamHub 任务尚未提交成功'))
        if (!correcting) {
          res.writeHead(200, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ id: 'ordinary-text-result', model, choices: [{ index: 0,
            finish_reason: 'stop', message: { role: 'assistant', content: '已读图，建议命名为红色现场照片。' } }],
          usage: { prompt_tokens: 100, completion_tokens: 30 } }))
          return
        }
        assert.equal(readings.length, 1, '补交必须复用已有图片来源，不重复读图')
      }
      const understanding = {
        summary: kind === 'image' ? '青川项目现场照片，画面为红色。' : '青川项目资料，记录设备检查或归档事项。',
        document_type: kind === 'image' ? '现场图片' : kind === 'archive' ? '项目资料压缩包' : '项目记录',
        topics: ['青川项目'], sections: [], facts: [],
        search_terms: [target.testSearch || '资料卡独有检索线索ABC'], uses: ['查找项目记录'], gaps: [], source_ids: sourceIds
      }
      if (cardTask) {
        stream(res, 'submit_document_card', understanding)
        return
      }
      stream(res, 'propose_organization', {
        name: '青川项目-' + target.name,
        tags: ['青川项目', kind === 'image' ? '现场照片' : '检修记录'],
        target_dir: '', summary: '依据已读取的内容准备整理方案。',
        related_clues: ['青川项目'], gaps: [], source_ids: sourceIds, card: understanding
      })
    } catch (error) {
      errors.push('模拟模型：' + error.message)
      if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'application/json' })
      if (!res.destroyed) res.end('{}')
    }
  })
  await new Promise(resolve => provider.listen(0, '127.0.0.1', resolve))
  providerBase = `http://127.0.0.1:${provider.address().port}/v1`
}
async function startBackend() {
  api = `http://127.0.0.1:${await freePort()}`
  let binary
  if (process.env.TEAMHUB_AI_BACKEND_BINARY) {
    binary = fs.realpathSync(process.env.TEAMHUB_AI_BACKEND_BINARY)
    const packaged = ['photo-render-fix-2026-10-22', 'image-preorganize-fix-2026-10-21', 'multimodal-organize-2026-10-21', 'folder-ai-2026-10-21', 'document-cards-2026-10-21', 'ai-preorganize-2026-10-20', 'security-audit-2026-10-21', 'preorganize-format-fix-2026-10-21'].map(folder =>
      path.join(repo, 'dist-electron', folder, 'mac-arm64/TeamHub.app/Contents/Resources/backend/teamhub-backend'))
    assert(packaged.includes(binary) || binary.startsWith(path.join(repo, 'dist') + path.sep))
  }
  const log = path.join(temp, 'personal.log'), fd = fs.openSync(log, 'w')
  const child = spawn(binary || path.join(repo, 'venv/bin/python'), binary ? [] :
    ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port],
  { cwd: temp, stdio: ['ignore', fd, fd], env: {
    PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'zh_CN.UTF-8', PYTHONPATH: repo,
    TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true', TEAMHUB_AI_CAPABILITY: capability,
    USER_DATA_DIR: path.join(temp, 'backend-profile'), TEAMHUB_AI_CONFIG_DIR: path.join(temp, 'ai-config'),
    LIBRARY_PATH: library, PORT: new URL(api).port, TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false'
  } })
  children.push(child); fs.closeSync(fd)
  await until(async () => {
    if (child.exitCode !== null) throw new Error(fs.readFileSync(log, 'utf8'))
    try { return (await fetch(api + '/api/health')).ok } catch { return false }
  }, '临时后端启动')
}
async function ingest(name, content) {
  const source = path.join(temp, name)
  if (content !== undefined) fs.writeFileSync(source, content)
  const doc = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: source } })
  documents.push(doc)
  return doc
}
async function state(doc) {
  return { document: await json('/api/documents/' + doc.id),
    versions: await json(`/api/documents/${doc.id}/versions`) }
}
async function ready(doc) {
  return until(async () => {
    const current = await pre('/documents/' + doc.id)
    if (current.state === 'ready') return current
    if (current.state === 'failed') throw new Error(JSON.stringify(current))
    return false
  }, '方案生成：' + doc.name)
}
async function startDesktop() {
  const bundled = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
  const { _electron } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(bundled) ? bundled : 'playwright'))
  const fixture = path.join(temp, 'fixture.json')
  fs.writeFileSync(fixture, JSON.stringify({ repo, temp, api, capability }), { mode: 0o600 })
  const env = { ...process.env, TEAMHUB_AI_FIXTURE: fixture }
  delete env.ELECTRON_RUN_AS_NODE
  desktop = await _electron.launch({
    executablePath: path.join(repo, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'),
    args: [path.join(repo, 'scripts/test_ai_assist_desktop_app.cjs')], env
  })
  page = await desktop.firstWindow()
  page.setDefaultTimeout(20000)
  context = desktop.context()
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true }); tracing = true
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== api) {
      external.push(url.href); return route.abort()
    }
    return route.continue()
  })
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/pending')
  await openPendingLibrary()
}
async function openPendingLibrary() {
  const open = page.getByRole('button', { name: '打开文件库', exact: true })
  await until(async () => {
    if (await open.isVisible()) {
      await open.click()
      return true
    }
    return page.locator('.pending-page').isVisible()
  }, '打开临时资料库')
  await page.locator('.pending-page').waitFor()
}
const card = doc => page.locator('.pending-card').filter({ has: page.getByText(doc.name, { exact: true }) })
async function capture(name) {
  await page.screenshot({ path: path.join(temp, name), fullPage: true, animations: 'disabled' })
}
async function main() {
  await startProvider(); await startBackend()
  assert.equal((await request('/api/ai/preorganize/config', { protectedAI: false })).status, 403)
  await json('/api/ai/settings', { method: 'PUT', body: { base_url: providerBase, model, api_key: testKey } })
  const config = await pre('/config')
  assert.equal(config.policy.enabled, false)
  const first = await ingest('检修记录.txt', '青川项目检修记录。变压器绝缘检查已完成，尚需复核仪表。')
  const second = await ingest('验收记录.txt', '青川项目验收记录。设备验收后应归档试验记录。')
  const imagePath = path.join(temp, '现场照片.png')
  const generated = spawnSync(path.join(repo, 'venv/bin/python'), ['-c',
    'from PIL import Image;import sys;Image.new("RGB",(240,120),"red").save(sys.argv[1])', imagePath],
  { encoding: 'utf8', timeout: 5000 })
  assert.equal(generated.status, 0, generated.stderr)
  const image = await ingest('现场照片.png')
  const privateDoc = await ingest('不参与预整理.txt', privateTerm)
  const originals = new Map(await Promise.all([first, second, image].map(async doc => [doc.id, await state(doc)])))
  await startDesktop()
  assert.equal(calls.length, 0, '配置密钥和收录资料不自动外发')
  pass('默认关闭；只允许可信本机界面；配置密钥与导入资料不触发模型')

  for (const doc of [first, second, image]) {
    await card(doc).locator('.el-checkbox').click()
    assert(await card(doc).getByRole('checkbox').isChecked())
  }
  await page.getByTestId('preorganize-start-selected').click()
  await page.getByTestId('preorganize-consent-confirm').click()
  const proposals = await Promise.all([first, second, image].map(ready))
  const cardsBefore = new Map()
  for (const doc of [first, second, image]) {
    assert.deepEqual(await state(doc), originals.get(doc.id), '生成方案不得改变正式资料或版本')
    assert.equal(calls.filter(call => call.document_id === doc.id).length, 2,
      '真实阅读与方案提交各一次，提交后不额外总结')
    const result = await json('/api/ai/cards/' + doc.id)
    assert(result.card?.content?.summary && result.card.sources.length)
    cardsBefore.set(doc.id, result.card)
  }
  assert.equal((await pre('/documents/' + privateDoc.id)).state, 'waiting')
  assert.equal((await json('/api/ai/partner/sessions')).items.length, 0, '后台预整理不产生可见聊天')
  await until(async () => /[（(]3[)）]/.test(await page.getByTestId('preorganize-review-selected').innerText()),
    '界面轮询显示全部三份可确认方案')
  await capture('preorganize-ready.png')
  pass('一次批次授权，真实 Hermes 阅读文字与图片，方案生成零资料变更、零额外总结请求')

  // 确认前保留用户修改；批量采用使用每份不同方案。
  await page.getByTestId('preorganize-review-selected').click()
  const review = page.getByTestId('preorganize-review-dialog')
  await review.waitFor()
  const nameSelector = `[data-testid="preorganize-name-${first.id}"]`
  await page.locator(`${nameSelector}:is(input),${nameSelector} input`).fill('人工确认的检修记录')
  await capture('preorganize-review.png')
  await page.getByTestId('preorganize-apply').click()
  await review.waitFor({ state: 'hidden' })
  for (const doc of [first, second, image]) {
    const current = await state(doc)
    assert.equal(current.document.status, 'organized')
    assert.equal(current.versions.length, originals.get(doc.id).versions.length)
    assert(current.document.tags.some(tag => tag.name === '青川项目'))
    assert(fs.existsSync(path.join(library, current.document.storage_path)))
    assert.equal((await json('/api/ai/cards/' + doc.id)).card.revision, cardsBefore.get(doc.id).revision,
      '改名分类入库不能丢失资料卡或重做理解')
  }
  assert.equal((await state(first)).document.name, '人工确认的检修记录.txt')
  const operationsResult = await pre('/operations')
  const operations = Array.isArray(operationsResult) ? operationsResult : operationsResult.items
  const operation = operations.find(value => value.document_id === first.id)
  assert(operation)
  await page.getByTestId('preorganize-operations').click()
  await page.getByTestId('preorganize-undo-' + operation.id).click()
  await until(async () => (await state(first)).document.status === 'pending', '撤销本次整理')
  const restored = await state(first)
  assert.equal(restored.document.name, originals.get(first.id).document.name)
  assert.equal(restored.document.storage_path, originals.get(first.id).document.storage_path)
  assert.deepEqual(restored.document.tags, originals.get(first.id).document.tags)
  assert.equal(restored.versions.length, originals.get(first.id).versions.length)
  await page.getByRole('dialog', { name: '最近整理', exact: true }).locator('.el-dialog__headerbtn').click()
  pass('批量一次确认保留人工改名、目录可空、版本不增；独立撤销精确恢复整理前状态')

  // 外部限流应可解释、不能自动重复收费；仍可手工整理。
  modes.set(privateDoc.id, '429')
  const currentConfig = await pre('/config')
  await pre('/start', { method: 'POST', body: { library_token: currentConfig.library_token,
    service_revision: currentConfig.service_revision, consent: true,
    request_id: crypto.randomUUID(), document_ids: [privateDoc.id] } })
  const failure = await until(async () => {
    const value = await pre('/documents/' + privateDoc.id)
    return value.state === 'failed' && value
  }, '外部服务限流')
  assert(failure.error && !failure.error.includes(testKey))
  const beforeCount = calls.filter(call => call.document_id === privateDoc.id).length
  await delay(2500)
  assert.equal(calls.filter(call => call.document_id === privateDoc.id).length, beforeCount)
  await json(`/api/documents/${privateDoc.id}/organize`, { method: 'POST',
    body: { name: privateDoc.name, tags: ['手工整理'], target_dir: '' } })
  assert.equal((await state(privateDoc)).document.status, 'organized')
  pass('服务失败有明确原因、不会自动重试收费，手工整理继续可用')

  await page.reload()
  await openPendingLibrary()
  await page.getByTestId('preorganize-auto-toggle').click()
  await page.getByTestId('preorganize-consent-confirm').click()
  assert.equal((await pre('/config')).policy.active, true)
  const future = await ingest('后续收录.txt', '青川项目后续收录的检修材料。')
  await ready(future)
  await page.getByTestId('preorganize-auto-toggle').click()
  await until(async () => !(await pre('/config')).policy.enabled, '关闭自动预整理')
  const disabled = await ingest('暂停后收录.txt', '青川项目暂停自动处理后收录的材料。')
  await delay(2500)
  assert.equal((await pre('/documents/' + disabled.id)).state, 'waiting')
  assert.equal(calls.filter(call => call.document_id === disabled.id).length, 0)
  pass('主动开启当前库后新资料自动准备方案，关闭后新资料不外发')

  const legacy = await ingest('旧版格式.wps', Buffer.concat([
    Buffer.from('d0cf11e0a1b11ae1', 'hex'), Buffer.from('synthetic binary office')]))
  Object.assign(legacy, await json('/api/documents/' + legacy.id, {
    method: 'PUT', body: { name: '显示为标准格式.docx' } }))
  const beforeLegacy = await state(legacy), beforeLegacyCalls = calls.length
  const legacyConfig = await pre('/config')
  await pre('/start', { method: 'POST', body: { library_token: legacyConfig.library_token,
    service_revision: legacyConfig.service_revision, consent: true,
    request_id: crypto.randomUUID(), document_ids: [legacy.id] } })
  const legacyFailure = await until(async () => {
    const value = await pre('/documents/' + legacy.id)
    return value.state === 'failed' && value
  }, '旧二进制格式的本地预检')
  assert(legacyFailure.error.includes('Office/WPS') && legacyFailure.error.includes('另存为'))
  assert.equal(calls.length, beforeLegacyCalls, '不可读格式不能发起模型请求')
  assert.deepEqual(await state(legacy), beforeLegacy, '预检不能改文件对象或版本')
  await page.reload(); await openPendingLibrary()
  const legacyStatus = page.getByTestId('preorganize-status-' + legacy.id)
  await until(() => legacyStatus.innerText().then(text => text.includes('Office/WPS')), '界面显示具体格式原因')
  assert((await legacyStatus.locator('.doc-preorganize-error').getAttribute('title')).includes('仅修改文件名后缀'))
  pass('旧WPS内容即使显示DOCX也在模型前明确提示转换，零模型请求、资料与版本不变')

  // 从正常整理窗口生成卡片，真实工具加载专项方法；用户补充独立持久保存。
  await page.getByTestId('manual-organize-' + disabled.id).click()
  const organize = page.getByRole('dialog', { name: '整理资料', exact: true })
  const understanding = organize.getByTestId('document-understanding-card')
  await understanding.getByTestId('card-generate').click()
  const generatedCard = await until(async () => {
    const value = await json('/api/ai/cards/' + disabled.id)
    if (value.job.state === 'failed') throw new Error(value.job.error)
    return value.card && value.job.state === 'ready' && value
  }, '独立资料卡生成')
  assert(generatedCard.card.content.search_terms.includes('资料卡独有检索线索ABC'))
  assert.equal(calls.filter(call => call.document_id === disabled.id).length, 3,
    '专项方法、原文读取、卡片提交三次，提交后无额外总结')
  await until(() => understanding.locator('.card-summary').isVisible(), '界面显示资料卡')
  await understanding.locator('details.card-annotation > summary').click()
  await understanding.getByLabel('我的补充与修正').fill('人工说明：用于培训交接，AI 不应覆盖。')
  await understanding.getByTestId('card-save-annotation').click()
  await until(async () => (await json('/api/ai/cards/' + disabled.id)).annotation.text.includes('培训交接'), '保存人工补充')
  await understanding.getByTestId('card-generate').click()
  await until(async () => {
    const value = await json('/api/ai/cards/' + disabled.id)
    return value.job.state === 'ready' && value.card.revision > generatedCard.card.revision
  }, '卡片重新生成')
  assert((await json('/api/ai/cards/' + disabled.id)).annotation.text.includes('AI 不应覆盖'))
  const search = await pre('/documents?search=' + encodeURIComponent('资料卡独有检索线索ABC'))
  assert(search.items.some(item => item.id === disabled.id), '仅资料卡提供的线索能够找回文件')
  const sourceState = await state(disabled)
  assert.equal(sourceState.document.status, 'pending')
  assert.equal(sourceState.versions.length, 1)
  await until(() => understanding.getByTestId('card-generate').isEnabled(), '界面刷新生成完成状态')
  await understanding.locator('.card-summary').scrollIntoViewIfNeeded()
  await capture('document-card-understanding.png')
  pass('预整理卡片跨入库保留；独立生成按需加载方法，人工补充不被重算覆盖，卡片线索能找回资料')
  await organize.locator('.el-dialog__headerbtn').click()

  // Folder remains one managed object; source locations identify its members.
  const folderName = '整套资料.v1'
  fs.mkdirSync(path.join(temp, folderName, '设计'), { recursive: true })
  fs.writeFileSync(path.join(temp, folderName, '设计/说明.txt'), '青川项目设备检查说明。')
  fs.writeFileSync(path.join(temp, folderName, '未读附件.txt'), '另一个需要后续核对的阶段。')
  const folder = await ingest(folderName)
  const folderOriginal = await state(folder)
  await page.reload(); await openPendingLibrary()
  await card(folder).locator('.el-checkbox').click()
  await page.getByTestId('preorganize-start-selected').click()
  await page.getByTestId('preorganize-consent-confirm').click()
  await ready(folder)
  assert.deepEqual(await state(folder), folderOriginal)
  const folderCard = (await json('/api/ai/cards/' + folder.id)).card
  assert.equal(folderCard.coverage.read_members, 1)
  assert.equal(folderCard.coverage.total_members, 2)
  assert.equal(folderCard.state, 'partial')
  assert.equal(folderCard.sources[0].location.member_path, '设计/说明.txt')
  await until(async () => /[（(]1[)）]/.test(await page.getByTestId('preorganize-review-selected').innerText()),
    '文件夹方案显示可确认')
  await page.getByTestId('preorganize-review-selected').click()
  await review.waitFor()
  await review.locator('.review-card > summary').click()
  const folderPreview = review.getByTestId('document-understanding-card')
  await folderPreview.waitFor()
  await until(() => folderPreview.locator('.card-summary').isVisible(), '文件夹审阅显示资料卡')
  await capture('folder-preorganize-review.png')
  await page.getByTestId('preorganize-apply').click()
  await review.waitFor({ state: 'hidden' })
  const folderOrganized = await state(folder)
  assert.equal(folderOrganized.document.status, 'organized')
  assert.equal(folderOrganized.versions.length, 1)
  const folderWorking = path.join(library, folderOrganized.document.storage_path)
  assert.equal(fs.readFileSync(path.join(folderWorking, '设计/说明.txt'), 'utf8'), '青川项目设备检查说明。')
  assert.equal(fs.readFileSync(path.join(folderWorking, '未读附件.txt'), 'utf8'), '另一个需要后续核对的阶段。')
  assert.equal((await json('/api/ai/cards/' + folder.id)).card.revision, folderCard.revision)
  pass('文件夹真实成员读取、部分覆盖与来源路径、审阅卡片展开、整体确认保持内部内容和版本')

  // Detail card remains accessible for an organized folder.
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/library')
  await until(() => page.getByText(folderOrganized.document.name, { exact: true }).first().isVisible(),
    '文档库显示已整理文件夹')
  await page.getByText(folderOrganized.document.name, { exact: true }).first().click()
  const detailCard = page.getByTestId('document-understanding-card').last()
  await detailCard.waitFor()
  await until(() => detailCard.locator('.card-summary').isVisible(), '文件夹详情显示资料卡')
  await capture('folder-card-detail.png')
  pass('已整理文件夹详情可查看持久化资料卡')

  // New object readers use actual synthetic bytes through the real Hermes process.
  const fixtures = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', [
    'import sys,io,pathlib,zipfile,tarfile,subprocess',
    'from PIL import Image',
    'root=pathlib.Path(sys.argv[1])',
    'image=Image.new("RGB",(80,60),"red")',
    'image.save(root/"扩展图片.avif","AVIF")',
    'image.save(root/"扩展图片.jp2","JPEG2000")',
    '(root/"矢量图片.svg").write_text(\'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="60"><rect width="80" height="60" fill="red"/></svg>\')',
    'image.save(root/"原始红图.png")',
    'subprocess.run(["/usr/bin/sips","-s","format","heic",str(root/"原始红图.png"),"--out",str(root/"手机图片.heic")],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10)',
    'nested=io.BytesIO()',
    'with zipfile.ZipFile(nested,"w",zipfile.ZIP_DEFLATED) as inner: inner.writestr("内部.txt","不得递归读取的合成资料")',
    'with zipfile.ZipFile(root/"混合项目资料.zip","w",zipfile.ZIP_DEFLATED) as archive:',
    ' archive.writestr("说明/检查.txt","青川项目设备检查说明，归档后应保留原压缩包。")',
    ' archive.write(root/"扩展图片.avif","照片/现场.avif")',
    ' archive.writestr("未展开附件.zip",nested.getvalue())',
    'with tarfile.open(root/"整套资料.tar.gz","w:gz") as archive:',
    ' data="青川项目归档记录，压缩包名称应保留完整复合扩展名。".encode()',
    ' info=tarfile.TarInfo("说明/归档.txt");info.size=len(data);archive.addfile(info,io.BytesIO(data))'
  ].join('\n'), temp], { encoding: 'utf8', timeout: 20000 })
  assert.equal(fixtures.status, 0, fixtures.stderr)
  const multimodalImages = []
  for (const name of ['扩展图片.avif', '扩展图片.jp2', '矢量图片.svg', '手机图片.heic']) {
    const doc = await ingest(name)
    doc.testKind = 'image'
    multimodalImages.push(doc)
  }
  const zip = await ingest('混合项目资料.zip')
  Object.assign(zip, { testKind: 'archive', testNested: true, testSearch: '多模态卡片查找唯一线索ZP',
    testMembers: [{ path: '说明/检查.txt', kind: 'text' }, { path: '照片/现场.avif', kind: 'image' }] })
  const tar = await ingest('整套资料.tar.gz')
  Object.assign(tar, { testKind: 'archive', testMembers: [{ path: '说明/归档.txt', kind: 'text' }] })
  const multimodal = [...multimodalImages, zip, tar]
  const snapshots = new Map(await Promise.all(multimodal.map(async doc => {
    const value = await state(doc)
    return [doc.id, { state: value, bytes: fs.readFileSync(path.join(library, value.document.storage_path)) }]
  })))
  const documentCount = (await json('/api/documents/search', { method: 'POST', body: {} })).total
  const beforeMultimodalCalls = calls.length
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/pending')
  await openPendingLibrary()
  assert.equal(calls.length, beforeMultimodalCalls, '扩大格式覆盖不应自动开启外发')
  for (const doc of multimodal) await card(doc).locator('.el-checkbox').click()
  await page.getByTestId('preorganize-start-selected').click()
  await page.getByTestId('preorganize-consent-confirm').click()
  await Promise.all(multimodal.map(ready))
  const multimodalCards = new Map()
  for (const doc of multimodal) {
    assert.deepEqual(await state(doc), snapshots.get(doc.id).state, '多模态预处理保持源对象和版本')
    const value = await json('/api/ai/cards/' + doc.id)
    assert.equal(value.object_kind, objectKind(doc))
    assert(value.card?.sources.length && value.card.content.summary)
    assert(value.card.sources.every(source => source.document_id === doc.id &&
      source.version_id === snapshots.get(doc.id).state.versions[0].id))
    multimodalCards.set(doc.id, value.card)
  }
  for (const doc of multimodalImages) {
    assert.equal(calls.filter(call => call.document_id === doc.id).length, 2, '图片实际视觉读取后直接提交')
    assert.equal(multimodalCards.get(doc.id).state, doc.name.endsWith('.heic') ? 'partial' : 'ready')
  }
  pass('AVIF、JPEG2000、SVG、HEIC 真正图像解码并传送像素；图片 Skill 自动加载，HEIC 主图范围明确')
  const zipCard = multimodalCards.get(zip.id)
  assert.equal(zipCard.coverage.total_members, 3)
  assert.equal(zipCard.coverage.read_members, 2)
  assert.equal(zipCard.state, 'partial')
  assert.deepEqual([...new Set(zipCard.sources.map(source => source.location.member_path))].sort(),
    zip.testMembers.map(member => member.path).sort())
  assert(zipCard.coverage.gaps.some(gap => gap.includes('未展开附件.zip')))
  assert.equal(multimodalCards.get(tar.id).coverage.read_members, 1)
  assert.equal(multimodalCards.get(tar.id).coverage.complete, true)
  const archiveSearch = await pre('/documents?search=' + encodeURIComponent(zip.testSearch))
  assert.deepEqual(archiveSearch.items.map(item => item.id), [zip.id], '压缩包资料卡独有内容能够检索整包')
  assert.equal((await json('/api/documents/search', { method: 'POST', body: {} })).total, documentCount,
    '压缩包成员不能生成额外的资料对象')
  pass('ZIP 文字与 AVIF 成员真实读取、按需加载图片 Skill、成员路径来源与卡片检索；嵌套包缺口保留')
  await until(async () => /[（(]6[)）]/.test(await page.getByTestId('preorganize-review-selected').innerText()),
    '六份多模态方案可一起确认')
  await page.getByTestId('preorganize-review-selected').click()
  await review.waitFor()
  const tarName = `[data-testid="preorganize-name-${tar.id}"]`
  await page.locator(`${tarName}:is(input),${tarName} input`).fill('人工确认的压缩资料.v2')
  await capture('multimodal-preorganize-review.png')
  await page.getByTestId('preorganize-apply').click()
  await review.waitFor({ state: 'hidden' })
  for (const doc of multimodal) {
    const value = await state(doc), original = snapshots.get(doc.id)
    assert.equal(value.document.status, 'organized')
    assert.equal(value.versions.length, 1)
    assert.equal(value.versions[0].id, original.state.versions[0].id)
    assert.equal(value.versions[0].sha256_hash, original.state.versions[0].sha256_hash)
    assert.deepEqual(fs.readFileSync(path.join(library, value.document.storage_path)), original.bytes)
    assert.deepEqual(fs.readFileSync(value.versions[0].file_path), original.bytes)
    assert.equal((await json('/api/ai/cards/' + doc.id)).card.revision, multimodalCards.get(doc.id).revision)
  }
  assert.equal((await state(tar)).document.name, '人工确认的压缩资料.v2.tar.gz')
  assert.equal((await json('/api/documents/search', { method: 'POST', body: {} })).total, documentCount)
  const organizedSearch = await json('/api/documents/search', { method: 'POST',
    body: { keyword: zip.testSearch, status: 'organized', include_content: true } })
  assert.deepEqual(organizedSearch.documents.map(item => item.id), [zip.id])
  pass('多模态资料整体确认入库；TAR.GZ 复合扩展名、归档字节、版本、卡片与资料对象数量保持')

  const recoveryImage = spawnSync(path.join(repo, 'venv/bin/python'), ['-c',
    'from PIL import Image;import sys;Image.new("RGB",(480,320),"red").save(sys.argv[1],"JPEG")',
    path.join(temp, '待补交图片.jpeg')], { encoding: 'utf8', timeout: 5000 })
  assert.equal(recoveryImage.status, 0, recoveryImage.stderr)
  const recoveryDoc = await ingest('待补交图片.jpeg')
  recoveryDoc.testKind = 'image'
  modes.set(recoveryDoc.id, 'text-end-once')
  const beforeRecovery = await state(recoveryDoc)
  const recoveryConfig = await pre('/config')
  await pre('/start', { method: 'POST', body: { library_token: recoveryConfig.library_token,
    service_revision: recoveryConfig.service_revision, consent: true,
    request_id: crypto.randomUUID(), document_ids: [recoveryDoc.id] } })
  const recovered = await ready(recoveryDoc)
  assert.equal(recovered.diagnostics?.recovery_attempts, 1)
  assert.equal(recovered.diagnostics?.outcome, 'submitted')
  assert.equal(calls.filter(call => call.document_id === recoveryDoc.id).length, 3)
  assert.equal(calls.filter(call => call.document_id === recoveryDoc.id).at(-1).readCalls, 1)
  assert.deepEqual(await state(recoveryDoc), beforeRecovery, '补交方案不改动待整理对象和版本')
  await page.reload(); await openPendingLibrary()
  await card(recoveryDoc).getByRole('button', { name: '确认方案', exact: true }).click()
  await review.waitFor()
  await capture('image-completion-recovered.png')
  await page.getByTestId('preorganize-apply').click()
  await review.waitFor({ state: 'hidden' })
  assert.equal((await state(recoveryDoc)).document.status, 'organized')
  assert.equal((await state(recoveryDoc)).versions.length, 1)
  pass('JPEG 读图后仅文字结束时自动补交，复用原像素和来源；一次确认入库，不重复读取或增加文件版本')

  const densePath = path.join(temp, '高细节相机照片.jpeg')
  const denseFixture = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', [
    'import sys,random,json,io',
    'from PIL import Image,ImageDraw',
    'im=Image.frombytes("RGB",(2304,1600),random.Random(75319).randbytes(2304*1600*3))',
    'ImageDraw.Draw(im).rectangle((1000,650,1300,950),fill="red")',
    'im.save(sys.argv[1],"JPEG",quality=92)',
    'photo=Image.open(sys.argv[1]).convert("RGB");b=io.BytesIO();photo.save(b,"PNG")',
    'print(json.dumps({"png_bytes":len(b.getvalue())}))'
  ].join('\n'), densePath], { encoding: 'utf8', timeout: 15000 })
  assert.equal(denseFixture.status, 0, denseFixture.stderr)
  assert(JSON.parse(denseFixture.stdout).png_bytes > 4 * 1024 * 1024,
    '合成照片必须实际触发旧版 PNG 输出超限，不能只测试纯色图')
  const denseBytes = fs.readFileSync(densePath)
  const dense = await ingest('高细节相机照片.jpeg')
  dense.testKind = 'image'
  const beforeDense = await state(dense), denseConfig = await pre('/config')
  await pre('/start', { method: 'POST', body: { library_token: denseConfig.library_token,
    service_revision: denseConfig.service_revision, consent: true,
    request_id: crypto.randomUUID(), document_ids: [dense.id] } })
  await ready(dense)
  const denseCalls = calls.filter(call => call.document_id === dense.id)
  assert.equal(denseCalls.length, 2, '可读照片只需读图和提交，无补交')
  assert.equal(denseCalls.at(-1).imageMediaType, 'image/jpeg', '高细节照片须以真实 JPEG 像素送达模型')
  assert.deepEqual(await state(dense), beforeDense)
  const denseCard = await json('/api/ai/cards/' + dense.id)
  assert.equal(denseCard.card.coverage.visual_pages, 1)
  assert(denseCard.card.coverage.gaps.some(gap => gap.includes('有损')), '压缩细节影响必须明确记录')
  await page.reload(); await openPendingLibrary()
  await card(dense).getByRole('button', { name: '确认方案', exact: true }).click()
  await review.waitFor()
  await capture('dense-photo-review.png')
  await page.getByTestId('preorganize-apply').click()
  await review.waitFor({ state: 'hidden' })
  const denseAfter = await state(dense)
  assert.equal(denseAfter.document.status, 'organized')
  assert.equal(denseAfter.versions.length, 1)
  assert.equal(denseAfter.versions[0].id, beforeDense.versions[0].id)
  assert.deepEqual(fs.readFileSync(path.join(library, denseAfter.document.storage_path)), denseBytes)
  assert.deepEqual(fs.readFileSync(denseAfter.versions[0].file_path), denseBytes)
  pass('高细节照片转 PNG 超过4MiB时以高质量JPEG送达真实Hermes，压缩缺口可见，原图与历史版本字节保持')

  assert.deepEqual(errors, [])
  assert.deepEqual(external, [])
  assert(!fs.readFileSync(path.join(temp, 'personal.log'), 'utf8').includes(testKey))
  assert(proposals.every(proposal => proposal.sources.length > 0))
  await capture('preorganize-complete.png')
  await context.tracing.stop({ path: path.join(temp, 'desktop-trace.zip') }); tracing = false
}
main().then(() => {
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify({
    status: 'passed', groups: results, modelRequests: calls.length, pageErrors: errors, external
  }, null, 2))
}, async error => {
  console.error(error)
  if (page) await capture('failure.png').catch(() => {})
  if (tracing) await context.tracing.stop({ path: path.join(temp, 'failure-trace.zip') }).catch(() => {})
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify({
    status: 'failed', groups: results, error: error.stack, pageErrors: errors, modelRequests: calls.length
  }, null, 2))
  process.exitCode = 1
}).finally(async () => {
  if (desktop) await desktop.close().catch(() => {})
  for (const child of children.reverse()) {
    if (child.exitCode === null) {
      child.kill('SIGTERM')
      await Promise.race([new Promise(resolve => child.once('exit', resolve)),
        delay(3000).then(() => { if (child.exitCode === null) child.kill('SIGKILL') })])
    }
  }
  if (provider) { provider.closeAllConnections(); await new Promise(resolve => provider.close(resolve)) }
  console.log('验收产物：' + temp)
})
