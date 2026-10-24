import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import compiler from '../node_modules/@vue/compiler-sfc/dist/compiler-sfc.cjs.js'
import { createRenderer, h, nextTick, reactive } from '../node_modules/vue/dist/vue.runtime.esm-bundler.js'

const vueURL = new URL('../node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url)
const renderer = createRenderer({
  createComment: () => ({}), createElement: () => ({}), createText: () => ({}),
  insert() {}, remove() {}, patchProp() {}, setElementText() {}, setText() {}, setComment() {},
  parentNode: () => null, nextSibling: () => null,
})
let serial = 0
const indexed = { document_id: 'doc-a', version_id: 'version-a', version_number: 1, content_hash: 'a'.repeat(64), state: 'ready', overview: '本地提取的说明', unit_count: 1, units: [{ id: 'unit-a', kind: 'paragraph', text: '本地原文', location: { paragraph: 1 }, label: '第 1 段', context: '' }], coverage: { total: 1, processed: 1, failed: 0, unit: '段' }, gaps: [], notes: [], ai: null }
const prepared = { preparation_id: 'prepare-a', document_id: 'doc-a', document_name: '说明.txt', version_id: 'version-a', content_hash: indexed.content_hash, text: '[unit-a] 第 1 段\n本地原文', truncated: false, notes: [], expires_in: 600, service: { base_url: 'https://service.example', model: 'test-model' } }
const enriched = { ...prepared, overview: 'AI 检索线索概要', clues: [{ unit_id: 'unit-a', terms: ['使用说明'] }], model: 'test-model' }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 8; i++) { await Promise.resolve(); await nextTick() } }

async function fixture(overrides = {}) {
  const calls = { get: [], rebuild: [], settings: [], prepare: [], enrich: [] }, routes = [], timers = new Map()
  let timerSerial = 0
  const appStore = reactive({ libraryPath: '/temporary/library-a' })
  const api = {
    async getDocumentIndex(id, signal) { calls.get.push({ id, signal }); return { ...indexed, document_id: id } },
    async rebuildDocumentIndex(id, signal) { calls.rebuild.push({ id, signal }); return { state: 'queued' } },
    async getAISettings(signal) { calls.settings.push(signal); return { has_api_key: true } },
    async prepareIndexEnrichment(id, signal) { calls.prepare.push({ id, signal }); return { ...prepared } },
    async enrichDocumentIndex(id, signal) { calls.enrich.push({ id, signal }); return { ...enriched } },
    ...overrides,
  }
  const key = `__index_component_fixture_${++serial}`
  globalThis[key] = { api, appStore, router: { push: value => routes.push(value) },
    setTimeout: callback => { const id = ++timerSerial; timers.set(id, callback); return id },
    clearTimeout: id => timers.delete(id),
  }
  const source = await fs.readFile(new URL('../src/components/DocumentIndexCard.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ useRouter \} from 'vue-router'/, `const useRouter = () => globalThis.${key}.router`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{([^}]+)\} from '@\/api\/(?:ai|indexing)'/g, (_, names) => names.split(',').map(name => name.trim()).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/\bsetTimeout\(/g, `globalThis.${key}.setTimeout(`)
    .replace(/\bclearTimeout\(/g, `globalThis.${key}.clearTimeout(`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  const props = reactive({ visible: false, document: { id: 'doc-a', name: '说明.txt', status: 'organized', current_version: { id: 'version-a' } } })
  let state
  const scriptOnlyComponent = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup() { return () => h(scriptOnlyComponent, { ...props }) } })
  app.mount({})
  return { api, calls, props, appStore, routes, timers, source, get state() { return state },
    open: async () => { props.visible = true; await settled() },
    tick: async () => { const entries = [...timers.entries()]; timers.clear(); for (const [, callback] of entries) callback(); await settled() },
    close: () => { app.unmount(); delete globalThis[key] },
  }
}

test('待整理只展示本地索引，完成整理后才准备付费补充，避免整理后线索立即失效', async () => {
  const f = await fixture()
  try {
    f.props.document.status = 'pending'
    await f.open(); await f.state.openAI()
    assert.equal(f.state.canEnrich.value, false)
    assert.equal(f.calls.prepare.length, 0)
    f.props.document.status = 'organized'
    await settled(); await f.state.openAI()
    assert.equal(f.calls.prepare.length, 1)
  } finally { f.close() }
})

test('缺少可搜索原文不代表不支持AI理解，图片与压缩包显示资料卡入口说明', async () => {
  const f = await fixture({ getDocumentIndex: async () => ({ ...indexed, state: 'failed', unit_count: 0, units: [], coverage: { total: 1, processed: 0, failed: 1 }, gaps: [{ location: { scope: 'file' }, reason: 'unsupported_format' }] }) })
  try {
    f.props.document.name = '验收资料.tar.gz'
    await f.open()
    assert.equal(f.state.stateLabel.value, '未提取原文')
    assert.match(f.state.gapReason('unsupported_format'), /本地原文索引/)
    assert.match(f.source, /图片、压缩包等资料可通过 AI 资料卡/)
    assert.doesNotMatch(f.state.gapReason('unsupported_format'), /暂不支持提取此格式/)
  } finally { f.close() }
})

test('卡片只读取本地索引，原文默认收起，AI 仅在主动展开并勾选后发送', async () => {
  const f = await fixture()
  try {
    assert.equal(f.calls.get.length, 0)
    await f.open()
    assert.equal(f.calls.get.length, 1); assert.equal(f.calls.settings.length, 0)
    assert.equal(f.state.showUnits.value, false)
    assert.equal(f.state.index.value.version_id, 'version-a')
    await f.state.openAI()
    assert.equal(f.state.prepared.value.text, prepared.text)
    assert.deepEqual(f.state.service.value, prepared.service)
    await f.state.enrich(); assert.equal(f.calls.enrich.length, 0)
    f.state.consent.value = true; await f.state.enrich()
    assert.equal(f.calls.enrich.length, 1); assert.equal(f.calls.get.length, 2)
    assert.equal(f.state.aiDone.value, true)
    assert.match(f.source, /索引基于当前已保存版本/)
    assert.doesNotMatch(f.source, /refreshDoc|updateDocumentTags/)
  } finally { f.close() }
})

test('排队和处理中独立轮询，完成即停止；索引错误不发起任何整理操作', async () => {
  let count = 0
  const f = await fixture({ getDocumentIndex: async () => ({ ...indexed, state: ++count < 3 ? 'processing' : 'ready' }) })
  try {
    await f.open(); assert.equal(f.timers.size, 1)
    await f.tick(); assert.equal(count, 2); assert.equal(f.timers.size, 1)
    await f.tick(); assert.equal(count, 3); assert.equal(f.timers.size, 0)
    await f.state.rebuild(); assert.equal(f.calls.rebuild.length, 1); assert.equal(count, 4)
  } finally { f.close() }
})

test('分块发布进度与原文提取覆盖分别展示，不提前宣称全部可搜索', async () => {
  const f = await fixture({ getDocumentIndex: async () => ({ ...indexed, state: 'processing',
    processing_progress: { stage: 'publish', cursor: 128, total_units: 300 } }) })
  try {
    await f.open()
    assert.match(f.state.publicationProgress.value, /128.*300/)
    assert.match(f.state.publicationProgress.value, /尚未全部/)
    assert.equal(f.state.stateLabel.value, '正在提取')
    assert.equal(f.timers.size, 1)
  } finally { f.close() }
})

test('关闭会中止请求和轮询，迟到的索引响应不会重现', async () => {
  const gate = deferred(); let requestSignal
  const f = await fixture({ getDocumentIndex: (id, signal) => { requestSignal = signal; return gate.promise } })
  try {
    await f.open(); f.props.visible = false; await settled()
    assert.equal(requestSignal.aborted, true)
    gate.resolve(indexed); await settled()
    assert.equal(f.state.index.value, null); assert.equal(f.timers.size, 0)
  } finally { f.close() }
})

for (const change of ['document', 'library', 'version', 'metadata']) {
  test(`切换 ${change} 会作废 AI 预览和迟到结果`, async () => {
    const gate = deferred(); let requestSignal
    const f = await fixture({ enrichDocumentIndex: (id, signal) => { requestSignal = signal; return gate.promise } })
    try {
      await f.open(); await f.state.openAI(); f.state.consent.value = true
      const pending = f.state.enrich()
      if (change === 'document') f.props.document = { id: 'doc-b' }
      else if (change === 'library') f.appStore.libraryPath = '/temporary/library-b'
      else if (change === 'version') f.props.document.current_version.id = 'version-b'
      else f.props.document.tags = [{ id: 'new-tag', name: '另一个项目' }]
      await settled(); assert.equal(requestSignal.aborted, true)
      gate.resolve(enriched); await pending; await settled()
      assert.equal(f.state.prepared.value, null); assert.equal(f.state.consent.value, false)
      assert.equal(f.state.aiDone.value, false); assert.equal(f.state.showAI.value, false)
    } finally { f.close() }
  })
}

test('未配置密钥时引导设置且不准备资料', async () => {
  const f = await fixture({ getAISettings: async () => ({ has_api_key: false }) })
  try {
    await f.open(); await f.state.openAI()
    assert.equal(f.state.notConfigured.value, true); assert.equal(f.calls.prepare.length, 0)
    f.state.openSettings(); assert.deepEqual(f.routes, ['/settings'])
  } finally { f.close() }
})

test('AI 失败后必须重新准备和重新同意，没有隐式重试', async () => {
  let paid = 0
  const f = await fixture({ enrichDocumentIndex: async () => { paid++; throw new Error('服务异常') } })
  try {
    await f.open(); await f.state.openAI(); f.state.consent.value = true; await f.state.enrich()
    assert.equal(f.state.prepared.value, null); assert.equal(f.state.consent.value, false)
    await f.state.enrich(); assert.equal(paid, 1)
    await f.state.prepare(); await f.state.enrich(); assert.equal(paid, 1)
    f.state.consent.value = true; await f.state.enrich(); assert.equal(paid, 2)
  } finally { f.close() }
})

test('错误版本、文档或缺失服务配置的准备响应不能展示与发送', async () => {
  for (const response of [{ ...prepared, version_id: 'other-version' }, { ...prepared, document_id: 'other-doc' }, { ...prepared, service: null }]) {
    const f = await fixture({ prepareIndexEnrichment: async () => response })
    try {
      await f.open(); await f.state.openAI()
      assert.equal(f.state.prepared.value, null); assert.match(f.state.aiError.value, /重新读取/)
      f.state.consent.value = true; await f.state.enrich(); assert.equal(f.calls.enrich.length, 0)
    } finally { f.close() }
  }
})

test('分析结果必须匹配本次准备、版本和哈希', async () => {
  const f = await fixture({ enrichDocumentIndex: async () => ({ ...enriched, version_id: 'other-version' }) })
  try {
    await f.open(); await f.state.openAI(); f.state.consent.value = true; await f.state.enrich()
    assert.equal(f.state.aiDone.value, false); assert.equal(f.state.prepared.value, null)
    assert.match(f.state.aiError.value, /不一致/)
  } finally { f.close() }
})
