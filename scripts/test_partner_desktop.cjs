#!/usr/bin/env node
// 真实生产 Electron 页面 + 临时个人库 + 本机假模型；验证自主工具与成果保存完整任务。
const assert = require('node:assert/strict')
const fs = require('node:fs')
const os = require('node:os')
const path = require('node:path')
const http = require('node:http')
const net = require('node:net')
const crypto = require('node:crypto')
const { spawn, spawnSync } = require('node:child_process')

const repo = path.resolve(__dirname, '..')
const temp = fs.mkdtempSync(path.join(os.tmpdir(), 'teamhub-ai-assist-partner-'))
const library = path.join(temp, 'library')
const capability = crypto.randomBytes(32).toString('hex')
const testKey = 'sk-partner-synthetic-only-not-a-real-key', model = 'deepseek-teamhub-partner-e2e'
const results = [], calls = [], errors = [], externalRequests = [], children = [], timers = new Set()
const privateTerm = '仅未读取资料包含的红杉密语'
let api, provider, providerBase, desktop, page, context, tracing = false, fixtures
let continuationAuthorized = false
let releaseAnswerPreview
const privateReasoning = 'synthetic-private-reasoning-never-render'
const continuationEvidence = '续做需要保留的完整原文末尾证据'

async function until(check, label, timeout = 30000) {
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
  listener.listen(0, '127.0.0.1', () => { const port = listener.address().port; listener.close(() => resolve(port)) })
})
async function request(route, { method = 'GET', body, protectedAI = true } = {}) {
  return fetch(api + route, { method,
    headers: { ...(body ? { 'Content-Type': 'application/json' } : {}), ...(protectedAI ? { 'X-TeamHub-AI': '1', 'X-TeamHub-AI-Capability': capability } : {}) },
    body: body ? JSON.stringify(body) : undefined })
}
async function json(route, options) {
  const response = await request(route, options)
  assert([200, 201, 202].includes(response.status), `${route}: ${await response.clone().text()}`)
  return response.json()
}
const partner = (route, options) => json('/api/ai/partner' + route, options)
function toolResponse(name, args, number) {
  return { id: 'chatcmpl-synthetic-' + number, object: 'chat.completion', created: 1, model,
    choices: [{ index: 0, finish_reason: 'tool_calls', message: { role: 'assistant', content: null,
      tool_calls: [{ id: 'tool-' + number, type: 'function', function: { name, arguments: JSON.stringify(args) } }] } }],
    usage: { prompt_tokens: 20, completion_tokens: 20, total_tokens: 40 } }
}
async function streamToolResponse(res, name, args, number, thinking, pauseForPreview = false) {
  res.writeHead(200, { 'Content-Type': 'text/event-stream', 'Cache-Control':'no-cache' })
  const frame = (delta, finish_reason = null, usage) => res.write(`data: ${JSON.stringify({ id:'chatcmpl-synthetic-' + number, object:'chat.completion.chunk', created:1, model, choices:[{ index:0, delta, finish_reason }], ...(usage ? { usage } : {}) })}\n\n`)
  frame({ role:'assistant' })
  if (thinking) frame({ reasoning_content: privateReasoning })
  frame({ tool_calls:[{ index:0, id:'tool-' + number, type:'function', function:{ name, arguments:'' } }] })
  const encoded = JSON.stringify(args), split = pauseForPreview ? Math.min(encoded.length - 1, 130) : Math.ceil(encoded.length / 2)
  frame({ tool_calls:[{ index:0, function:{ arguments:encoded.slice(0, split) } }] })
  if (pauseForPreview) await new Promise(resolve => {
    const timer = setTimeout(() => { timers.delete(timer); resolve() }, 15000)
    timers.add(timer)
    releaseAnswerPreview = () => { clearTimeout(timer); timers.delete(timer); resolve() }
  })
  if (res.destroyed) return
  frame({ tool_calls:[{ index:0, function:{ arguments:encoded.slice(split) } }] })
  frame({}, 'tool_calls')
  res.write(`data: ${JSON.stringify({ id:'chatcmpl-synthetic-' + number, object:'chat.completion.chunk', created:1, model, choices:[], usage:{ prompt_tokens:20, completion_tokens:20, total_tokens:40 } })}\n\n`)
  res.end('data: [DONE]\n\n')
}
function questionOf(messages) {
  for (const message of messages) {
    if (message.role !== 'user' || typeof message.content !== 'string') continue
    try { const value = JSON.parse(message.content); if (value.question) return value.question } catch {}
  }
  throw new Error('缺少本轮问题')
}
function toolResults(messages) {
  const names = new Map(messages.flatMap(message => message.tool_calls || []).map(call => [call.id, call.function.name]))
  return messages.filter(message => message.role === 'tool').map(message => {
    let value
    try { value = JSON.parse(message.content) } catch { value = { text: message.content } }
    return { name: names.get(message.tool_call_id), value }
  })
}
async function startProvider() {
  provider = http.createServer(async (req, res) => {
    try {
      let content = ''
      for await (const chunk of req) { content += chunk; if (content.length > 24 * 1024 * 1024) throw new Error('请求超过测试上限') }
      assert.equal(req.url, '/v1/chat/completions'); assert.equal(req.headers.authorization, 'Bearer ' + testKey)
      const body = JSON.parse(content)
      assert.equal(body.model, model); assert(!content.includes(privateTerm))
      const probeUser = body.messages.at(-1)?.content
      if (Array.isArray(probeUser) && !body.tools) {
        const image = probeUser.find(part => part.type === 'image_url')?.image_url?.url
        assert(image?.startsWith('data:image/png;base64,'))
        const decoded = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', `import sys,json,base64
from io import BytesIO
from PIL import Image
image=Image.open(BytesIO(base64.b64decode(sys.stdin.read()))).convert("RGB")
assert image.size==(480,180)
colors={(226,46,46):"red",(32,158,74):"green",(42,96,224):"blue",(242,209,32):"yellow",(151,63,202):"purple",(239,132,30):"orange"}
result={}
for index,position in enumerate(("left","middle","right")):
    center=80+index*160
    corner=image.getpixel((center-42,48))!=(255,255,255)
    top=image.getpixel((center+20,42))!=(255,255,255)
    lower=image.getpixel((center+38,120))!=(255,255,255)
    result[position]={"shape":"square" if corner else "circle" if top else "triangle" if lower else "diamond","color":colors[image.getpixel((center,90))]}
print(json.dumps(result))`], { input: image.split(',')[1], encoding: 'utf8', timeout: 5000 })
        assert.equal(decoded.status, 0, decoded.stderr)
        calls.push({ question: '图像能力测试', body })
        res.writeHead(200, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ choices: [{ finish_reason: 'stop', message: { role: 'assistant', content: decoded.stdout } }] })); return
      }
      const question = questionOf(body.messages), executed = toolResults(body.messages)
      assert.equal(body.stream, true, '伙伴必须使用真正SDK流式请求')
      assert(['enabled','disabled'].includes(body.thinking?.type))
      if (executed.length) assert(body.messages.some(message => message.role === 'assistant' && message.reasoning_content === privateReasoning), '思考内容仅在同轮工具链中回传')
      calls.push({ question, body })
      if (question.includes('任务四')) {
        const timer = setTimeout(() => { timers.delete(timer); if (!res.destroyed) res.end(JSON.stringify(toolResponse('final_result', { answer: '迟到回答不得出现', source_ids: [] }, calls.length))) }, 5000)
        timers.add(timer); res.writeHead(200, { 'Content-Type': 'application/json' }); return
      }
      if (question.includes('任务五')) {
        res.writeHead(429, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: { message: '不要透传的上游错误 ' + testKey, type: 'rate_limit' } })); return
      }
      let name = 'final_result', args
      if (question.includes('任务九')) {
        const read = executed.filter(item => item.name === 'read_visual')
        if (read.length < fixtures.imageFormats.length) {
          const fixture = fixtures.imageFormats[read.length]
          name = 'read_visual'; args = { document_id: fixture.id, pages: [fixture.frame] }
        } else {
          const images = body.messages.flatMap(item => Array.isArray(item.content) ? item.content : []).filter(part => part.type === 'image_url')
          assert.equal(images.length, 5)
          for (const image of images) assert(image.image_url.url.startsWith('data:image/png;base64,'))
          const decoded = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', `import sys,json,base64
from io import BytesIO
from PIL import Image
for index,encoded in enumerate(json.load(sys.stdin)):
    data=base64.b64decode(encoded,validate=True)
    assert data.startswith(bytes([137,80,78,71,13,10,26,10]))
    with Image.open(BytesIO(data)) as image:
        assert image.format=="PNG" and image.size==(80,60)
        color=image.convert("RGB").getpixel((40,30))
    expected=(0,0,255) if index==4 else (255,0,0)
    assert all(abs(actual-target)<=10 for actual,target in zip(color,expected)), (index,color,expected)
print("actual PNG pixels verified")`], { input: JSON.stringify(images.map(image => image.image_url.url.split(',')[1])), encoding: 'utf8', timeout: 5000 })
          assert.equal(decoded.status, 0, decoded.stderr)
          for (const [index, item] of read.entries()) assert.equal(item.value.document_id, fixtures.imageFormats[index].id)
          assert.equal(read.at(-1).value.page_count, 2)
          assert.deepEqual(read.at(-1).value.read_pages, [2])
          const ids = read.map(item => item.value.images[0].source_id)
          args = { answer: `已实际读取JPEG、GIF、WebP、BMP及双页TIFF第2帧。${ids.map(id => `[${id}]`).join(' ')}`, source_ids: ids }
        }
      } else if (question.includes('任务八')) {
        const overview = executed.find(item => item.name === 'document_overview')
        const embedded = executed.find(item => item.name === 'read_embedded_images')
        const presentation = executed.find(item => item.name === 'read_document')
        const tables = executed.filter(item => item.name === 'read_table')
        if (!overview) { name = 'document_overview'; args = { document_id: fixtures.embedded.id } }
        else if (!embedded) {
          assert.equal(overview.value.visual_asset_count, 1)
          assert.equal(overview.value.unit_count, 0, '纯图Word无文字，不代表没有可读图片')
          name = 'read_embedded_images'; args = { document_id: fixtures.embedded.id, image_ids: [overview.value.visual_assets[0].image_id] }
        } else if (!presentation) {
          assert(JSON.stringify(body.messages).includes('data:image/png;base64,'))
          assert.equal(embedded.value.images[0].location.image_index, 1)
          assert(!('page' in embedded.value.images[0].location), 'Word内嵌图不可冒称原始页码')
          name = 'read_document'; args = { document_id: fixtures.presentation.id }
        } else if (tables.length < 2) {
          assert(JSON.stringify(presentation.value).includes('演示文稿中的安全说明'))
          name = 'read_table'; args = { document_id: tables.length ? fixtures.openTable.id : fixtures.csv.id, start_row: 1, row_count: 80 }
        } else {
          assert(JSON.stringify(tables).includes('带逗号,的设备'))
          assert(JSON.stringify(tables).includes('开放表格设备'))
          const ids = [embedded.value.images[0].source_id, presentation.value.units[0].source_id, ...tables.map(item => item.value.units[0].source_id)]
          args = { answer: `已读取Word内嵌图、PPTX正文、CSV及ODS表格，内嵌图不代表Word整页布局。${ids.map(id => `[${id}]`).join(' ')}`, source_ids: ids }
        }
      } else if (question.includes('任务六')) {
        const isSummary = body.messages.some(message => typeof message.content === 'string' && message.content.includes('"purpose"') && message.content.includes('progress_summary'))
        if (continuationAuthorized) {
          assert(content.includes(continuationEvidence), '续做必须保留超过300字摘录的完整已读证据')
          assert(content.includes('暂停前说明.md'), '续做必须保留此前生成的草稿')
          args = { answer: '已从上次阅读进度继续完成分析，原有证据与草稿得到保留。[S1]', source_ids: ['S1'] }
        } else if (isSummary) {
          args = { answer: '阶段总结：已阅读资料并生成草稿，剩余核对等待确认继续。[S1]', source_ids: ['S1'] }
        } else if (!executed.some(item => item.name === 'read_document')) {
          name = 'read_document'; args = { document_id: fixtures.resume.id, offset: 0, limit: 20 }
        } else if (!executed.some(item => item.name === 'create_draft')) {
          name = 'create_draft'; args = { name: '暂停前说明.md', content: '# 阶段说明\n\n已有原文依据，后续还需继续核对。[S1]', source_ids: ['S1'] }
        } else {
          name = 'read_document'; args = { document_id: fixtures.resume.id, offset: 20, limit: 20 }
        }
      } else if (question.includes('任务七')) {
        const overview = executed.find(item => item.name === 'document_overview')
        const table = executed.find(item => item.name === 'read_table')
        if (!overview) { name = 'document_overview'; args = { document_id: fixtures.table.id } }
        else if (!table) { name = 'read_table'; args = { document_id: fixtures.table.id, sheet: '设备', start_row: 1, row_count: 80 } }
        else {
          assert(table.value.returned_cells >= 400, '一次行块读取应覆盖至少400个单元格')
          assert.equal(table.value.row_end, 80)
          assert.equal(table.value.has_more, false)
          assert(JSON.stringify(table.value).includes('A80'))
          const sourceId = table.value.units[0].source_id
          args = { answer: `已按工作表行块读取设备表第1至80行、400个单元格，并完成核对。[${sourceId}]`, source_ids: [sourceId] }
        }
      } else if (question.includes('任务一')) {
        const searched = executed.find(item => item.name === 'search_documents')
        const read = executed.filter(item => item.name === 'read_document')
        if (!searched) { name = 'search_documents'; args = { query: '青川项目', limit: 8, offset: 0 } }
        else if (read.length < 2) {
          const candidates = searched.value.documents.filter(item => item.name.endsWith('.txt'))
          assert.equal(candidates.length, 2)
          name = 'read_document'; args = { document_id: candidates[read.length].document_id, offset: 0, limit: 12 }
        } else {
          const sourceIds = read.map(item => item.value.units[0].source_id)
          assert(JSON.stringify(read).includes('验收记录'))
          if (!executed.some(item => item.name === 'create_draft')) {
            name = 'create_draft'; args = { name: '青川项目分析说明.md', content: `# 青川项目分析说明\n\n已对照两份资料，验收记录应逐项核对。[${sourceIds[0]}] [${sourceIds[1]}]\n`, source_ids: sourceIds }
          } else args = { answer: `## 核对结果\n\n两份青川项目资料分别记录了计划和验收情况。[${sourceIds[0]}] [${sourceIds[1]}]\n\n已生成分析说明草稿，等待你确认收录。\n\n|资料|用途|\n|---|---|\n|计划|安排工作|\n|验收|核对结果|\n\n- 核对设备\n- 核对日期\n\n\`\`\`text\n保留代码格式\n\`\`\`\n\n<img src="https://example.test/private.png" onerror="window.__partnerUnsafe=1"><script>window.__partnerUnsafe=1</script>\n\n[参考](https://example.test/reference)`, source_ids: sourceIds }
        }
      } else if (question.includes('任务二')) {
        assert(body.messages.some(message => message.role === 'assistant' && typeof message.content === 'string' && message.content.includes('分别记录了计划和验收')))
        assert(content.includes('验收记录'), '普通追问需真正接续上一轮已读全文证据')
        assert.equal(executed.length, 0, '接续已读证据无需再次读文件')
        assert(!body.messages.some(message => message.role === 'assistant' && message.reasoning_content), '不跨轮发送原始思考内容')
        assert.equal(body.thinking.type, 'disabled')
        args = { answer: '我们继续上轮讨论，下一步可核对验收清单，已接续有效的原文依据。[S1]', source_ids: ['S1'] }
      } else if (question.includes('任务三')) {
        const read = executed.filter(item => item.name === 'read_visual')
        if (read.length === 0) { name = 'read_visual'; args = { document_id: fixtures.pdf.id, pages: [1] } }
        else if (read.length === 1) {
          assert(JSON.stringify(body.messages).includes('data:image/png;base64,'))
          name = 'read_visual'; args = { document_id: fixtures.image.id, pages: [1] }
        } else {
          const images = body.messages.flatMap(message => Array.isArray(message.content) ? message.content : []).filter(part => part.type === 'image_url')
          assert(images.length >= 2)
          for (const item of images) assert(Buffer.from(item.image_url.url.split(',')[1], 'base64').subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10])))
          const sourceIds = read.map(item => item.value.images[0].source_id)
          args = { answer: `已读取图纸原始第1页和独立图片，图形细节需要结合原件继续核对。[${sourceIds[0]}] [${sourceIds[1]}]`, source_ids: sourceIds }
        }
      } else args = { answer: '可以一起讨论。', source_ids: [] }
      await streamToolResponse(res, name, args, calls.length, body.thinking.type === 'enabled', question.includes('任务一') && name === 'final_result')
    } catch (error) {
      errors.push('本机假模型：' + error.message)
      if (!res.headersSent) res.writeHead(500, { 'Content-Type': 'application/json' })
      res.end('{}')
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
    assert(binary.startsWith(path.join(repo, 'dist') + path.sep) && fs.statSync(binary).isFile())
  }
  const env = { PATH: process.env.PATH, HOME: process.env.HOME, LANG: 'zh_CN.UTF-8', PYTHONPATH: repo,
    TEAMHUB_SANDBOX: '1', ELECTRON_APP: 'true', TEAMHUB_AI_CAPABILITY: capability,
    USER_DATA_DIR: path.join(temp, 'backend-profile'), TEAMHUB_AI_CONFIG_DIR: path.join(temp, 'ai-config'),
    LIBRARY_PATH: library, PORT: new URL(api).port, TRASH_AUTO_CLEAN_DAYS: '0', AUTO_SCAN_ON_STARTUP: 'false' }
  const log = path.join(temp, 'personal.log'), fd = fs.openSync(log, 'w')
  const child = spawn(binary || path.join(repo, 'venv/bin/python'), binary ? [] : ['-m', 'uvicorn', 'main:app', '--app-dir', repo, '--host', '127.0.0.1', '--port', new URL(api).port], { cwd: temp, env, stdio: ['ignore', fd, fd] })
  children.push(child); fs.closeSync(fd)
  await until(async () => { if (child.exitCode !== null) throw new Error('后端退出：' + fs.readFileSync(log, 'utf8')); try { return (await fetch(api + '/api/health')).ok } catch { return false } }, '隔离后端就绪')
}
async function ingest(name, content) {
  const source = path.join(temp, name); fs.writeFileSync(source, content)
  return json('/api/documents/quick-add', { method: 'POST', body: { source_path: source } })
}
async function setupFixtures() {
  const first = await ingest('青川项目A计划.txt', '青川项目\n检修计划要求核对操作与验收记录。\n'), second = await ingest('青川项目B验收.txt', '青川项目\n验收记录中已登记现场检查结果。\n')
  await ingest('其他资料.txt', privateTerm)
  const generated = spawnSync(path.join(repo, 'venv/bin/python'), ['-c',
    'import sys\nfrom pathlib import Path\nfrom pypdf import PdfWriter\nfrom pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject\nfrom PIL import Image\nr=Path(sys.argv[1]);w=PdfWriter();p=w.add_blank_page(400,300);s=DecodedStreamObject();s.set_data(b"40 40 120 100 re S");p[NameObject("/Contents")]=w._add_object(s);w.write(r/"原始图纸.pdf");Image.new("RGB",(240,120),"red").save(r/"现场图片.png")', temp], { encoding: 'utf8' })
  assert.equal(generated.status, 0, generated.stderr)
  const pdf = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, '原始图纸.pdf') } })
  const image = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, '现场图片.png') } })
  const resume = await ingest('持续任务资料.txt', '完整原文前部。'.repeat(90) + continuationEvidence + '\n' + Array.from({ length: 60 }, (_, index) => `第${index + 2}行：需逐项核对的资料记录。`).join('\n'))
  const workbookPython = `import sys
from pathlib import Path
from zipfile import ZipFile
r = Path(sys.argv[1])
s = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
a = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
rel = "http://schemas.openxmlformats.org/package/2006/relationships"
rows = "".join(f'<row r="{i}">' + "".join(f'<c r="{c}{i}"><v>{i}</v></c>' for c in "ABCDE") + "</row>" for i in range(1, 81))
with ZipFile(r / "多行设备表.xlsx", "w") as z:
    z.writestr("xl/workbook.xml", f'<workbook xmlns="{s}" xmlns:r="{a}"><sheets><sheet name="设备" r:id="rId1"/></sheets></workbook>')
    z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships xmlns="{rel}"><Relationship Id="rId1" Type="{a}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
    z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{s}"><sheetData>{rows}</sheetData></worksheet>')
`
  const workbook = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', workbookPython, temp], { encoding: 'utf8' })
  assert.equal(workbook.status, 0, workbook.stderr)
  const table = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, '多行设备表.xlsx') } })
  const officePython = `import sys
from pathlib import Path
from zipfile import ZipFile
r=Path(sys.argv[1])
w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"
a="http://schemas.openxmlformats.org/drawingml/2006/main"
rr="http://schemas.openxmlformats.org/officeDocument/2006/relationships"
rel="http://schemas.openxmlformats.org/package/2006/relationships"
p="http://schemas.openxmlformats.org/presentationml/2006/main"
with ZipFile(r/"纯图Word.docx","w") as z:
    z.writestr("word/document.xml",f'<w:document xmlns:w="{w}" xmlns:a="{a}" xmlns:r="{rr}"><w:body><w:p><w:r><w:drawing><a:blip r:embed="img1"/></w:drawing></w:r></w:p></w:body></w:document>')
    z.writestr("word/_rels/document.xml.rels",f'<Relationships xmlns="{rel}"><Relationship Id="img1" Type="{rr}/image" Target="media/image1.png"/></Relationships>')
    z.writestr("word/media/image1.png",(r/"原图.png").read_bytes())
with ZipFile(r/"安全说明.pptx","w") as z:
    z.writestr("ppt/presentation.xml",f'<p:presentation xmlns:p="{p}" xmlns:r="{rr}"><p:sldIdLst><p:sldId id="256" r:id="slide1"/></p:sldIdLst></p:presentation>')
    z.writestr("ppt/_rels/presentation.xml.rels",f'<Relationships xmlns="{rel}"><Relationship Id="slide1" Type="{rr}/slide" Target="slides/slide1.xml"/></Relationships>')
    z.writestr("ppt/slides/slide1.xml",f'<p:sld xmlns:p="{p}" xmlns:a="{a}"><p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>演示文稿中的安全说明</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld></p:sld>')
with ZipFile(r/"开放设备表.ods","w") as z:
    z.writestr("content.xml",'<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"><office:body><office:spreadsheet><table:table table:name="设备"><table:table-row><table:table-cell office:value-type="string"><text:p>开放表格设备</text:p></table:table-cell></table:table-row></table:table></office:spreadsheet></office:body></office:document-content>')
`
  // 收录会移动源文件，专门保留一张合成原图供生成Office测试包使用。
  const originalImage = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', 'from PIL import Image;import sys;Image.new("RGB",(100,60),"blue").save(sys.argv[1])', path.join(temp, '原图.png')], { encoding: 'utf8' })
  assert.equal(originalImage.status, 0, originalImage.stderr)
  const office = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', officePython, temp], { encoding: 'utf8' })
  assert.equal(office.status, 0, office.stderr)
  const embedded = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, '纯图Word.docx') } })
  const presentation = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, '安全说明.pptx') } })
  const openTable = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, '开放设备表.ods') } })
  const csv = await ingest('逗号设备表.csv', '\ufeff设备,状态\n"带逗号,的设备",正常\n')
  const formats = spawnSync(path.join(repo, 'venv/bin/python'), ['-c', `from pathlib import Path
from PIL import Image
import sys
r=Path(sys.argv[1])
for suffix in ("jpg","gif","webp","bmp"):
    Image.new("RGB",(80,60),"red").save(r/("合成图片."+suffix))
Image.new("RGB",(80,60),"red").save(r/"双页图片.tiff",save_all=True,append_images=[Image.new("RGB",(80,60),"blue")])`, temp], { encoding: 'utf8' })
  assert.equal(formats.status, 0, formats.stderr)
  const imageFormats = []
  for (const suffix of ['jpg', 'gif', 'webp', 'bmp', 'tiff']) {
    const entry = await json('/api/documents/quick-add', { method: 'POST', body: { source_path: path.join(temp, suffix === 'tiff' ? '双页图片.tiff' : '合成图片.' + suffix) } })
    imageFormats.push({ id: entry.id, frame: suffix === 'tiff' ? 2 : 1 })
  }
  return { first, second, pdf, image, resume, table, embedded, presentation, openTable, csv, imageFormats }
}
async function startDesktop() {
  const bundled = '/Applications/ChatGPT.app/Contents/Resources/cua_node/lib/node_modules/playwright-core'
  const { _electron } = require(process.env.PLAYWRIGHT_MODULE || (fs.existsSync(bundled) ? bundled : 'playwright'))
  const fixture = path.join(temp, 'fixture.json'); fs.writeFileSync(fixture, JSON.stringify({ repo, temp, api, capability }), { mode: 0o600 })
  const env = { ...process.env, TEAMHUB_AI_FIXTURE: fixture }; delete env.ELECTRON_RUN_AS_NODE
  desktop = await _electron.launch({ executablePath: path.join(repo, 'node_modules/electron/dist/Electron.app/Contents/MacOS/Electron'), args: [path.join(repo, 'scripts/test_ai_assist_desktop_app.cjs')], env })
  await desktop.evaluate(({ BrowserWindow }) => BrowserWindow.getAllWindows()[0].setSize(1280, 800))
  page = await desktop.firstWindow(); page.setDefaultTimeout(15000); context = desktop.context()
  await context.tracing.start({ screenshots: true, snapshots: true, sources: true }); tracing = true
  page.on('pageerror', error => errors.push(error.message))
  await context.route('**/*', route => {
    const url = new URL(route.request().url())
    if (['http:', 'https:'].includes(url.protocol) && url.origin !== api) { externalRequests.push(url.href); return route.abort() }
    return route.continue()
  })
  await page.goto('file://' + path.join(repo, 'frontend/dist/index.html') + '#/pending')
  const enter = page.getByRole('button', { name: '打开文件库', exact: true })
  await enter.waitFor(); await enter.click()
  await page.locator('.pending-page').waitFor()
  await page.locator('.nav-primary .nav-item').filter({ hasText: 'AI 伙伴' }).click()
  await page.getByTestId('partner-page').waitFor()
}
function questionField() {
  return page.locator('[data-testid="partner-question"]:is(input,textarea),[data-testid="partner-question"] textarea')
}
async function prepareQuestion(question) {
  await questionField().fill(question); await page.getByTestId('partner-prepare').click()
  await page.getByRole('dialog', { name: '确认本轮 AI 讨论' }).waitFor()
  assert(await page.getByTestId('partner-send').isDisabled())
}
async function sendQuestion() {
  const started = page.waitForResponse(response => response.url().includes('/api/ai/partner/') && /\/(turns|continue)$/.test(response.url()) && response.request().method() === 'POST')
  await page.getByTestId('partner-consent').click(); await page.getByTestId('partner-send').click()
  const response = await started; assert.equal(response.status(), 202, await response.text())
  const task = await response.json(), submitted = response.request().postDataJSON()
  return { ...task, preparation_id: submitted.preparation_id, request_id: submitted.request_id }
}
async function taskDone(id, expected = 'completed') {
  return until(async () => { const task = await partner('/tasks/' + id); if (task.state === expected) return task; if (['failed', 'cancelled', 'interrupted'].includes(task.state)) throw new Error(JSON.stringify(task.error)); return false }, '伙伴任务 ' + expected)
}
async function capture(name) { await page.screenshot({ path: path.join(temp, name), fullPage: true }) }

async function main() {
  await startProvider(); await startBackend()
  await json('/api/ai/settings', { method: 'PUT', body: { base_url: providerBase, model, api_key: testKey } })
  assert.equal((await request('/api/ai/partner/sessions', { protectedAI: false })).status, 403)
  fixtures = await setupFixtures(); await startDesktop()
  assert.equal(calls.length, 0)
  await page.getByText('整个个人库', { exact: true }).click()
  await prepareQuestion('任务一：自主查找青川项目资料，比较计划与验收情况，并生成分析说明。')
  assert.equal(calls.length, 0, '授权准备不调用模型')
  const task = await sendQuestion()
  await page.getByTestId('partner-answer-preview').filter({ hasText:'两份青川项目资料' }).waitFor()
  assert.equal((await partner('/tasks/' + task.id)).state, 'running', '预览出现时上游尚未完成')
  assert.equal((await partner('/sessions/' + task.session_id)).messages.filter(item => item.role === 'assistant').length, 0)
  assert.equal(await page.getByTestId('partner-message-assistant').count(), 0)
  await capture('partner-streaming-preview.png')
  releaseAnswerPreview(); releaseAnswerPreview = undefined
  const completed = await taskDone(task.id)
  assert(completed.sources.length >= 2); assert.equal(completed.artifacts.length, 1)
  const role = page.getByTestId('partner-message-assistant').filter({ hasText: '分别记录了计划和验收' }); await role.waitFor()
  assert.equal(await role.locator('h2').textContent(), '核对结果')
  assert.equal(await role.locator('table tbody tr').count(), 2)
  assert.equal(await role.locator('pre code').textContent(), '保留代码格式\n')
  assert.equal(await role.locator('img,script,svg').count(), 0)
  assert.equal(await page.evaluate(() => window.__partnerUnsafe), undefined)
  assert.equal(await role.locator('a').getAttribute('rel'), 'noopener noreferrer')
  await role.locator('a').click()
  await until(() => fs.existsSync(path.join(temp, 'opened-reference-links.json')), '显式点击参考链接已通过桌面桥')
  assert.deepEqual(JSON.parse(fs.readFileSync(path.join(temp, 'opened-reference-links.json'), 'utf8')), ['https://example.test/reference'])
  assert((await role.locator('.source-chip').first().textContent()).includes('[S1]'))
  await role.locator('.source-chip').first().click()
  assert((await page.getByRole('dialog', { name:'查看资料依据' }).locator('.source-title').textContent()).includes('[S1]'))
  await page.getByRole('dialog', { name:'查看资料依据' }).getByRole('button', { name:'关闭', exact:true }).click()
  assert(!(await page.locator('body').textContent()).includes(privateReasoning))
  assert.equal(await page.getByTestId('partner-task-details').getAttribute('open'), null)
  assert.equal((await request(`/api/ai/partner/tasks/${task.id}/events`, { protectedAI:false })).status, 403)
  pass('真实SSE在上游完成前显示预览；核验前无正式回答，Markdown表格代码正常且HTML与远程图片不会执行，思考内容不泄露')
  assert.deepEqual(new Set(calls.filter(item => item.question.includes('任务一')).flatMap(item => item.body.messages.flatMap(message => message.tool_calls || []).map(tool => tool.function.name))), new Set(['search_documents', 'read_document', 'create_draft']))
  await capture('partner-analysis.png')
  const originalTask = await partner(`/tasks/${task.id}`)
  const repeated = await partner(`/sessions/${task.session_id}/turns`, { method: 'POST', body: { preparation_id: task.preparation_id, consent: true, request_id: task.request_id } })
  assert.equal(repeated.id, task.id)
  pass('真实工具循环自主搜索、读取两份固定版本、比较并生成草稿；未确认零外发，重复启动复用原任务')

  await page.getByTestId('partner-save-artifact').click()
  await page.getByRole('dialog', { name: '收录生成成果', exact: true }).getByRole('button', { name: '确认收录', exact: true }).click()
  await page.getByRole('button', { name: '查看已收录资料', exact: true }).waitFor()
  const savedTask = await partner('/tasks/' + task.id), artifact = savedTask.artifacts[0]
  assert(artifact.saved_document_id)
  const doc = await json('/api/documents/' + artifact.saved_document_id)
  assert.equal(doc.status, 'pending'); assert.equal(doc.current_version.version_number, 1)
  const saved = await partner(`/tasks/${task.id}/artifacts/${artifact.id}/save`, { method: 'POST', body: { consent: true, request_id: crypto.randomUUID() } })
  assert.equal(saved.document_id, artifact.saved_document_id)
  assert.equal((await json('/api/documents/' + fixtures.first.id)).current_version.id, fixtures.first.current_version.id)
  assert.equal((await json('/api/documents/' + fixtures.second.id)).current_version.id, fixtures.second.current_version.id)
  assert.equal(originalTask.sources[0].version_number, 1)
  pass('成果经真实界面确认后收录待整理并建立V1；重复保存无冗余，原资料版本不变')

  await page.getByTestId('partner-thinking-mode').getByText('快速回答', { exact:true }).click()
  await prepareQuestion('任务二：继续上轮讨论，下一步应该核对什么？')
  await page.getByTestId('partner-conversation-context').waitFor()
  assert((await page.getByRole('dialog', { name:'确认本轮 AI 讨论' }).textContent()).includes('验收记录'))
  const followup = await sendQuestion(); await taskDone(followup.id)
  await page.getByTestId('partner-message-assistant').filter({ hasText: '我们继续上轮讨论' }).waitFor()
  await page.reload()
  const reopen = page.getByRole('button', { name: '打开文件库', exact: true })
  await reopen.waitFor(); await reopen.click()
  await page.locator('.nav-primary .nav-item').filter({ hasText: 'AI 伙伴' }).click()
  await page.getByTestId('partner-page').waitFor()
  const detail = await partner('/sessions/' + task.session_id); assert.equal(detail.messages.length, 4)
  assert.equal(detail.tasks.find(item => item.id === followup.id).sources[0].version_id, completed.sources[0].version_id)
  pass('快速回答与预算独立；追问经确认恢复有效原文并直接引用，零重复阅读，刷新保留正式会话与成果')

  await page.getByTestId('partner-new-session').click(); await page.getByTestId('partner-scope').click()
  await page.getByRole('dialog', { name: '选择本轮讨论的资料' }).waitFor()
  for (const docId of [fixtures.pdf.id, fixtures.image.id]) await page.getByTestId('partner-select-document-' + docId).click()
  await page.getByRole('dialog', { name: '选择本轮讨论的资料' }).getByRole('button', { name: '完成', exact: true }).click()
  await prepareQuestion('任务三：查看图纸原始页面和现场图片，结合原貌一起分析。')
  const visual = await sendQuestion(), visualDone = await taskDone(visual.id)
  assert.equal(visualDone.sources.length, 2); assert(visualDone.steps.some(step => step.kind === 'visual'))
  await page.getByTestId('partner-message-assistant').filter({ hasText: '已读取图纸原始第1页' }).waitFor()
  await capture('partner-original-pages.png')
  pass('资料选择器授权PDF与图片，真实原页PNG进入模型请求，引用固定版本及页码')

  await page.getByTestId('partner-new-session').click(); await prepareQuestion('任务四：持续等待，测试停止任务。')
  const cancelTask = await sendQuestion(); await until(() => calls.some(call => call.question.includes('任务四')), '慢请求已发送')
  await page.getByTestId('partner-cancel').click()
  await until(async () => (await partner('/tasks/' + cancelTask.id)).state === 'cancelled', '停止任务')
  assert.equal((await partner('/tasks/' + cancelTask.id)).answer, null)
  pass('停止正在进行的模型任务，取消后不保存迟到回答')

  await page.getByTestId('partner-new-session').click(); await prepareQuestion('任务五：验证服务限流错误。')
  const failed = await sendQuestion()
  await until(async () => (await partner('/tasks/' + failed.id)).state === 'failed', '限流错误')
  const error = (await partner('/tasks/' + failed.id)).error
  assert.equal(error.code, 'AI_RATE_LIMITED'); assert(!JSON.stringify(error).includes(testKey))
  await page.getByText('AI 服务请求较多，请稍后重新确认再试', { exact: true }).waitFor()
  assert.equal(calls.filter(call => call.question.includes('任务五')).length, 1)
  assert.deepEqual(errors, []); assert.deepEqual(externalRequests, [])
  assert(!fs.readFileSync(path.join(temp, 'personal.log'), 'utf8').includes(testKey))
  await capture('partner-service-error.png')
  pass('限流安全报错且零自动重试；生产页面无异常、渲染器无外部请求、日志无密钥')

  await page.getByTestId('partner-new-session').click()
  await page.getByTestId('partner-budget-mode').getByText('自定义', { exact: true }).click()
  for (const [field, value] of Object.entries({ 'model-requests': 4, 'tool-calls': 8, 'output-tokens': 8000, 'time-seconds': 180 })) {
    const input = page.locator(`[data-testid="partner-budget-${field}"] input,[data-testid="partner-budget-${field}"]:is(input)`)
    await input.fill(String(value)); await input.press('Tab')
  }
  await page.getByTestId('partner-scope').click()
  await page.getByTestId('partner-select-document-' + fixtures.resume.id).click()
  await page.getByRole('dialog', { name: '选择本轮讨论的资料' }).getByRole('button', { name: '完成', exact: true }).click()
  await prepareQuestion('任务六：分步核对资料并生成阶段说明，预算不足时保留进度。')
  const limited = await sendQuestion(), paused = await taskDone(limited.id, 'paused')
  assert.equal(paused.pause.reason, 'model_requests'); assert.equal(paused.pause.resumable, true)
  assert(paused.sources.length > 0); assert.equal(paused.artifacts.length, 1)
  assert(paused.usage.model_requests <= 4)
  assert(!JSON.stringify(paused).includes('continuation_context'), '任务读取不应暴露内部续做上下文')
  const continueAction = page.locator('.composer').getByTestId('partner-continue')
  await continueAction.waitFor()
  const continueBox = await continueAction.boundingBox()
  assert(continueBox && continueBox.y >= 0 && continueBox.y + continueBox.height <= await page.evaluate(() => innerHeight), '继续操作应位于可见预算区域，不能被长来源列表埋住')
  await page.getByTestId('partner-partial-answer').filter({ hasText: '阶段总结：已阅读资料并生成草稿' }).waitFor()
  await capture('partner-budget-paused.png')
  await page.reload()
  await page.getByRole('button', { name: '打开文件库', exact: true }).click()
  await page.locator('.nav-primary .nav-item').filter({ hasText: 'AI 伙伴' }).click()
  await page.getByTestId('partner-continue').waitFor()
  const beforeContinue = calls.filter(call => call.question.includes('任务六')).length
  await page.getByTestId('partner-continue').click()
  await page.getByRole('dialog', { name: '确认继续处理', exact: true }).waitFor()
  assert.equal(calls.filter(call => call.question.includes('任务六')).length, beforeContinue)
  assert(await page.getByTestId('partner-send').isDisabled())
  continuationAuthorized = true
  const continued = await sendQuestion(); assert.equal(continued.id, limited.id)
  const resumed = await taskDone(limited.id)
  assert(resumed.answer.includes('已从上次阅读进度继续'))
  assert.equal(resumed.artifacts[0].id, paused.artifacts[0].id)
  const afterContinue = calls.filter(call => call.question.includes('任务六')).length
  await partner(`/tasks/${limited.id}/continue`, { method: 'POST', body: { preparation_id: continued.preparation_id, consent: true, request_id: continued.request_id } })
  assert.equal(calls.filter(call => call.question.includes('任务六')).length, afterContinue)
  assert.equal((await partner('/sessions/' + limited.session_id)).messages.filter(message => message.role === 'assistant').length, 1)
  await capture('partner-budget-resumed.png')
  pass('自定义预算达限保留来源草稿与全文证据；刷新恢复、再次确认后续做同一任务，重复续做无额外请求')

  await page.getByTestId('partner-new-session').click()
  await page.getByTestId('partner-budget-mode').getByText('标准', { exact: true }).click()
  await page.getByTestId('partner-scope').click()
  await page.getByTestId('partner-select-document-' + fixtures.table.id).click()
  await page.getByRole('dialog', { name: '选择本轮讨论的资料' }).getByRole('button', { name: '完成', exact: true }).click()
  await prepareQuestion('任务七：查看设备表整体结构并核对所有80行。')
  const tabular = await sendQuestion(), tableResult = await taskDone(tabular.id)
  assert(tableResult.answer.includes('400个单元格'))
  assert(tableResult.steps.some(step => /1.*80/.test(step.message)))
  assert.equal(calls.filter(call => call.question.includes('任务七')).length, 3)
  assert.deepEqual(errors, []); assert.deepEqual(externalRequests, [])
  await capture('partner-table-batch.png')
  pass('真实xlsx结构浏览后一次行块读取400个单元格，3次模型请求完成并展示行范围')
  await page.getByTestId('partner-new-session').click()
  await page.getByTestId('partner-scope').click()
  for (const id of [fixtures.embedded.id, fixtures.presentation.id, fixtures.csv.id, fixtures.openTable.id]) await page.getByTestId('partner-select-document-' + id).click()
  await page.getByRole('dialog', { name: '选择本轮讨论的资料' }).getByRole('button', { name: '完成', exact: true }).click()
  await prepareQuestion('任务八：一起阅读Word内嵌图、演示文稿和不同格式的表格。')
  const officeTask = await sendQuestion(), officeResult = await taskDone(officeTask.id)
  assert.equal(officeResult.sources.length, 4)
  assert.equal(officeResult.progress.embedded_images, 1)
  await page.getByTestId('partner-message-assistant').filter({ hasText: '已读取Word内嵌图' }).waitFor()
  await capture('partner-office-multimodal.png')
  assert.equal(calls.filter(call => call.question.includes('任务八')).length, 6)
  pass('纯图DOCX实际内嵌PNG进入模型，PPTX正文与CSV/ODS表格通过真实工具读取并保留4条固定来源')
  await page.getByTestId('partner-new-session').click()
  await page.getByText('整个个人库', { exact: true }).click()
  await prepareQuestion('任务九：阅读不同编码的图片，TIFF请只看第二帧。')
  const formatsTask = await sendQuestion(), formatsResult = await taskDone(formatsTask.id)
  assert.equal(formatsResult.sources.length, 5)
  await page.getByTestId('partner-message-assistant').filter({ hasText: '已实际读取JPEG' }).waitFor()
  pass('JPEG/GIF/WebP/BMP真实解码为PNG，双页TIFF按第2帧读取并进入模型请求')
  const beforeSettings = calls.length
  await page.locator('.partner-heading').getByRole('button', { name: 'AI 设置', exact: true }).click()
  await page.getByTestId('ai-settings-test-vision').waitFor()
  assert.equal(calls.length, beforeSettings, '进入设置不会自动发送测试请求')
  await page.getByTestId('ai-settings-test-vision').click()
  await page.getByRole('status').filter({ hasText: '本次测试成功识别合成图' }).waitFor()
  assert.equal(calls.length, beforeSettings + 1)
  assert(!JSON.stringify(calls.at(-1).body).includes('纯图Word'))
  await capture('partner-vision-probe.png')
  pass('真实设置按钮仅主动发送随机合成PNG，假模型按实际像素识别并显示本次图像测试结果，未上传资料')
  assert.deepEqual(errors, []); assert.deepEqual(externalRequests, [])
  await context.tracing.stop({ path: path.join(temp, 'desktop-trace.zip') }); tracing = false
}
function report(status, error) {
  const data = { status, groups: results, providerRequests: calls.length, pageErrors: errors, externalRequests, ...(error ? { error: error.stack } : {}) }
  fs.writeFileSync(path.join(temp, 'results.json'), JSON.stringify(data, null, 2))
  const escape = value => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;' })[char])
  fs.writeFileSync(path.join(temp, 'report.html'), `<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>AI智能伙伴验收</title><h1>${escape(status)}</h1><ul>${results.map(name => `<li>${escape(name)}</li>`).join('')}</ul>${error ? `<pre>${escape(error.stack)}</pre>` : ''}<p>仅使用临时资料和本机假模型，未调用真实收费服务。</p>${fs.readdirSync(temp).filter(name => /\.(png|zip)$/.test(name)).map(name => `<p><a href="${escape(name)}">${escape(name)}</a></p>`).join('')}</html>`)
  console.log('验收产物：' + temp)
}
main().then(() => report('passed'), async error => {
  console.error(error)
  if (page) await capture('failure.png').catch(() => {})
  if (tracing) await context.tracing.stop({ path: path.join(temp, 'failure-trace.zip') }).catch(() => {})
  report('failed', error); process.exitCode = 1
}).finally(async () => {
  if (desktop) await desktop.close().catch(() => {})
  for (const child of children.reverse()) if (child.exitCode === null) child.kill('SIGTERM')
  for (const timer of timers) clearTimeout(timer)
  if (provider) { provider.closeAllConnections(); await new Promise(resolve => provider.close(resolve)) }
})
