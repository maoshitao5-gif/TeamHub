import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer, h, nextTick, reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
const renderer = createRenderer({ createComment: () => ({}), createElement: () => ({}), createText: () => ({}), insert() {}, remove() {}, patchProp() {}, setElementText() {}, setText() {}, parentNode: () => null, nextSibling: () => null })
let serial = 0
const session = { id: 'session-a', title: '资料讨论', active_task_id: null }
const document = { id: 'doc-a', name: '说明.txt', status: 'organized', is_folder: false, current_version: { id: 'version-a', version_number: 1 } }
const source = { id: 'source-a', document_id: 'doc-a', name: '说明.txt', version_id: 'version-a', version_number: 1, label: '第 1 段', location: { paragraph: 1 }, excerpt: '固定版本的原文' }
const running = { id: 'task-a', session_id: 'session-a', state: 'running', message: '解释资料', answer: null, steps: [{ id: 'step-a', kind: 'search', message: '正在查找资料' }], sources: [], artifacts: [], error: null }
const finished = { ...running, state: 'completed', answer: '<img src=x onerror=alert(1)>\n答案', sources: [source], artifacts: [{ id: 'artifact-a', name: '分析说明.md', content: '# 分析说明\n成果正文', media_type: 'text/markdown', saved_document_id: null }] }
const budgetPolicy = { default_mode: 'standard', presets: { quick: { label: '快速', model_requests: 12, tool_calls: 24, output_tokens: 16000, time_seconds: 180 }, standard: { label: '标准', model_requests: 32, tool_calls: 80, output_tokens: 48000, time_seconds: 600 }, deep: { label: '深入', model_requests: 64, tool_calls: 160, output_tokens: 96000, time_seconds: 1800 } }, bounds: { model_requests: { min: 4, max: 128 }, tool_calls: { min: 8, max: 256 }, output_tokens: { min: 4000, max: 128000 }, time_seconds: { min: 60, max: 3600 } } }
const paused = { ...finished, state: 'paused', answer: null, pause: { reason: 'model_requests', message: '本段模型请求预算已用完，已保留阅读进度。', resumable: true }, usage: { model_requests: 32, tool_calls: 45, output_tokens: 22000, time_seconds: 170 }, segment_usage: { model_requests: 32, tool_calls: 45, output_tokens: 22000, time_seconds: 170 }, limits: budgetPolicy.presets.standard }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 12; i++) { await Promise.resolve(); await nextTick() } }

async function fixture(overrides = {}, query = {}, storedBudget = null, submissionRegistry = new Map()) {
  const calls = [], timers = new Map(), routes = [], notices = []
  const storage = new Map(storedBudget === null ? [] : [['teamhub.partner.budget.v1', storedBudget]])
  const appStore = reactive({ libraryPath: '/temporary/library-a', fetchPendingCount: async () => {} })
  const route = reactive({ query, path: '/partner' })
  let timerSerial = 0
  const record = (name, value) => async (...args) => { calls.push({ name, args }); return typeof value === 'function' ? value(...args) : structuredClone(value) }
  const api = {
    getPartnerChatConfig: record('chat-config', { configured: true, has_api_key: true, service_revision: 'service-a', service: { base_url: 'https://example.test', model: 'test-model', engine: 'hermes' }, defaults: { scope_mode: 'selected', thinking_mode: 'disabled', budget: { mode: 'custom', model_requests: 64, tool_calls: 160, output_tokens: 128000, time_seconds: 1800 } } }),
    sendPartnerMessage: record('send', running),
    resumePartnerTask: record('resume', running),
    getPartnerBudgetPolicy: record('budget-policy', budgetPolicy),
    listPartnerSessions: record('list', { items: [session] }),
    createPartnerSession: record('create', { ...session, id: 'session-new', title: '新对话' }),
    getPartnerSession: record('detail', id => ({ session: { ...session, id }, messages: [], tasks: [] })),
    deletePartnerSession: record('delete', { deleted: true }),
    getPartnerTask: record('task', finished),
    getPartnerRequestTask: record('request-task', () => { throw Object.assign(new Error('尚未登记'), { response: { status: 404 } }) }),
    streamPartnerTask: record('stream', () => Promise.reject(new Error('测试连接不可用'))),
    cancelPartnerTask: record('cancel', { ...running, state: 'cancelled' }),
    savePartnerArtifact: record('save', { document_id: 'saved-doc', artifact_id: 'artifact-a', duplicate: false }),
    searchDocuments: record('search', { documents: [document], total: 1 }),
    getDocument: record('document', document),
    ...overrides,
  }
  const key = `__partner_component_fixture_${++serial}`
  globalThis[key] = { api, appStore, route, submissionRegistry, router: { push: value => routes.push(value), replace: value => { routes.push(value); route.query = value.query || {} } },
    message: { success: value => notices.push(value), error: value => notices.push(value) },
    messageBox: { confirm: async () => true },
    setTimeout: (callback, delay) => { const id = ++timerSerial; callback.delay = delay; timers.set(id, callback); return id }, clearTimeout: id => timers.delete(id),
    localStorage: { getItem: key => storage.get(key) ?? null, setItem: (key, value) => storage.set(key, value), removeItem: key => storage.delete(key) },
  }
  const sourceText = await fs.readFile(new URL('../src/pages/AIPartnerPage.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(sourceText).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import PartnerMarkdown from '@\/components\/PartnerMarkdown.vue'/, 'const PartnerMarkdown = {}')
    .replace(/import \{ pendingPartnerSubmissions \} from '@\/utils\/partnerSubmissionRegistry'/, `const pendingPartnerSubmissions = globalThis.${key}.submissionRegistry`)
    .replace(/import \{ useRoute, useRouter \} from 'vue-router'/, `const useRoute = () => globalThis.${key}.route; const useRouter = () => globalThis.${key}.router`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{ ElMessage, ElMessageBox \} from 'element-plus'/, `const ElMessage = globalThis.${key}.message; const ElMessageBox = globalThis.${key}.messageBox`)
    .replace(/import \{([^}]+)\} from '@\/api\/(?:partner|ai|document)'/g, (_, names) => names.split(',').map(name => name.trim()).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/\bsetTimeout\(/g, `globalThis.${key}.setTimeout(`).replace(/\bclearTimeout\(/g, `globalThis.${key}.clearTimeout(`)
    .replace(/\blocalStorage\./g, `globalThis.${key}.localStorage.`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  let state
  const app = renderer.createApp({ setup() { return () => h({ ...component, setup(values, context) { state = component.setup(values, context); return () => null } }) } })
  app.mount({}); await settled()
  return { calls, timers, routes, notices, appStore, route, api, sourceText, storage, get state() { return state },
    tick: async () => { await settled(); const current = [...timers.values()]; timers.clear(); for (const callback of current) callback(); await settled() },
    close() { app.unmount(); delete globalThis[key] },
  }
}

test('实时预览先显示，完成核验前不加入正式对话，终态关闭订阅', async () => {
  let update, signal
  const gate = deferred(), f = await fixture({ streamPartnerTask: (id, listener, value) => { update = listener; signal = value; return gate.promise } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    assert.equal(typeof update, 'function'); assert.equal(f.timers.size, 0)
    update({ ...running, answer_preview:'## 正在形成答案' }); await settled()
    assert.equal(f.state.run.value.answer_preview, '## 正在形成答案')
    assert.equal(f.state.messages.value.some(item => item.role === 'assistant'), false)
    update(finished); await settled()
    assert.equal(f.state.messages.value.at(-1).content, finished.answer)
    assert.equal(signal.aborted, true)
  } finally { gate.resolve(); f.close() }
})


test('切换会话或停止后，迟到的实时片段不能写入当前界面', async () => {
  let update, signal
  const gate = deferred(), f = await fixture({ streamPartnerTask: (id, listener, value) => { update = listener; signal = value; return gate.promise } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    assert.equal(typeof update, 'function')
    await f.state.cancelRun(); assert.equal(signal.aborted, true)
    update({ ...running, answer_preview:'迟到的旧回答' }); await settled()
    assert.equal(f.state.run.value.state, 'cancelled')
    await f.state.selectSession('other-session')
    update(finished); await settled()
    assert.equal(f.state.run.value, null); assert.equal(f.state.messages.value.length, 0)
  } finally { gate.resolve(); f.close() }
})


test('工具进度完成后显示固定版引用和成果，成果须确认才能收录', async () => {
  const f = await fixture()
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    await f.tick()
    assert.equal(f.state.run.value.state, 'completed')
    assert.equal(f.state.messages.value.at(-1).content, finished.answer)
    assert.equal(f.state.messages.value.at(-1).sources[0].version_id, 'version-a')
    f.state.openSource(source); assert.equal(f.state.selectedSource.value.excerpt, source.excerpt)
    assert.equal(f.calls.some(call => call.name === 'save'), false)
    await f.state.saveArtifact(finished, finished.artifacts[0])
    assert.equal(f.calls.filter(call => call.name === 'save').length, 1)
    assert.equal(f.state.run.value.artifacts[0].saved_document_id, 'saved-doc')
    assert.match(f.notices.at(-1), /待整理/)
  } finally { f.close() }
})


test('切换会话中止轮询；迟到的任务结果不会污染新会话', async () => {
  const gate = deferred(); let taskSignal
  const f = await fixture({ getPartnerTask: (id, signal) => { taskSignal = signal; return gate.promise } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    await f.tick(); await f.state.selectSession('session-b')
    assert.equal(taskSignal.aborted, true); assert.equal(f.timers.size, 0)
    gate.resolve(finished); await settled()
    assert.equal(f.state.activeSession.value.id, 'session-b')
    assert.equal(f.state.run.value, null); assert.equal(f.state.messages.value.length, 0)
  } finally { f.close() }
})


test('资料详情入口只预选资料，搜索忽略文件夹与不可用版本', async () => {
  const f = await fixture({ searchDocuments: async () => ({ documents: [document, { ...document, id: 'folder', is_folder: true }, { ...document, id: 'missing', status: 'missing' }], total: 3 }) }, { doc_id: 'doc-a' })
  try {
    assert.equal(f.state.selectedDocuments.value[0].id, 'doc-a')
    assert.equal(f.calls.some(call => call.name === 'prepare'), false)
    await f.state.searchScopeDocuments()
    assert.deepEqual(f.state.documentOptions.value.map(item => item.id), ['doc-a'])
  } finally { f.close() }
})


test('停止任务后，较早发出的进度响应不能把已停止任务恢复成运行中', async () => {
  const gate = deferred()
  const f = await fixture({ getPartnerTask: () => gate.promise })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    await f.tick(); await f.state.cancelRun()
    gate.resolve(running); await settled()
    assert.equal(f.state.run.value.state, 'cancelled'); assert.equal(f.timers.size, 0)
  } finally { f.close() }
})


test('停止请求正在确认时不能另发刷新，迟到状态不恢复已停止任务', async () => {
  const cancellation = deferred(), query = deferred(); let queries = 0
  const f = await fixture({ cancelPartnerTask: () => cancellation.promise, getPartnerTask: () => { queries++; return query.promise } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage(); await settled()
    f.state.pollError.value = true
    const stopping = f.state.cancelRun()
    assert.equal(f.state.cancelling.value, true)
    f.state.refreshRun(); await settled()
    assert.equal(queries, 0, '确认停止期间不允许重新发起进度查询')
    cancellation.resolve({ ...running, state:'cancelled' }); await stopping
    query.resolve(running); await settled()
    assert.equal(f.state.run.value.state, 'cancelled'); assert.equal(f.timers.size, 0)
  } finally { cancellation.resolve({ ...running, state:'cancelled' }); query.resolve(running); f.close() }
})


test('主导航与资料详情提供独立的伙伴入口', async () => {
  const [app, routes, drawer] = await Promise.all([
    fs.readFile(new URL('../src/App.vue', import.meta.url), 'utf8'),
    fs.readFile(new URL('../src/router/index.js', import.meta.url), 'utf8'),
    fs.readFile(new URL('../src/components/DocumentDetailDrawer.vue', import.meta.url), 'utf8'),
  ])
  assert.match(app, /handleMenuSelect\('\/partner'\)/)
  assert.match(routes, /path: '\/partner'/); assert.match(routes, /AIPartnerPage\.vue/)
  assert.match(drawer, /与 AI 讨论/); assert.match(drawer, /path: '\/partner', query: \{ doc_id:/)
})


test('重返会话能恢复运行进度与已保存成果，等待无需重复调用模型', async () => {
  const f = await fixture({ getPartnerSession: async id => ({ session: { ...session, id, active_task_id: running.id }, messages: [{ id: 'history-user', role: 'user', content: '先前问题', task_id: 'prior-task', sources: [] }], tasks: [running] }) })
  try {
    assert.equal(f.state.isRunning.value, true); assert.equal(f.timers.size, 1)
    assert.equal(f.calls.some(call => call.name === 'start'), false)
    await f.tick()
    assert.equal(f.state.run.value.state, 'completed'); assert.equal(f.timers.size, 0)
    assert.equal(f.state.artifactTasks.value[0].artifacts[0].id, 'artifact-a')
  } finally { f.close() }
})


test('连续从资料入口进入时，较早创建的会话不能把旧资料加入较新的会话', async () => {
  const first = deferred(), second = deferred(); let creations = 0
  const f = await fixture({
    createPartnerSession: () => ++creations === 1 ? first.promise : second.promise,
    getDocument: async id => ({ ...document, id, name: `${id}.txt` }),
  })
  try {
    f.route.query = { doc_id: 'doc-old' }; await settled()
    f.route.query = { doc_id: 'doc-new' }; await settled()
    second.resolve({ ...session, id: 'session-new' }); await settled()
    assert.equal(f.state.activeSession.value.id, 'session-new')
    assert.deepEqual(f.state.selectedDocuments.value.map(doc => doc.id), ['doc-new'])
    first.resolve({ ...session, id: 'session-old' }); await settled()
    assert.equal(f.state.activeSession.value.id, 'session-new')
    assert.deepEqual(f.state.selectedDocuments.value.map(doc => doc.id), ['doc-new'])
  } finally { f.close() }
})


test('资料入口正在创建会话时手动切换会话，迟到的入口不能追加资料', async () => {
  const creation = deferred(), documentsRead = []
  const f = await fixture({ createPartnerSession: () => creation.promise, getDocument: async id => { documentsRead.push(id); return { ...document, id } } })
  try {
    f.route.query = { doc_id: 'doc-old' }; await settled()
    await f.state.selectSession('manual-session')
    creation.resolve({ ...session, id: 'session-old' }); await settled()
    assert.equal(f.state.activeSession.value.id, 'manual-session')
    assert.deepEqual(f.state.selectedDocuments.value, []); assert.deepEqual(documentsRead, [])
  } finally { f.close() }
})


test('资料入口等待期间切库，旧入口参数和迟到创建都不能继承到新库', async () => {
  const creation = deferred(), documentsRead = []; let creations = 0
  const f = await fixture({
    createPartnerSession: async () => ++creations === 1 ? creation.promise : { ...session, id: 'new-library-session' },
    getDocument: async id => { documentsRead.push(id); return { ...document, id } },
  })
  try {
    f.route.query = { doc_id: 'doc-old' }; await settled()
    f.appStore.libraryPath = '/temporary/library-b'; await settled()
    creation.resolve({ ...session, id: 'old-library-session' }); await settled()
    assert.deepEqual(f.state.selectedDocuments.value, []); assert.deepEqual(documentsRead, [])
    assert.equal(f.route.query.doc_id, undefined)
  } finally { f.close() }
})


test('启动首轮后立即同步后端会话标题', async () => {
  let reads = 0
  const f = await fixture({
    listPartnerSessions: async () => ({ items: [{ ...session, title: ++reads === 1 ? '新对话' : '解释资料' }] }),
    getPartnerSession: async id => ({ session: { ...session, id, title: '新对话' }, messages: [], tasks: [] }),
  })
  try {
    assert.equal(f.state.activeSession.value.title, '新对话')
    f.state.draft.value = '解释资料'; await f.state.sendMessage(); await settled()
    assert.equal(f.state.activeSession.value.title, '解释资料')
    assert.equal(f.state.sessions.value.find(item => item.id === 'session-a').title, '解释资料')
    assert.equal(f.state.run.value.state, 'running')
  } finally { f.close() }
})


test('标题列表响应迟到时，不能覆盖手动切换后的会话', async () => {
  const refresh = deferred(); let reads = 0
  const f = await fixture({ listPartnerSessions: () => ++reads === 1 ? Promise.resolve({ items: [session] }) : refresh.promise })
  try {
    f.state.draft.value = '解释资料'; 
    const starting = f.state.sendMessage(); await settled()
    await f.state.selectSession('manual-session')
    refresh.resolve({ items: [{ ...session, title: '迟到的旧会话标题' }] }); await starting; await settled()
    assert.equal(f.state.activeSession.value.id, 'manual-session')
    assert.equal(f.state.activeSession.value.title, session.title)
    assert.notEqual(f.state.sessions.value.find(item => item.id === 'session-a').title, '迟到的旧会话标题')
  } finally { f.close() }
})


test('失败或取消后的未知用量明确提示不完整，不能显示为零消耗', async () => {
  const stopped = { ...running, state: 'cancelled', usage: {} }
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [stopped] }) })
  try {
    assert.match(f.state.formatUsage(stopped.usage), /用量未完整取得/)
    assert.equal(f.state.usageMayBeIncomplete.value, true)
    f.state.run.value = { ...paused, usage: { ...paused.usage, input_tokens: 12500 } }
    assert.match(f.state.formatUsage(f.state.run.value.usage), /12500 输入 tokens/)
    assert.equal(f.state.usageMayBeIncomplete.value, false)
  } finally { f.close() }
})


test('暂停后的草稿可以单独确认收录，暂停和失败均保留成果', async () => {
  for (const state of ['paused', 'failed', 'cancelled', 'interrupted']) {
    const task = structuredClone({ ...paused, state }), f = await fixture({ getPartnerSession: async () => ({ session: { ...session, active_task_id: task.id }, messages: [], tasks: [task] }) })
    try {
      assert.equal(f.state.artifactTasks.value.length, 1)
      await f.state.saveArtifact(task, task.artifacts[0])
      assert.equal(f.calls.filter(call => call.name === 'save').length, 1)
    } finally { f.close() }
  }
})


test('重新打开已结束的会话显示最近任务状态与实际阅读进展', async () => {
  const latest = { ...finished, id: 'latest-task', progress: { read_units: 85, visual_pages: 2, text_characters: 5000 } }
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [latest, { ...finished, id: 'old-task' }] }) })
  try {
    assert.equal(f.state.run.value.id, 'latest-task')
    assert.match(f.state.readingProgressText(latest.progress), /85.*2.*5000/)
    assert.match(f.sourceText, /partner-reading-progress/)
  } finally { f.close() }
})

test('一次发送自然提问，无准备或勾选；当前服务与范围绑定到请求', async () => {
  const f = await fixture()
  try {
    assert.equal(f.state.thinkingMode.value, 'disabled')
    assert.equal(f.calls.some(call => ['prepare', 'start', 'send'].includes(call.name)), false)
    f.state.draft.value = '先帮我梳理思路'; await f.state.sendMessage()
    const calls = f.calls.filter(call => call.name === 'send'); assert.equal(calls.length, 1)
    const payload = calls[0].args[1]
    assert.equal(payload.message, '先帮我梳理思路'); assert.equal(payload.consent, true)
    assert.equal(payload.expected_service_revision, 'service-a'); assert.equal(payload.thinking_mode, 'disabled')
    assert.equal(payload.scope_mode, 'selected'); assert.deepEqual(payload.document_ids, [])
    assert.ok(payload.request_id); assert.equal(f.state.draft.value, '')
    assert.equal(f.calls.some(call => ['prepare', 'start'].includes(call.name)), false)
    assert.doesNotMatch(f.sourceText, /partner-consent|preparationVisible|v-html|innerHTML/)
    assert.match(f.sourceText, /data-testid="partner-send"/)
  } finally { f.close() }
})

test('会话恢复上次范围、具体资料与思考方式；移除不可用资料', async () => {
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [], chat_options: { scope_mode: 'selected', document_ids: ['doc-a', 'gone'], thinking_mode: 'enabled' } }), getDocument: async id => id === 'gone' ? { ...document, id, status: 'missing' } : document })
  try {
    assert.equal(f.state.thinkingMode.value, 'enabled')
    assert.deepEqual(f.state.selectedDocuments.value.map(doc => doc.id), ['doc-a'])
    f.state.draft.value = '接着讨论'; await f.state.sendMessage()
    assert.deepEqual(f.calls.find(call => call.name === 'send').args[1].document_ids, ['doc-a'])
  } finally { f.close() }
})

test('暂停任务允许直接新问题，也能一次点击继续原任务', async () => {
  for (const action of ['send', 'resume']) {
    const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [paused] }) })
    try {
      assert.equal(f.state.isBlocked.value, false)
      if (action === 'send') { f.state.draft.value = '换一个问题'; await f.state.sendMessage() }
      else await f.state.resumeRun()
      assert.equal(f.calls.filter(call => call.name === action).length, 1)
      assert.equal(f.calls.some(call => ['prepare', 'prepare-continue', 'start', 'continue'].includes(call.name)), false)
      assert.equal(f.state.isRunning.value, true)
    } finally { f.close() }
  }
})

test('不确定的发送结果不自动重发；用户重试复用请求，重复点击只发送一次', async () => {
  const ids = [], gate = deferred()
  const f = await fixture({ sendPartnerMessage: async (id, body) => { ids.push(body.request_id); if (ids.length === 1) throw new Error('连接中断'); return gate.promise } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage(); await settled()
    assert.equal(ids.length, 1); assert.equal(f.state.draft.value, '解释资料')
    const retry = f.state.retryPendingSubmission(); await f.state.retryPendingSubmission(); assert.equal(ids.length, 2)
    assert.equal(ids[0], ids[1]); gate.resolve(running); await retry
  } finally { gate.resolve(running); f.close() }
})

test('更改问题与范围后使用新幂等请求；服务变化只刷新说明不静默重发', async () => {
  const requests = []; let configurations = 0
  const f = await fixture({ sendPartnerMessage: async (id, body) => { requests.push(body); throw Object.assign(new Error('服务配置已变化'), { code: 'AI_SERVICE_CHANGED' }) }, getPartnerChatConfig: async () => ({ configured: true, has_api_key: true, service_revision: `service-${++configurations}`, service: { base_url: 'https://example.test', model: 'test-model' } }) })
  try {
    f.state.draft.value = '原问题'; await f.state.sendMessage(); await settled()
    assert.equal(requests.length, 1); assert.ok(configurations >= 2)
    assert.match(f.state.error.value, /服务.*变化|服务.*更新/)
    f.state.draft.value = '新问题'; f.state.scopeMode.value = 'library'; await f.state.sendMessage()
    assert.notEqual(requests[0].request_id, requests[1].request_id)
    assert.equal(requests[1].scope_mode, 'library')
  } finally { f.close() }
})

test('停止或失败保留已看到的部分回答，继续作为未完成消息展示', async () => {
  const gate = deferred(); let update
  const f = await fixture({ streamPartnerTask: (id, listener) => { update = listener; return gate.promise } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    update({ ...running, answer_preview: '已经读到的分析' }); await settled()
    await f.state.cancelRun()
    assert.equal(f.state.partialAnswer.value, '已经读到的分析')
    assert.equal(f.state.conversationItems.value.at(-1).incomplete, true)
    assert.equal(f.state.messages.value.some(item => item.role === 'assistant'), false)
    f.state.applyTask({ ...running, id: 'next-task', message: '接着说' })
    assert.ok(f.state.conversationItems.value.some(item => item.content === '已经读到的分析' && item.incomplete))
  } finally { gate.resolve(); f.close() }
})

test('断流与连续暂时断网自动退避恢复同一任务，仅GET且不重复发送', async () => {
  let reads = 0
  const f = await fixture({ getPartnerTask: async () => { if (++reads <= 2) throw new Error('网络暂时不可用'); return finished } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage(); await settled()
    const delays = []
    for (let i = 0; i < 3; i++) { delays.push([...f.timers.values()][0]?.delay); await f.tick() }
    assert.equal(f.state.run.value.state, 'completed'); assert.equal(f.timers.size, 0)
    assert.ok(delays[1] > delays[0]); assert.ok(delays[2] > delays[1])
    assert.equal(f.calls.filter(call => call.name === 'send').length, 1)
  } finally { f.close() }
})

test('向上浏览时流式快照不强制滚底；发送新问题主动跟随', async () => {
  const f = await fixture()
  try {
    const element = { scrollTop: 100, scrollHeight: 1000, clientHeight: 300 }; f.state.conversationRef.value = element
    f.state.onConversationScroll(); f.state.applyTask({ ...running, answer_preview: '新片段' }); await settled()
    assert.equal(element.scrollTop, 100)
    f.state.followConversation(); await settled(); assert.equal(element.scrollTop, 1000)
  } finally { f.close() }
})

test('恢复会话选择资料的迟到请求不能污染新会话', async () => {
  const gate = deferred()
  const f = await fixture({ getPartnerSession: async id => ({ session: { ...session, id }, messages: [], tasks: [], chat_options: { scope_mode: 'selected', document_ids: id === 'with-doc' ? ['doc-a'] : [], thinking_mode: 'disabled' } }), getDocument: () => gate.promise })
  try {
    const old = f.state.selectSession('with-doc'); await settled(); await f.state.selectSession('empty')
    gate.resolve(document); await old
    assert.equal(f.state.activeSession.value.id, 'empty'); assert.deepEqual(f.state.selectedDocuments.value, [])
  } finally { gate.resolve(document); f.close() }
})

test('高级预算默认折叠、记住非敏感偏好并拒绝越界参数', async () => {
  const f = await fixture()
  try {
    assert.equal(f.state.budgetMode.value, 'custom'); assert.equal(f.state.customBudget.value.model_requests, 64)
    f.state.budgetMode.value = 'deep'; await settled(); f.state.draft.value = '深入比较'; await f.state.sendMessage()
    assert.deepEqual(f.calls.find(call => call.name === 'send').args[1].budget, { mode: 'deep' })
    assert.deepEqual(JSON.parse(f.storage.get('teamhub.partner.budget.v1')), { mode: 'deep' })
    assert.match(f.sourceText, /<details[^>]*class="advanced-options"/)
  } finally { f.close() }
  const invalid = await fixture()
  try {
    invalid.state.customBudget.value.model_requests = 999999; invalid.state.draft.value = '分析'; await invalid.state.sendMessage()
    assert.equal(invalid.calls.some(call => call.name === 'send'), false); assert.match(invalid.state.budgetError.value, /模型请求/)
  } finally { invalid.close() }
})

test('模板能编译且流式助手消息、当前服务与资料选择保留独立入口', async () => {
  const f = await fixture()
  try {
    const compiled = compiler.compileTemplate({ source: compiler.parse(f.sourceText).descriptor.template.content, filename: 'AIPartnerPage.vue', id: 'natural-chat' })
    assert.deepEqual(compiled.errors, [])
    for (const name of ['partner-question', 'partner-send', 'partner-thinking', 'partner-service', 'partner-answer-preview', 'partner-cancel', 'partner-continue']) assert.match(f.sourceText, new RegExp(name))
    assert.doesNotMatch(f.sourceText, /发送前.*确认|重新确认.*预算|preparePartnerTurn/)
  } finally { f.close() }
})

test('发送响应迟到并切库时，不把旧问题或资料加入新库', async () => {
  const gate = deferred(), f = await fixture({ sendPartnerMessage: () => gate.promise })
  try {
    f.state.draft.value = '旧库问题'; f.state.selectedDocuments.value = [document]
    const sending = f.state.sendMessage(); f.appStore.libraryPath = '/temporary/library-b'; await settled()
    gate.resolve(running); await sending
    assert.equal(f.state.run.value, null); assert.deepEqual(f.state.selectedDocuments.value, []); assert.deepEqual(f.state.messages.value, [])
  } finally { gate.resolve(running); f.close() }
})

test('继续失败后重试同一请求；会话切换后迟到结果不覆盖新会话', async () => {
  const ids = [], gate = deferred()
  const f = await fixture({ getPartnerSession: async id => ({ session: { ...session, id }, messages: [], tasks: id === 'session-a' ? [paused] : [] }), resumePartnerTask: async (id, body) => { ids.push(body.request_id); if (ids.length === 1) throw new Error('连接中断'); return gate.promise } })
  try {
    await f.state.resumeRun(); assert.equal(ids.length, 1)
    const retry = f.state.retryPendingSubmission(); await f.state.retryPendingSubmission(); assert.equal(ids.length, 2); assert.equal(ids[0], ids[1])
    await f.state.selectSession('new-session'); gate.resolve(running); await retry
    assert.equal(f.state.run.value, null); assert.equal(f.state.activeSession.value.id, 'new-session')
  } finally { gate.resolve(running); f.close() }
})

test('缺少服务修订时不可发送；错配任务不写入当前对话', async () => {
  const missing = await fixture({ getPartnerChatConfig: async () => ({ has_api_key: true, service: { model: 'test', base_url: 'https://example.test' } }) })
  try { missing.state.draft.value = '问题'; await missing.state.sendMessage(); assert.equal(missing.calls.some(call => call.name === 'send'), false) } finally { missing.close() }
  const wrong = await fixture({ sendPartnerMessage: async () => ({ ...running, session_id: 'other' }) })
  try { wrong.state.draft.value = '问题'; await wrong.state.sendMessage(); assert.equal(wrong.state.run.value, null); assert.equal(wrong.state.submissionPending.value, true) } finally { wrong.close() }
})

test('任务访问被撤销时停止重连并展示原因，不无限发出状态请求', async () => {
  const f = await fixture({ getPartnerTask: async () => { throw Object.assign(new Error('已无权读取任务'), { response: { status: 403 } }) } })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage(); await f.tick()
    assert.equal(f.timers.size, 0); assert.match(f.state.error.value, /无权/)
    assert.equal(f.calls.filter(call => call.name === 'send').length, 1)
  } finally { f.close() }
})

test('对话标题刷新失败不覆盖正在生成的回答或呈现阻断错误', async () => {
  let reads = 0
  const f = await fixture({ listPartnerSessions: async () => { if (++reads > 1) throw new Error('标题暂时不可用'); return { items: [session] } } })
  try { f.state.draft.value = '解释资料'; await f.state.sendMessage(); assert.equal(f.state.isRunning.value, true); assert.equal(f.state.error.value, '') } finally { f.close() }
})

test('明确撤销预览时清除本地旧片段，不能因保留部分回答重新展示', async () => {
  const f = await fixture()
  try {
    f.state.applyTask({ ...running, answer_preview: '应清除的旧内容' })
    f.state.applyTask({ ...running, state: 'failed', answer_preview: '', preview_discarded: true })
    assert.equal(f.state.partialAnswer.value, '')
    assert.equal(f.state.conversationItems.value.some(item => item.content === '应清除的旧内容'), false)
  } finally { f.close() }
})

test('Enter 发送，Shift换行；输入法确认文字时不会误发', async () => {
  const f = await fixture(); let prevented = 0
  try {
    f.state.draft.value = '还在输入'
    await f.state.handleComposerEnter({ isComposing: true, preventDefault: () => prevented++ })
    await f.state.handleComposerEnter({ shiftKey: true, preventDefault: () => prevented++ })
    assert.equal(prevented, 0); assert.equal(f.calls.some(call => call.name === 'send'), false)
    await f.state.handleComposerEnter({ preventDefault: () => prevented++ })
    assert.equal(prevented, 1); assert.equal(f.calls.filter(call => call.name === 'send').length, 1)
  } finally { f.close() }
})

test('失败任务可一次点击重试，保留上一轮部分回答', async () => {
  const failed = { ...running, state: 'failed', answer_preview: '保留的上一轮分析', error: { message: '连接中断' } }
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [failed] }), sendPartnerMessage: async () => ({ ...running, id: 'new-task' }) })
  try {
    await f.state.retryRun()
    assert.equal(f.state.run.value.id, 'new-task')
    assert.ok(f.state.conversationItems.value.some(item => item.incomplete && item.content === failed.answer_preview))
  } finally { f.close() }
})

test('坏预算偏好或继承属性不能绕过允许范围；有效偏好恢复', async () => {
  for (const stored of ['not-json', JSON.stringify({ mode: '__proto__' }), JSON.stringify({ mode: 'deep', document_ids: ['secret'] }), JSON.stringify({ mode: 'custom', model_requests: 999999, tool_calls: 80, output_tokens: 48000, time_seconds: 600 })]) {
    const f = await fixture({}, {}, stored)
    try { assert.equal(f.state.budgetMode.value, 'custom'); assert.equal(f.state.customBudget.value.model_requests, 64) } finally { f.close() }
  }
  const f = await fixture({}, {}, JSON.stringify({ mode: 'quick' }))
  try { assert.equal(f.state.budgetMode.value, 'quick') } finally { f.close() }
})

test('附属标题查询尚未结束时，已开始的任务仍能立即停止', async () => {
  const gate = deferred(); let reads = 0
  const f = await fixture({ listPartnerSessions: () => ++reads === 1 ? Promise.resolve({ items: [session] }) : gate.promise })
  try {
    f.state.draft.value = '解释资料'; await f.state.sendMessage()
    assert.equal(f.state.starting.value, false)
    await f.state.cancelRun(); assert.equal(f.state.run.value.state, 'cancelled')
  } finally { gate.resolve({ items: [session] }); await settled(); f.close() }
})

test('提交结果未知期间编辑再发送不会覆盖原编号；GET对账恢复任务且保留新草稿', async () => {
  const posts = [], lookups = [], lookup = deferred()
  const f = await fixture({ sendPartnerMessage: async (id, body) => { posts.push(structuredClone(body)); throw new Error('响应丢失') }, getPartnerRequestTask: async (id, requestId) => { lookups.push(requestId); return lookup.promise } })
  try {
    f.state.draft.value = '原问题'; await f.state.sendMessage()
    f.state.draft.value = '编辑后的新问题'; await f.state.sendMessage()
    f.state.draft.value = '原问题'; await f.state.sendMessage()
    assert.equal(posts.length, 1)
    f.state.draft.value = '保留这个新草稿'
    await f.tick(); assert.equal(lookups[0], posts[0].request_id)
    lookup.resolve({ ...running, request_id: posts[0].request_id, message: '原问题' }); await settled()
    assert.equal(f.state.run.value.id, running.id); assert.equal(f.state.draft.value, '保留这个新草稿')
    await f.state.cancelRun(); assert.equal(f.state.run.value.state, 'cancelled')
  } finally { lookup.resolve(running); f.close() }
})

test('未知提交查询404仍保留原请求并退避；显式重试只提交原始payload', async () => {
  const posts = [], lookups = []
  const f = await fixture({ sendPartnerMessage: async (id, body) => { posts.push(structuredClone(body)); throw new Error('网络中断') }, getPartnerRequestTask: async (id, requestId) => { lookups.push(requestId); throw Object.assign(new Error('尚未登记'), { response: { status: 404 } }) } })
  try {
    f.state.draft.value = '原问题'; await f.state.sendMessage(); const firstDelay = [...f.timers.values()][0]?.delay
    await f.tick(); assert.equal(posts.length, 1); assert.ok([...f.timers.values()][0]?.delay > firstDelay)
    f.state.draft.value = '另一个问题'; f.state.budgetMode.value = 'quick'; f.state.scopeMode.value = 'library'
    await f.state.sendMessage(); assert.equal(posts.length, 1)
    await f.state.retryPendingSubmission(); assert.equal(posts.length, 2); assert.deepEqual(posts[1], posts[0])
    assert.equal(f.state.draft.value, '另一个问题'); assert.equal(lookups.length, 1)
  } finally { f.close() }
})

test('续行响应未知且预算改变时不新建续行；GET确认实际接受后恢复', async () => {
  const posts = []; let taskReads = 0
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [paused] }), resumePartnerTask: async (id, body) => { posts.push(structuredClone(body)); throw new Error('响应未知') }, getPartnerTask: async () => ++taskReads === 1 ? paused : { ...paused, last_request_id: posts[0].request_id } })
  try {
    await f.state.resumeRun(); f.state.budgetMode.value = 'quick'; await f.state.resumeRun()
    assert.equal(posts.length, 1)
    await f.tick(); assert.equal(f.state.submissionPending.value, true)
    await f.state.retryPendingSubmission(); assert.equal(posts.length, 2); assert.deepEqual(posts[1], posts[0])
    await f.tick(); assert.equal(f.state.submissionPending.value, false); assert.equal(f.state.run.value.state, 'paused')
  } finally { f.close() }
})

test('确定拒绝的4xx不会进入未知提交；busy和不可续行先读取现有任务', async () => {
  let calls = 0, reads = 0
  const f = await fixture({ sendPartnerMessage: async () => { calls++; throw Object.assign(new Error('参数不合法'), { response: { status: 422 } }) } })
  try { f.state.draft.value = '问题'; await f.state.sendMessage(); assert.equal(f.state.submissionPending.value, false); f.state.draft.value = '修正后问题'; await f.state.sendMessage(); assert.equal(calls, 2) } finally { f.close() }
  const busy = await fixture({ sendPartnerMessage: async () => { throw Object.assign(new Error('正在生成'), { response: { status: 409, data: { detail: { error: { code: 'PARTNER_SESSION_BUSY' } } } } }) }, getPartnerSession: async () => ({ session, messages: [], tasks: ++reads > 1 ? [running] : [] }) })
  try { busy.state.draft.value = '新问题'; await busy.state.sendMessage(); assert.equal(busy.state.run.value.id, running.id); assert.equal(busy.state.draft.value, '新问题') } finally { busy.close() }
})

test('未知提交切会话再返回仍按原编号对账；不能新建同一请求', async () => {
  let posts = 0, lookupId
  const f = await fixture({ sendPartnerMessage: async () => { posts++; throw new Error('响应未知') }, getPartnerRequestTask: async (id, requestId) => { lookupId = requestId; return { ...running, request_id: requestId } } })
  try {
    f.state.draft.value = '原问题'; await f.state.sendMessage(); const originalId = f.state.pendingSubmission.value.body.request_id
    await f.state.selectSession('other-session'); await f.state.selectSession('session-a')
    assert.equal(f.state.submissionPending.value, true)
    f.state.draft.value = '新的草稿'; await f.state.sendMessage(); assert.equal(posts, 1)
    await f.tick(); assert.equal(lookupId, originalId); assert.equal(f.state.run.value.id, running.id); assert.equal(f.state.draft.value, '新的草稿')
  } finally { f.close() }
})

test('明确不可续行时读取任务最新状态并恢复停止入口，不新建请求', async () => {
  let reads = 0
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [], tasks: [paused] }), resumePartnerTask: async () => { throw Object.assign(new Error('已在处理'), { response: { status: 409, data: { error: { code: 'PARTNER_NOT_RESUMABLE' } } } }) }, getPartnerTask: async () => { reads++; return running } })
  try {
    await f.state.resumeRun(); assert.equal(reads, 1); assert.equal(f.state.isRunning.value, true)
    assert.equal(f.state.submissionPending.value, false); await f.state.cancelRun(); assert.equal(f.state.run.value.state, 'cancelled')
  } finally { f.close() }
})

test('较早的提交对账GET在显式重试后返回，不得覆盖新任务状态', async () => {
  const lookup = deferred(), posts = []
  const f = await fixture({ sendPartnerMessage: async (id, body) => { posts.push(body); if (posts.length === 1) throw new Error('响应未知'); return running }, getPartnerRequestTask: () => lookup.promise })
  try {
    f.state.draft.value = '原问题'; await f.state.sendMessage(); await f.tick()
    await f.state.retryPendingSubmission(); assert.equal(f.state.run.value.state, 'running')
    lookup.resolve({ ...finished, request_id: posts[0].request_id }); await settled()
    assert.equal(f.state.run.value.state, 'running'); assert.equal(posts.length, 2)
  } finally { lookup.resolve(running); f.close() }
})

test('上一轮成果附在所属回复后，最新追问底部不重复旧草稿；旧成果仍可收录', async () => {
  const f = await fixture({ getPartnerSession: async () => ({ session, messages: [{ id:'user-a', role:'user', task_id:finished.id, content:'原问题' }, { id:'assistant-a', role:'assistant', task_id:finished.id, content:'原回复' }, { id:'user-b', role:'user', task_id:'task-b', content:'追问' }, { id:'assistant-b', role:'assistant', task_id:'task-b', content:'新回复' }], tasks:[{ ...finished, id:'task-b', artifacts:[] }, finished] }) })
  try {
    assert.deepEqual(f.state.artifactTasksForMessage(f.state.messages.value[0]), [])
    const matched = f.state.artifactTasksForMessage(f.state.messages.value[1]); assert.equal(matched[0].id, finished.id)
    assert.deepEqual(f.state.artifactTasksForMessage(f.state.messages.value.at(-1)), [])
    await f.state.saveArtifact(matched[0], matched[0].artifacts[0]); assert.equal(f.calls.filter(call=>call.name==='save').length,1)
    assert.ok(f.sourceText.indexOf('artifactTasksForMessage(message)') < f.sourceText.indexOf('data-testid="partner-task-progress"'))
    assert.match(f.sourceText, /task-completed/)
  } finally { f.close() }
})

test('5xx提交结果未知，查询暂时断网后恢复；不自动重放POST', async () => {
  let posts = 0, lookups = 0, originalId
  const f = await fixture({ sendPartnerMessage: async (id, body) => { posts++; originalId = body.request_id; throw Object.assign(new Error('网关异常'), { response:{ status:502 } }) }, getPartnerRequestTask: async () => { if (++lookups === 1) throw new Error('查询暂时断网'); return { ...finished, request_id:originalId } } })
  try {
    f.state.draft.value = '原问题'; await f.state.sendMessage(); await f.tick(); assert.equal(f.state.submissionPending.value, true)
    await f.tick(); assert.equal(f.state.run.value.state, 'completed'); assert.equal(f.state.submissionPending.value, false); assert.equal(posts, 1)
  } finally { f.close() }
})

test('离开伙伴页面再回来仍能核对未知发送，页面卸载不丢失幂等身份', async () => {
  const registry = new Map(); let originalId
  const first = await fixture({ sendPartnerMessage: async (id, body) => { originalId=body.request_id; throw new Error('响应未知') } }, {}, null, registry)
  first.state.draft.value = '原问题'; await first.state.sendMessage(); first.close()
  const returned = await fixture({ getPartnerRequestTask: async (id, requestId) => ({ ...running, request_id: requestId }) }, {}, null, registry)
  try {
    assert.equal(returned.state.submissionPending.value, true); assert.equal(returned.state.pendingSubmission.value.body.request_id, originalId)
    await returned.tick(); assert.equal(returned.state.run.value.id, running.id)
    assert.equal(returned.calls.some(call=>call.name==='send'), false)
  } finally { returned.close() }
})
