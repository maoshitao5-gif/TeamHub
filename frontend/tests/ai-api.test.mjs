import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('每次 AI 请求携带当前桌面启动凭证，浏览器不会附加或持久化凭证', async () => {
  const calls = [], store = new Map()
  globalThis.window = { electron: { aiCapability: 'test-capability-first' } }
  globalThis.localStorage = { setItem: (key, value) => store.set(key, value) }
  globalThis.__ai_api_fixture = Object.fromEntries(['get', 'put', 'post'].map(method => [method, (...args) => { calls.push({ method, args }); return Promise.resolve({}) }]))
  const source = (await fs.readFile(new URL('../src/api/ai.js', import.meta.url), 'utf8')).replace("import request from './request'", 'const request = globalThis.__ai_api_fixture')
  const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
  const signal = new AbortController().signal
  await api.getAISettings(signal)
  window.electron.aiCapability = 'test-capability-next'
  await api.updateAISettings({ model: 'flash' }, signal)
  await api.testAIConnection(signal)
  await api.prepareAIDocument('doc-a', signal)
  await api.analyzeAIDocument('prepare-a', signal)
  assert.equal(calls[0].args.at(-1).headers['X-TeamHub-AI-Capability'], 'test-capability-first')
  for (const call of calls.slice(1)) assert.equal(call.args.at(-1).headers['X-TeamHub-AI-Capability'], 'test-capability-next')
  for (const call of calls) {
    assert.equal(call.args.at(-1).headers['X-TeamHub-AI'], '1')
    assert.equal(call.args.at(-1).signal, signal)
  }
  window.electron = undefined
  await api.getAISettings(signal)
  assert.equal('X-TeamHub-AI-Capability' in calls.at(-1).args.at(-1).headers, false)
  assert.equal(store.size, 0)
  delete globalThis.__ai_api_fixture
})
