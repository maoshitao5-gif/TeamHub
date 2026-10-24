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
const saved = { has_api_key: true, base_url: 'https://api.deepseek.com', model: 'deepseek-flash', key_storage: 'memory', can_persist_key: false }
const deferred = () => { let resolve; const promise = new Promise(done => { resolve = done }); return { promise, resolve } }
async function settled() { for (let i = 0; i < 6; i++) { await Promise.resolve(); await nextTick() } }

async function fixture(overrides = {}) {
  const calls = { vision: [], connection: [], save: [] }
  const appStore = reactive({ libraryPath: '/temporary/library-a' })
  const api = {
    async getAISettings() { return { ...saved } },
    async updateAISettings(value) { calls.save.push(value); return { ...saved, ...value } },
    async testAIConnection(signal) { calls.connection.push(signal); return { ok: true, message: '连接成功' } },
    async testAIVision(signal) { calls.vision.push(signal); return { ok: true, mode: 'vision', message: '本次测试成功识别合成图' } },
    ...overrides,
  }
  const key = `__ai_vision_settings_${++serial}`
  globalThis[key] = { api, appStore, messages: [] }
  const source = await fs.readFile(new URL('../src/components/AISettingsCard.vue', import.meta.url), 'utf8')
  let script = compiler.compileScript(compiler.parse(source).descriptor, { id: key }).content
  script = script.replace(/from 'vue'/g, `from '${vueURL}'`)
    .replace(/import \{ ElMessage, ElMessageBox \} from 'element-plus'/, `const ElMessage = { success: message => globalThis.${key}.messages.push(message) }; const ElMessageBox = { confirm: async () => true }`)
    .replace(/import \{ useAppStore \} from '@\/stores\/app'/, `const useAppStore = () => globalThis.${key}.appStore`)
    .replace(/import \{([^}]+)\} from '@\/api\/ai'/, (_, names) => names.split(',').map(name => name.trim()).map(name => `const ${name} = (...args) => globalThis.${key}.api.${name}(...args)`).join('\n'))
    .replace(/from '@\/utils\/aiAssist'/, `from '${new URL('../src/utils/aiAssist.js', import.meta.url)}'`)
  const { default: component } = await import(`data:text/javascript;base64,${Buffer.from(script).toString('base64')}#${serial}`)
  let state
  const scriptOnlyComponent = { ...component, setup(values, context) { state = component.setup(values, context); return () => null } }
  const app = renderer.createApp({ setup: () => () => h(scriptOnlyComponent) })
  app.mount({}); await settled()
  return { api, calls, appStore, get state() { return state }, close: () => { app.unmount(); delete globalThis[key] } }
}

test('设置页仅主动测试才发送合成图，保留独立连接测试', async () => {
  const f = await fixture()
  try {
    assert.equal(f.calls.vision.length, 0)
    assert.equal(f.calls.connection.length, 0)
    await f.state.testVision()
    assert.equal(f.calls.vision.length, 1)
    assert.equal(f.state.visionPassed.value, true)
    assert.match(f.state.visionMessage.value, /本次测试成功/)
    await f.state.testConnection()
    assert.equal(f.calls.connection.length, 1)
  } finally { f.close() }
})

test('编辑地址、模型、密钥后清除视觉测试结果，未保存不得调用', async () => {
  for (const [field, value] of [['base_url', 'https://other.example'], ['model', 'another-model'], ['api_key', 'only-test-new-key']]) {
    const f = await fixture()
    try {
      await f.state.testVision()
      f.state.form[field] = value; await settled()
      assert.equal(f.state.visionMessage.value, '')
      assert.equal(f.state.visionPassed.value, null)
      await f.state.testVision()
      assert.equal(f.calls.vision.length, 1)
    } finally { f.close() }
  }
})

test('切库及取消后的旧视觉响应不再展示', async () => {
  const gate = deferred(), signals = []
  const f = await fixture({ testAIVision: signal => { signals.push(signal); return gate.promise } })
  try {
    const pending = f.state.testVision()
    f.appStore.libraryPath = '/temporary/library-b'; await settled()
    assert.equal(signals[0].aborted, true)
    gate.resolve({ ok: true, mode: 'vision', message: '旧结果' }); await pending; await settled()
    assert.equal(f.state.visionMessage.value, '')
    assert.equal(f.state.visionPassed.value, null)
  } finally { f.close() }
})

test('编辑配置会中止在途测试并丢弃其旧结果，保存和清除密钥也使结果失效', async () => {
  const gate = deferred(), signals = []
  const f = await fixture({ testAIVision: signal => { signals.push(signal); return gate.promise } })
  try {
    const pending = f.state.testVision()
    f.state.form.model = 'updated-model'; await settled()
    assert.equal(signals[0].aborted, true)
    gate.resolve({ ok: true, mode: 'vision', message: '旧模型结果' }); await pending
    assert.equal(f.state.visionMessage.value, '')
    assert.equal(f.state.testing.value, false)
  } finally { f.close() }
  const g = await fixture()
  try {
    await g.state.testVision(); await g.state.save()
    assert.equal(g.state.visionMessage.value, '')
    await g.state.testVision(); await g.state.clearKey()
    assert.equal(g.state.visionMessage.value, '')
    assert.equal(g.state.visionPassed.value, null)
  } finally { g.close() }
})

test('没有已保存密钥时不会测试图像理解', async () => {
  const f = await fixture({ getAISettings: async () => ({ ...saved, has_api_key: false }) })
  try { await f.state.testVision(); assert.equal(f.calls.vision.length, 0) }
  finally { f.close() }
})

test('视觉识别不匹配显示未通过，网络错误保留真实原因，不当作模型不支持', async () => {
  const f = await fixture({ testAIVision: async () => ({ ok: false, mode: 'vision', message: '本次未能正确识别合成图' }) })
  try { await f.state.testVision(); assert.equal(f.state.visionPassed.value, false); assert.match(f.state.visionMessage.value, /本次未能/) }
  finally { f.close() }
  const g = await fixture({ testAIVision: async () => { throw new Error('AI 密钥无效，请检查设置') } })
  try { await g.state.testVision(); assert.match(g.state.error.value, /密钥无效/); assert.equal(g.state.visionPassed.value, null); assert.equal(g.state.visionMessage.value, '') }
  finally { g.close() }
})

test('视觉测试按钮事前说明只发合成图和可能费用，结果不会存入本地存储', async () => {
  const source = await fs.readFile(new URL('../src/components/AISettingsCard.vue', import.meta.url), 'utf8')
  assert.match(source, /data-testid="ai-settings-test-vision"/)
  assert.match(source, /合成测试图/)
  assert.match(source, /可能产生少量费用/)
  assert.equal(/localStorage|sessionStorage|v-html/.test(source), false)
})

test('视觉测试 API 只发送白名单模式和当前桌面凭证', async () => {
  const calls = []
  globalThis.window = { electron: { aiCapability: 'isolated-vision-capability' } }
  globalThis.__ai_vision_api_fixture = { post(...args) { calls.push(args); return Promise.resolve({}) } }
  const source = (await fs.readFile(new URL('../src/api/ai.js', import.meta.url), 'utf8')).replace("import request from './request'", 'const request = globalThis.__ai_vision_api_fixture')
  const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
  const signal = new AbortController().signal
  try {
    await api.testAIVision(signal)
    assert.deepEqual(calls[0].slice(0, 2), ['/api/ai/test', { mode: 'vision' }])
    assert.equal(calls[0][2].headers['X-TeamHub-AI-Capability'], 'isolated-vision-capability')
    assert.equal(calls[0][2].signal, signal)
  } finally { delete globalThis.__ai_vision_api_fixture; delete globalThis.window }
})
