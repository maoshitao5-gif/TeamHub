#!/usr/bin/env node
// 真实 Hermes 子进程、生产 Vue/Electron 页面和隔离资料库；仅使用本机合成模型。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, spawnSync } = require('node:child_process')

const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-ai-assist-hermes-'))
const capability = crypto.randomBytes(32).toString('hex')
const model = 'deepseek-teamhub-hermes-e2e'
const testKey = 'sk-hermes-synthetic-only-not-a-real-key'
const privateTerm = '未授权读取的合成红杉密语'
const privateReasoning = 'synthetic-hermes-private-reasoning-never-render'
const results = [], calls = [], errors = [], externalRequests = [], children = [], timers = new Set()
let api, provider, providerBase, desktop, page, context, fixtures, releasePreview, releaseDelayed
let tracing = false
let simulateEventDisconnects = 0
let dropNextSendResponse = false, missingLookups = 0, lookupCount = 0, recoveredTask
let sendPostCount = 0
const lookupRequestIds = []

async function until(check, label, timeout = 45000) {
  const deadline = Date.now() + timeout
  while (Date.now() < deadline) {
    const value = await check()
    if (value) return value
    await new Promise(resolve => setTimeout(resolve, 60))
  }
  throw new Error('等待超时：' + label)
}
function pass(name) { results.push(name); console.log('PASS ' + name) }
const freePort = () => new Promise((resolve, reject) => {
  const listener = net.createServer().once('error', reject)
  listener.listen(0, '127.0.0.1', () => {
    const port = listener.address().port
    listener.close(error => error ? reject(error) : resolve(port))
  })
})
async function request(route, { method = 'GET', body, protectedAI = true } = {}) {
  return fetch(api + route, {
    method,
    headers: {
      ...(body ? { 'Content-Type': 'application/json' } : {}),
      ...(protectedAI ? { 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': capability } : {})
    },
    body: body ? JSON.stringify(body) : undefined,
    signal: AbortSignal.timeout(30000)
  })
}
async function json(route, options) {
  const response = await request(route, options)
  assert([200, 201, 202].includes(response.status), `${route}: ${await response.clone().text()}`)
  return response.json()
}
const partner = (route, options) => json('/api/ai/partner' + route, options)

function questionOf(messages) {
  for (const message of [...messages].reverse()) {
    if (message.role !== 'user' || typeof message.content !== 'string') continue
    const naturalQuestion = message.content.match(/(?:^|\n)(任务[^\n]+)\s*$/)
    if (naturalQuestion) return naturalQuestion[1]
    try {
      const value = JSON.parse(message.content)
      if (typeof value.question === 'string') return value.question
    } catch {}
  }
  throw new Error('缺少本轮问题')
}
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
function imageParts(messages) {
  return messages.flatMap(message => Array.isArray(message.content) ? message.content : [])
    .filter(part => part.type === 'image_url')
}
async function gate(name) {
  await new Promise(resolve => {
    const timer = setTimeout(() => { timers.delete(timer); resolve() }, 20000)
    timers.add(timer)
    const release = () => { clearTimeout(timer); timers.delete(timer); resolve() }
    if (name === 'preview') releasePreview = release
    else releaseDelayed = release
  })
}
async function streamResponse(res, { name, args, answer, pause = false }) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control': 'no-cache' })
  const frame = (delta, finish_reason = null) => {
    if (!res.destroyed) res.write(`data: ${JSON.stringify({ id: 'synthetic-' + calls.length,
      object: 'chat.completion.chunk', created: 1, model,
      choices: [{ index: 0, delta, finish_reason }] })}\n\n`)
  }
  frame({ role: 'assistant' })
  frame({ reasoning_content: privateReasoning })
  if (name) {
    const encoded = JSON.stringify(args), midpoint = Math.ceil(encoded.length / 2)
    frame({ tool_calls: [{ index: 0, id: 'tool-' + calls.length, type: 'function',
      function: { name, arguments: encoded.slice(0, midpoint) } }] })
    frame({ tool_calls: [{ index: 0, function: { arguments: encoded.slice(midpoint) } }] })
    frame({}, 'tool_calls')
  } else {
    const midpoint = Math.min(100, Math.ceil(answer.length / 2))
    frame({ content: answer.slice(0, midpoint) })
    if (pause) await gate('preview')
    if (res.destroyed) return
    frame({ content: answer.slice(midpoint) })
    frame({}, 'stop')
  }
  if (res.destroyed) return
  res.write(`data: ${JSON.stringify({ id: 'synthetic-' + calls.length,
    object: 'chat.completion.chunk', created: 1, model, choices: [],
    usage: { prompt_tokens: 30, completion_tokens: 20, total_tokens: 50 } })}\n\n`)
  res.end('data: [DONE]\n\n')
}
async function startProvider() {
  provider = http.createServer(async (req, res) => {
    try {
      let content = ''
      for await (const chunk of req) {
        content += chunk
        assert(content.length <= 24 * 1024 * 1024, '请求超过测试上限')
      }
      assert.equal(req.url, '/v1/chat/completions')
      assert.equal(req.headers.authorization, 'Bearer ' + testKey)
      const body = JSON.parse(content), question = questionOf(body.messages)
      const executed = toolResults(body.messages)
      assert.equal(body.model, model)
      assert.equal(body.stream, true, '真实 Hermes 必须发送流式模型请求')
      assert.equal(body.thinking.type, question.includes('深度思考') ? 'enabled' : 'disabled')
      if (body.thinking.type === 'enabled') assert.equal(body.reasoning_effort, 'high')
      assert(!content.includes(privateTerm), '未读取的原文不能进入模型请求')
      if (question.includes('任务连续流') || question.includes('任务资料') && !executed.length) {
        assert(!content.includes('其他合成资料.txt'), '全库模式不能在按需搜索前曝光任意文件名')
      }
      assert.deepEqual(new Set(body.tools.map(tool => tool.function.name)), new Set([
        'search_documents', 'document_overview', 'read_document', 'read_table',
        'read_visual', 'read_embedded_images', 'read_document_card', 'read_skill_reference', 'read_object_skill', 'create_draft'
      ]), 'Hermes 只能看到 TeamHub 控制的 10 个工具')
      calls.push({ question, body })
      if (question.includes('任务取消')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' })
        res.write(`data: ${JSON.stringify({ id: 'cancel-preview', model,
          choices: [{ index: 0, delta: { content: '已生成的部分回答，停止后仍应保留。' }, finish_reason: null }] })}\n\n`)
        await gate('delayed')
        if (!res.destroyed) res.end('data: [DONE]\n\n')
        return
      }
      if (question.includes('任务限流')) {
        res.writeHead(429, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ error: { message: '不要透传的上游错误 ' + testKey, type: 'rate_limit' } }))
        return
      }
      if (question.includes('任务闲聊')) {
        await streamResponse(res, {
          answer: '## Hermes 已连接\n\n这是通过真实 Hermes 引擎生成的流式回答。\n\n|方式|状态|\n|---|---|\n|本机引擎|正常|\n\n```text\n格式正常\n```\n\n<img src="https://example.test/private.png" onerror="window.__hermesUnsafe=1"><script>window.__hermesUnsafe=1</script>',
          pause: true
        })
        return
      }
      if (question.includes('任务恢复')) {
        await streamResponse(res, { answer: '原发送结果已经通过只读查询恢复，继续显示同一个任务的回答。', pause: true })
        return
      }
      if (question.includes('任务连续流')) {
        res.writeHead(200, { 'Content-Type': 'text/event-stream' })
        for (let index = 0; index < 240 && !res.destroyed; index++) {
          res.write(`data: ${JSON.stringify({ id: 'health-stream', model,
            choices: [{ index: 0, delta: { reasoning_content: crypto.randomBytes(1200).toString('hex'),
              content: index === 0 ? '连续流测试：' : '正常。' }, finish_reason: null }] })}\n\n`)
          await new Promise(resolve => setTimeout(resolve, 5))
        }
        res.end(`data: ${JSON.stringify({ id: 'health-stream', model,
          choices: [{ index: 0, delta: {}, finish_reason: 'stop' }],
          usage: { prompt_tokens: 100, completion_tokens: 250 } })}\n\ndata: [DONE]\n\n`)
        return
      }
      if (question.includes('任务长回答')) {
        const records = Array.from({ length: 400 }, (_, index) =>
          `记录 ${index + 1}：${crypto.createHash('sha256').update('synthetic-long-answer-' + index).digest('hex')}，此条用于核验独立段落完整性。`).join('\n\n')
        await streamResponse(res, { answer: '长回答开始。\n\n' + records + '\n\n长回答完整结束。' })
        return
      }
      if (question.includes('任务未知引用')) {
        await streamResponse(res, { answer: '这段分析仍有参考价值，但引用编号有误。[S999]' })
        return
      }
      let name, args, answer
      if (question.includes('任务资料')) {
        const searched = executed.find(item => item.name === 'search_documents')
        const read = executed.find(item => item.name === 'read_document')
        const draft = executed.find(item => item.name === 'create_draft')
        if (!searched) { name = 'search_documents'; args = { query: '青川项目', limit: 8, offset: 0 } }
        else if (!read) {
          assert.equal(searched.value.documents.length, 1)
          assert.equal(searched.value.documents[0].document_id, fixtures.document.id)
          name = 'read_document'; args = { document_id: fixtures.document.id, offset: 0, limit: 20 }
        } else if (!draft) {
          assert(JSON.stringify(read.value).includes('验收记录已完成三项核对'))
          const id = read.value.units[0].source_id
          name = 'create_draft'
          args = { name: 'Hermes青川项目分析.md', content: `# 项目分析\n\n验收记录已完成三项核对。[${id}]`, source_ids: [id] }
        } else {
          const id = read.value.units[0].source_id
          assert(draft.value.id)
          answer = `## 项目分析\n\n已阅读青川项目的固定版本，验收记录已完成三项核对。[${id}]\n\n已生成分析草稿，等待确认收录。`
        }
      } else if (question.includes('任务追问') || question.includes('任务第三轮')) {
        assert(content.includes('验收记录已完成三项核对'), '追问须接续完整有效证据')
        assert(body.messages.some(message => message.role === 'assistant' &&
          typeof message.content === 'string' && message.content.includes('已阅读青川项目的固定版本')),
        '上一轮正式回答必须作为原生 assistant 消息接续')
        assert(body.messages.some(message => message.role === 'assistant' && message.tool_calls?.length &&
          message.reasoning_content === privateReasoning), '原生工具历史须保留模型要求的 reasoning_content')
        assert(executed.some(item => item.name === 'read_document'), '原生历史保留已读证据的工具消息')
        if (question.includes('任务第三轮')) {
          assert(body.messages.some(message => message.role === 'assistant' &&
            message.content?.includes('继续上轮讨论')), '第三轮必须接续第二轮回答')
          answer = '第三轮接续正常，沿用前两轮的有效证据。[S1]'
        } else answer = '继续上轮讨论，三项核对已有原文依据，可以据此补充检查清单。[S1]'
      } else if (question.includes('任务空检索')) {
        const attempts = executed.filter(item => item.name === 'search_documents').length
        if (attempts < 3) {
          name = 'search_documents'; args = { query: `不存在的合成资料${attempts}`, limit: 8, offset: 0 }
        } else answer = '没有检索到相关资料。现有线索不足，建议补充设备名称或项目名称。'
      } else if (question.includes('任务图像')) {
        const read = executed.find(item => item.name === 'read_visual')
        if (!read) { name = 'read_visual'; args = { document_id: fixtures.image.id, pages: [1] } }
        else {
          const images = imageParts(body.messages)
          assert.equal(images.length, 1, '实际图片应进入模型请求')
          assert(images[0].image_url.url.startsWith('data:image/png;base64,'))
          const decoded = spawnSync(path.join(repo, 'venv/bin/python'), ['-c',
            'import sys,base64;from io import BytesIO;from PIL import Image;i=Image.open(BytesIO(base64.b64decode(sys.stdin.read()))).convert("RGB");assert i.size==(240,120);assert i.getpixel((120,60))==(255,0,0)'],
          { input: images[0].image_url.url.split(',')[1], encoding: 'utf8', timeout: 5000 })
          assert.equal(decoded.status, 0, decoded.stderr)
          const id = read.value.images[0].source_id
          answer = `已实际读取现场合成图片的红色像素，引用对应固定版本。[${id}]`
        }
      } else throw new Error('未知合成任务')
      await streamResponse(res, { name, args, answer })
    } catch (error) {
      errors.push('本机假模型：' + error.message)
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
    binary = fs.realpathSync(path.resolve(process.env.TEAMHUB_AI_BACKEND_BINARY))
    const packaged = ['photo-render-fix-2026-10-22', 'image-preorganize-fix-2026-10-21', 'multimodal-organize-2026-10-21', 'folder-ai-2026-10-21', 'document-cards-2026-10-21', 'hermes-2026-10-18', 'hermes-connection-fix-2026-10-18', 'ai-natural-chat-2026-10-19', 'ai-preorganize-2026-10-20', 'security-audit-2026-10-21'].map(folder =>
      path.join(repo, 'dist-electron', folder, 'mac-arm64/TeamHub.app/Contents/Resources/backend/teamhub-backend'))
    assert((binary.startsWith(path.join(repo, 'dist') + path.sep) || packaged.includes(binary)) &&
      fs.statSync(binary).isFile(), '只允许本仓库冻结后端或本轮明确验收的应用内后端')
  }
  const env = {
    PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'zh_CN.UTF-8', PYTHONPATH: repo,
    TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true', TEAMHUB_AI_CAPABILITY: capability,
    USER_DATA_DIR: path.join(temp, 'backend-profile'), TEAMHUB_AI_CONFIG_DIR: path.join(temp, 'ai-config'),
    LIBRARY_PATH: path.join(temp, 'library'), PORT: new URL(api).port,
    TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false'
  }
  const log = path.join(temp, 'personal.log'), fd = fs.openSync(log, 'w')
  const child = spawn(binary || path.join(repo, 'venv/bin/python'), binary ? [] :
    ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port],
  { cwd: temp, env, stdio: ['ignore', fd, fd] })
  children.push(child)
  fs.closeSync(fd)
  await until(async () => {
    if (child.exitCode !== null) throw new Error('后端退出：' + fs.readFileSync(log, 'utf8'))
    try { return (await fetch(api + '/api/health')).ok } catch { return false }
  }, '隔离后端就绪')
}
async function ingest(name, content) {
  const source = path.join(temp, name)
  fs.writeFileSync(source, content)
  return json('/api/documents/quick-add', { method: 'POST', body: { source_path: source } })
}
async function setupFixtures() {
  const document = await ingest('青川项目验收.txt', '青川项目\n验收记录已完成三项核对。\n下一步需补充检查清单。\n')
  await ingest('其他合成资料.txt', privateTerm)
  const imagePath = path.join(temp, '现场合成图片.png')
  const generated = spawnSync(path.join(repo, 'venv/bin/python'), ['-c',
    'from PIL import Image;import sys;Image.new("RGB",(240,120),"red").save(sys.argv[1])', imagePath],
  { encoding: 'utf8', timeout: 5000 })
  assert.equal(generated.status, 0, generated.stderr)
  const image = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: imagePath } })
  return { document, image }
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
  await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 800))
  page = await desktop.firstWindow()
  page.setDefaultTimeout(20000)
  context = desktop.context()
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true })
  tracing = true
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/*', async route => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== api) {
      externalRequests.push(url.href)
      return route.abort()
    }
    if (url.pathname.endsWith('/events') && simulateEventDisconnects > 0) {
      simulateEventDisconnects -= 1
      return route.abort('connectionreset')
    }
    const isSend = url.pathname.endsWith('/send') && route.request().method() === 'POST'
    if (isSend) sendPostCount++
    if (isSend && dropNextSendResponse) {
      dropNextSendResponse = false
      const accepted = await route.fetch()
      assert.equal(accepted.status(), 202)
      recoveredTask = await accepted.json()
      return route.abort('connectionreset')
    }
    if (url.pathname.includes('/requests/')) {
      assert.equal(route.request().method(), 'GET')
      lookupRequestIds.push(decodeURIComponent(url.pathname.split('/').at(-1)))
      lookupCount++
      if (missingLookups > 0) {
        missingLookups--
        return route.fulfill({ status: 404, contentType: 'application/json',
          body: JSON.stringify({ error: { code: 'PARTNER_REQUEST_NOT_FOUND', message: '请求尚未登记' } }) })
      }
    }
    return route.continue()
  })
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/pending')
  await page.getByRole('button', { name: '打开文件库', exact: true }).click()
  await page.locator('.pending-page').waitFor()
  await page.locator('.nav-primary .nav-item').filter({ hasText: 'AI 伙伴' }).click()
  await page.getByTestId('partner-page').waitFor()
}
class PartnerPage {
  async compose(question) {
    await page.locator('[data-testid="partner-question"]:is(input,textarea),[data-testid="partner-question"] textarea').fill(question)
    assert.equal(await page.getByRole('dialog', { name: '确认本轮 AI 讨论' }).count(), 0)
  }
  async send() {
    const started = page.waitForResponse(response => response.url().includes('/api/ai/partner/') &&
      /\/(send|resume)$/.test(response.url()) && response.request().method() === 'POST')
    const [, response] = await Promise.all([page.getByTestId('partner-send').click(), started])
    assert.equal(response.status(), 202, await response.text())
    const task = await response.json(), submitted = response.request().postDataJSON()
    assert.equal(await page.getByRole('dialog', { name: '确认本轮 AI 讨论' }).count(), 0)
    return { ...task, submitted }
  }
  async newSession() { await page.getByTestId('partner-new-session').click() }
  async selectDocuments(ids) {
    await page.getByTestId('partner-scope').click()
    const dialog = page.getByRole('dialog', { name: '选择本轮讨论的资料' })
    await dialog.waitFor()
    for (const id of ids) await page.getByTestId('partner-select-document-' + id).click()
    await dialog.getByRole('button', { name: '完成', exact: true }).click()
  }
  assistant(text) { return page.getByTestId('partner-message-assistant').filter({ hasText: text }) }
}
const ui = new PartnerPage()
async function taskDone(id, expected = 'completed') {
  return until(async () => {
    const task = await partner('/tasks/' + id)
    if (task.state === expected) return task
    if (['failed', 'cancelled', 'interrupted'].includes(task.state)) throw new Error(JSON.stringify(task.error))
    return false
  }, 'Hermes 任务 ' + expected, 90000)
}
async function capture(name) { await page.screenshot({ path: path.join(temp, name), fullPage: true }) }

async function main() {
  await startProvider()
  await startBackend()
  await json('/api/ai/settings', { method: 'PUT', body: { base_url: providerBase, model, api_key: testKey } })
  assert.equal((await request('/api/ai/partner/sessions', { protectedAI: false })).status, 403)
  fixtures = await setupFixtures()
  await startDesktop()
  await ui.compose('任务闲聊：一起讨论资料整理思路。')
  assert.equal(calls.length, 0, '点击发送前不调用模型')
  const chat = await ui.send()
  await page.getByTestId('partner-answer-preview').filter({ hasText: '真实 Hermes 引擎' }).waitFor()
  assert.equal((await partner('/tasks/' + chat.id)).state, 'running')
  assert.equal((await partner('/sessions/' + chat.session_id)).messages.filter(item => item.role === 'assistant').length, 0)
  await capture('hermes-streaming.png')
  releasePreview()
  releasePreview = undefined
  const chatted = await taskDone(chat.id)
  assert(chatted.steps.some(step => step.message.includes('Hermes 已就绪')))
  const answer = ui.assistant('真实 Hermes 引擎')
  await answer.waitFor()
  assert.equal(await answer.locator('h2').textContent(), 'Hermes 已连接')
  assert.equal(await answer.locator('table tbody tr').count(), 1)
  assert.equal(await answer.locator('pre code').textContent(), '格式正常\n')
  assert.equal(await answer.locator('img,script,svg').count(), 0)
  assert.equal(await page.evaluate(() => window.__hermesUnsafe), undefined)
  assert(!(await page.locator('body').textContent()).includes(privateReasoning))
  pass('一次点击直接发送，无确认弹窗；真实 Hermes 流式响应在完成前显示安全 Markdown 预览')

  // A long, busy stream must leave the health endpoint responsive, not just idle SSE.
  for (let index = 0; index < 28; index++) await ingest(`合成健康检查资料${index}.txt`, `合成内容 ${index}`)
  await ui.newSession()
  await page.getByText('整个个人库', { exact: true }).click()
  await ui.compose('任务连续流：验证对话期间后台连接持续可用。')
  const busy = await ui.send(), healthTimes = []
  let polling = true
  const healthPolling = (async () => {
    while (polling) {
      const started = Date.now()
      const response = await fetch(api + '/api/health', { signal: AbortSignal.timeout(1500) })
      assert(response.ok)
      healthTimes.push(Date.now() - started)
      await new Promise(resolve => setTimeout(resolve, 50))
    }
  })()
  try {
    await taskDone(busy.id)
    await ui.assistant('连续流测试').waitFor()
  } finally {
    polling = false
    await healthPolling
  }
  assert(healthTimes.length >= 5, '持续流期间必须实际探测多次健康状态')
  assert(Math.max(...healthTimes) < 1000, `健康请求最大延迟 ${Math.max(...healthTimes)}ms`)
  fs.writeFileSync(path.join(temp, 'stream-health.json'), JSON.stringify({ samples: healthTimes.length,
    maxMs: Math.max(...healthTimes), allMs: healthTimes }, null, 2))
  pass('30份资料范围内连续240次模型流输出，合法响应超过旧512KiB，后台健康检查持续响应')

  await ui.newSession()
  await page.getByText('整个个人库', { exact: true }).click()
  await ui.compose('任务资料：查找青川项目，阅读资料并生成分析说明。')
  const task = await ui.send(), completed = await taskDone(task.id)
  assert(completed.sources.length >= 1)
  assert(completed.sources.every(source => source.version_id === fixtures.document.current_version.id))
  assert.equal(completed.artifacts.length, 1)
  await ui.assistant('已阅读青川项目的固定版本').waitFor()
  assert((await ui.assistant('已阅读青川项目的固定版本').locator('.source-chip').first().textContent()).includes('[S1]'))
  const repeated = await partner(`/sessions/${task.session_id}/send`, { method: 'POST', body: task.submitted })
  assert.equal(repeated.id, task.id)
  assert.equal(calls.filter(call => call.question.includes('任务资料')).length, 4)
  await capture('hermes-analysis.png')
  pass('真实 Hermes 调用受控搜索、固定版本阅读和草稿工具；来源可见，重复启动复用同一任务')

  await page.getByTestId('partner-save-artifact').click()
  await page.getByRole('dialog', { name: '收录生成成果', exact: true }).getByRole('button', { name: '确认收录', exact: true }).click()
  await page.getByRole('button', { name: '查看已收录资料', exact: true }).waitFor()
  const artifact = (await partner('/tasks/' + task.id)).artifacts[0]
  assert(artifact.saved_document_id)
  const saved = await json('/api/documents/' + artifact.saved_document_id)
  assert.equal(saved.status, 'pending')
  assert.equal(saved.current_version.version_number, 1)
  const savedAgain = await partner(`/tasks/${task.id}/artifacts/${artifact.id}/save`, { method: 'POST',
    body: { consent: true, request_id: crypto.randomUUID() } })
  assert.equal(savedAgain.document_id, artifact.saved_document_id)
  assert.equal((await json('/api/documents/' + fixtures.document.id)).current_version.id, fixtures.document.current_version.id)
  pass('成果通过生产界面确认后建立待整理 V1；重复保存不增加副本，原资料版本保持完整')

  await ui.compose('任务追问：继续上轮讨论，下一步应该做什么？')
  const followup = await ui.send(), followed = await taskDone(followup.id)
  await ui.assistant('继续上轮讨论').waitFor()
  assert.equal(followed.sources[0].version_id, completed.sources[0].version_id)
  assert.equal(calls.filter(call => call.question.includes('任务追问')).length, 1)
  await capture('hermes-followup.png')
  pass('追问以原生角色、工具配对和必要模型字段接续历史，复用有效证据且不重复读文件')

  await ui.compose('任务第三轮：延续刚才的建议，说明核验重点。')
  const third = await ui.send()
  await taskDone(third.id)
  await ui.assistant('第三轮接续正常').waitFor()
  assert.equal(calls.filter(call => call.question.includes('任务第三轮')).length, 1)
  pass('三轮对话连续接续，每轮一次发送，无额外确认及重复取材')

  await ui.newSession()
  await page.getByText('整个个人库', { exact: true }).click()
  await ui.compose('任务空检索：搜索相关资料，没有结果则说明缺少的线索。')
  const empty = await ui.send()
  await taskDone(empty.id)
  await ui.assistant('没有检索到相关资料').waitFor()
  assert.equal(calls.filter(call => call.question.includes('任务空检索')).length, 4)
  pass('连续三次空检索不会中断整轮，Hermes 收到反馈后自然说明缺口')

  await ui.newSession()
  await ui.compose('任务未知引用：核验一个不匹配引用对回答的影响。')
  const invalid = await ui.send(), invalidDone = await taskDone(invalid.id)
  assert(invalidDone.answer.includes('这段分析仍有参考价值'))
  assert.equal(invalidDone.sources.length, 0)
  assert(!invalidDone.answer.includes('[S999]'), '无效编号不能伪装成已核对引用')
  await ui.assistant('这段分析仍有参考价值').waitFor()
  pass('单个未知引用降级提示，保留回答正文且不伪造来源')

  await ui.newSession()
  simulateEventDisconnects = 2
  await ui.compose('任务长回答：验证长文本完整呈现与事件连接恢复。')
  const lengthy = await ui.send(), longDone = await taskDone(lengthy.id)
  assert(longDone.answer.length > 18000)
  assert(longDone.answer.endsWith('长回答完整结束。'))
  await ui.assistant('长回答完整结束').waitFor()
  assert.equal(calls.filter(call => call.question.includes('任务长回答')).length, 1)
  await capture('hermes-long-answer.png')
  pass('超过18000字的回答完整保留，事件流短暂断开后恢复同一任务，没有重复付费请求')

  await ui.newSession()
  dropNextSendResponse = true
  missingLookups = 2
  lookupCount = 0
  lookupRequestIds.length = 0
  const sendCountBeforeRecovery = sendPostCount
  await ui.compose('任务恢复：发送响应丢失时自动找回原任务。')
  await page.getByTestId('partner-send').click()
  await until(() => recoveredTask && lookupCount >= 3, '发送结果以原请求编号只读对账')
  await page.getByTestId('partner-answer-preview').filter({ hasText: '原发送结果已经' }).waitFor()
  releasePreview()
  releasePreview = undefined
  await taskDone(recoveredTask.id)
  await ui.assistant('原发送结果已经通过只读查询恢复').waitFor()
  assert.equal(sendPostCount - sendCountBeforeRecovery, 1, '响应丢失后不得自动重发 POST')
  assert(lookupRequestIds.every(id => id === recoveredTask.request_id), '对账必须沿用原请求编号')
  assert.equal(calls.filter(call => call.question.includes('任务恢复')).length, 1)
  pass('POST响应丢失及两次暂时404后，自动GET找回原任务，模型只调用一次')

  await ui.newSession()
  await ui.selectDocuments([fixtures.image.id])
  await ui.compose('任务图像：查看所选现场图片并说明图像。')
  const visual = await ui.send(), viewed = await taskDone(visual.id)
  assert.equal(viewed.sources.length, 1)
  assert.equal(viewed.sources[0].document_id, fixtures.image.id)
  await ui.assistant('已实际读取现场合成图片').waitFor()
  await capture('hermes-image.png')
  pass('真实图片通过 TeamHub 工具交给 Hermes 并进入模型请求，PNG 实际尺寸与红色像素核验通过')

  await ui.newSession()
  await ui.compose('任务取消：等待模型回复，再停止任务。')
  const delayed = await ui.send()
  await until(() => calls.some(call => call.question.includes('任务取消')), '慢模型请求已发出')
  await page.getByTestId('partner-answer-preview').filter({ hasText: '已生成的部分回答' }).waitFor()
  await page.getByTestId('partner-cancel').click()
  await until(async () => (await partner('/tasks/' + delayed.id)).state === 'cancelled', '取消任务')
  releaseDelayed()
  releaseDelayed = undefined
  const cancelled = await partner('/tasks/' + delayed.id)
  assert.equal(cancelled.answer, null)
  assert(cancelled.answer_preview.includes('已生成的部分回答'))
  assert((await page.locator('body').textContent()).includes('已生成的部分回答'))
  assert.equal((await partner('/sessions/' + delayed.session_id)).messages.filter(item => item.role === 'assistant').length, 0)
  assert.equal(calls.filter(call => call.question.includes('任务取消')).length, 1)
  pass('停止后保留已生成的部分文字，取消 Hermes 进程，不保存迟到正式回复且不重复调用')

  await ui.newSession()
  await ui.compose('任务限流：验证安全报错和不自动重试。')
  const failed = await ui.send()
  await until(async () => (await partner('/tasks/' + failed.id)).state === 'failed', '模型限流')
  const failure = (await partner('/tasks/' + failed.id)).error
  assert.equal(failure.code, 'AI_RATE_LIMITED')
  assert(!JSON.stringify(failure).includes(testKey))
  await page.getByText(failure.message, { exact: true }).waitFor()
  assert.equal(calls.filter(call => call.question.includes('任务限流')).length, 1)
  assert.deepEqual(errors, [])
  assert.deepEqual(externalRequests, [])
  assert(!fs.readFileSync(path.join(temp, 'personal.log'), 'utf8').includes(testKey))
  await capture('hermes-service-error.png')
  pass('429 通过生产界面安全提示且零自动重试；页面无异常、无远程资源请求、日志无密钥')
  await context.tracing.stop({ path: path.join(temp, 'desktop-trace.zip') })
  tracing = false
}
function report(status, error) {
  const data = { status, groups: results, providerRequests: calls.length, pageErrors: errors,
    externalRequests, ...(error ? { error: error.stack } : {}) }
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify(data, null, 2))
  const escape = value => String(value).replace(/[&<>"']/g,
    char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>Hermes 桌面验收</title><h1>${escape(status)}</h1><ul>${results.map(name => `<li>${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p>真实 Hermes 引擎、临时资料、本机合成模型；未调用真实收费服务。</p>${fs.readdirSync(temp).filter(name => /\.(png|zip)$/.test(name)).map(name => `<p><a href="${escape(name)}">${escape(name)}</a></p>`).join('')}</html>`)
  fs.writeFileSync(path.join(temp, 'junit.xml'), `<?xml version="1.0" encoding="UTF-8"?><testsuite name="Hermes desktop" tests="${results.length + (error ? 1 : 0)}" failures="${error ? 1 : 0}">${results.map(name => `<testcase name="${escape(name)}"/>`).join('')}${error ? `<testcase name="incomplete journey"><failure>${escape(error.stack)}</failure></testcase>` : ''}</testsuite>`)
  console.log('验收产物：' + temp)
}
main().then(() => report('passed'), async error => {
  console.error(error)
  if (page) await capture('failure.png').catch(() => {})
  if (tracing) await context.tracing.stop({ path: path.join(temp, 'failure-trace.zip') }).catch(() => {})
  report('failed', error)
  process.exitCode = 1
}).finally(async () => {
  releasePreview?.()
  releaseDelayed?.()
  if (desktop) await desktop.close().catch(() => {})
  for (const child of children.reverse()) {
    if (child.exitCode === null) {
      child.kill('SIGTERM')
      await Promise.race([
        new Promise(resolve => child.once('exit', resolve)),
        new Promise(resolve => setTimeout(() => { if (child.exitCode === null) child.kill('SIGKILL'); resolve() }, 3000))
      ])
    }
  }
  for (const timer of timers) clearTimeout(timer)
  if (provider) {
    provider.closeAllConnections()
    await new Promise(resolve => provider.close(resolve))
  }
})
