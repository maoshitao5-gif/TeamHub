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
const prepared = { preparation_id: 'prepare-a', document_id: 'doc-a', document_name: '说明.txt', text: '经过检查的本地资料摘录', content_hash: 'a'.repeat(64), truncated: false, notes: [], expires_in: 600, service: { base_url: 'https://api.deepseek.com', model: 'deepseek-flash' } }
const analyzed = { ...prepared, summary: '简短摘要仅供参考', tags: ['设备', '试验'], model: 'deepseek-flash' }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 6; i++) { await Promise.resolve(); await nextTick() } }

async function fixture(overrides = {}) {
  const calls = { settings: 0, prepare: [], analyze: [] }, events = [], routes = []
  const appStore = reactive({ libraryPath: '/temporary/library-a' })
  const api = {
    async getAISettings() { calls.settings++; return { has_api_key: true, base_url: 'https://api.deepseek.com', model: 'deepseek-flash' } },
    async prepareAIDocument(id, signal) { calls.prepare.push({ id, signal }); return { ...prepared } },
    async analyzeAIDocument(id, signal) { calls.analyze.push({ id, signal }); return { ...analyzed } },
    ...overrides,
  }
  const key = `__ai_component_fixture_${++serial}`
  globalThis[key] = { api, appStore, router: { push: value => routes.push(value) } }
  const source = await fs.readFile(new URL('../src/components/AIAssistDialog.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ useRouter \} from 'vue-router'/, `const useRouter = () => globalThis.${key}.router`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{([^}]+)\} from '@\/api\/ai'/, (_, names) => names.split(',').map(name => name.trim()).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/from '@\/utils\/aiAssist'/, `from '${new URL('../src/utils/aiAssist.js', import.meta.url)}'`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  const props = reactive({ modelValue: false, document: { id: 'doc-a', name: '说明.txt' } })
  let state
  const scriptOnlyComponent = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup() { return () => h(scriptOnlyComponent, { ...props, 'onUpdate:modelValue': value => { props.modelValue = value }, onApply: value => events.push(value) }) } })
  app.mount({})
  return { api, calls, props, appStore, events, routes, get state() { return state }, open: async () => { props.modelValue = true; await settled() }, close: () => { app.unmount(); delete globalThis[key] } }
}

test('实际 AI 弹窗只在主动打开时读取本机内容，未同意不调用模型，候选必须明确选择', async () => {
  const f = await fixture()
  try {
    assert.equal(f.calls.settings, 0)
    await f.open()
    assert.equal(f.calls.prepare.length, 1); assert.equal(f.calls.analyze.length, 0)
    assert.equal(f.state.prepared.value.text, prepared.text)
    await f.state.analyze(); assert.equal(f.calls.analyze.length, 0)
    f.state.consent.value = true; await f.state.analyze()
    assert.equal(f.calls.analyze.length, 1)
    assert.deepEqual(f.state.selected.value, [])
    f.state.apply(); assert.deepEqual(f.events, [])
    f.state.selected.value = ['设备']; f.state.apply()
    assert.deepEqual(f.events, [{ documentId: 'doc-a', libraryPath: '/temporary/library-a', candidates: ['设备', '试验'], selected: ['设备'] }])
    assert.equal('summary' in f.events[0], false)
  } finally { f.close() }
})

test('没有密钥时引导配置，不提取或发送任何资料', async () => {
  const f = await fixture({ getAISettings: async () => ({ has_api_key: false }) })
  try { await f.open(); assert.equal(f.state.notConfigured.value, true); assert.equal(f.calls.prepare.length, 0); assert.equal(f.calls.analyze.length, 0) }
  finally { f.close() }
})

test('关闭后迟到的本地提取结果不会重新出现，重新打开需要重新同意', async () => {
  const gate = deferred(), f = await fixture({ prepareAIDocument: () => gate.promise })
  try {
    await f.open(); assert.equal(f.state.preparing.value, true)
    f.props.modelValue = false; await settled(); gate.resolve(prepared); await settled()
    assert.equal(f.state.prepared.value, null)
    await f.open(); assert.equal(f.state.consent.value, false)
  } finally { f.close() }
})

test('切换个人库会关闭并中止 AI 等待，旧分析结果不能进入新库', async () => {
  const gate = deferred(), f = await fixture({ analyzeAIDocument: () => gate.promise })
  try {
    await f.open(); f.state.consent.value = true
    const pending = f.state.analyze(); f.appStore.libraryPath = '/temporary/library-b'; await settled()
    gate.resolve(analyzed); await pending; await settled()
    assert.equal(f.props.modelValue, false); assert.equal(f.state.result.value, null)
    assert.equal(f.calls.prepare.length, 1); assert.deepEqual(f.events, [])
  } finally { f.close() }
})

test('内容已变化或准备过期时必须重新读取并重新确认发送', async () => {
  const f = await fixture({ analyzeAIDocument: async () => { const error = new Error('资料已变化，请重新读取'); error.response = { status: 409 }; throw error } })
  try {
    await f.open(); f.state.consent.value = true; await f.state.analyze()
    assert.equal(f.state.prepared.value, null); assert.equal(f.state.consent.value, false)
    assert.match(f.state.error.value, /重新读取/)
    await f.state.prepare(); assert.equal(f.state.consent.value, false)
  } finally { f.close() }
})

test('不匹配的资料准备响应不能被展示或发送', async () => {
  const f = await fixture({ prepareAIDocument: async () => ({ ...prepared, document_id: 'doc-other' }) })
  try { await f.open(); assert.equal(f.state.prepared.value, null); assert.match(f.state.error.value, /不一致/) }
  finally { f.close() }
})

test('分析结果必须对应本次准备和内容哈希，不能采用错配结果', async () => {
  const f = await fixture({ analyzeAIDocument: async () => ({ ...analyzed, preparation_id: 'prepare-other', content_hash: 'b'.repeat(64) }) })
  try {
    await f.open(); f.state.consent.value = true; await f.state.analyze()
    assert.equal(f.state.result.value, null); assert.match(f.state.error.value, /不一致/)
  } finally { f.close() }
})

test('用户确认的服务来自本次准备绑定配置，不能显示较早的设置地址', async () => {
  const f = await fixture({ prepareAIDocument: async () => ({ ...prepared, service: { base_url: 'https://service-b.example', model: 'model-b' } }) })
  try {
    await f.open()
    assert.deepEqual(f.state.service.value, { base_url: 'https://service-b.example', model: 'model-b' })
  } finally { f.close() }
})

test('缺少准备绑定服务时拒绝发送，要求重新读取', async () => {
  const f = await fixture({ prepareAIDocument: async () => ({ ...prepared, service: undefined }) })
  try { await f.open(); assert.equal(f.state.prepared.value, null); assert.match(f.state.error.value, /重新读取/) }
  finally { f.close() }
})

test('付费调用失败后作废旧准备，不自动重试，重新读取且重新同意后才能再次调用', async () => {
  let paidCalls = 0
  const f = await fixture({ analyzeAIDocument: async () => {
    paidCalls++
    if (paidCalls === 1) { const error = new Error('AI 服务响应异常，请重新读取资料'); error.response = { status: 502 }; throw error }
    return { ...analyzed }
  } })
  try {
    await f.open(); f.state.consent.value = true; await f.state.analyze()
    assert.equal(f.state.prepared.value, null); assert.equal(f.state.consent.value, false)
    assert.equal(paidCalls, 1)
    await f.state.analyze(); assert.equal(paidCalls, 1)
    await f.state.prepare(); assert.equal(f.calls.prepare.length, 2)
    await f.state.analyze(); assert.equal(paidCalls, 1)
    f.state.consent.value = true; await f.state.analyze()
    assert.equal(paidCalls, 2); assert.equal(f.state.result.value.summary, analyzed.summary)
  } finally { f.close() }
})
