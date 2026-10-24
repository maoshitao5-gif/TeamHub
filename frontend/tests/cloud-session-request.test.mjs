import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'
import { advanceCloudSessionScope } from '../src/utils/cloudSession.js'

globalThis.CustomEvent ??= class extends Event { constructor(type, options) { super(type); this.detail = options?.detail } }
let serial = 0
async function fixture() {
  globalThis.window = new EventTarget(); window.electron = { apiBaseURL: 'http://127.0.0.1:8123' }
  const events = [], messages = [], interceptors = {}
  window.addEventListener('cloud:session-expired', event => events.push(event.detail))
  const hook = `__session_request_fixture_${++serial}`
  globalThis[hook] = {
    axios: { create: () => ({ interceptors: {
      request: { use: ok => { interceptors.request = ok } },
      response: { use: (_ok, fail) => { interceptors.error = fail } },
    } }) }, ElMessage: { error: value => messages.push(value) },
  }
  let source = await fs.readFile(new URL('../src/api/request.js', import.meta.url), 'utf8')
  source = source.replace("import axios from 'axios'", `const axios = globalThis.${hook}.axios`)
    .replace("import { ElMessage } from 'element-plus'", `const ElMessage = globalThis.${hook}.ElMessage`)
    .replace(/from ['"]@\/utils\/cloudSession['"]/, `from '${new URL('../src/utils/cloudSession.js', import.meta.url)}'`)
  await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#${serial}`)
  return { ...interceptors, events, messages }
}
const data = { detail: { error: { code: 'CLOUD_SESSION_EXPIRED', message: '云服务登录已失效，请重新登录' } } }

test('real response interceptor notifies current terminal expiry and preserves structured error', async () => {
  const f = await fixture(), config = f.request({ _silent: true })
  let error; try { await f.error({ config, response: { status: 401, data } }) } catch (caught) { error = caught }
  assert.equal(f.events.length, 1); assert.equal(error.message, '云服务登录已失效，请重新登录')
  assert.deepEqual(error.response.data, data); assert.equal(f.messages.length, 0)
})
test('response interceptor ignores a terminal response after a newer login scope', async () => {
  const f = await fixture(), config = f.request({ _silent: true })
  advanceCloudSessionScope()
  await assert.rejects(f.error({ config, response: { status: 401, data } }))
  assert.equal(f.events.length, 0)
})
test('blob terminal JSON is parsed and scoped; ordinary blob 401 does not expire login', async () => {
  const f = await fixture(), config = f.request({ _silent: true, responseType: 'blob' })
  await assert.rejects(f.error({ config, response: { status: 401, data: new Blob([JSON.stringify(data)], { type: 'application/json' }) } }), /云服务登录已失效/)
  assert.equal(f.events.length, 1); assert.equal(f.messages.length, 0)
  f.events.length = 0
  await assert.rejects(f.error({ config, response: { status: 401, data: new Blob([JSON.stringify({ detail: '无权下载' })]) } }), /无权下载/)
  assert.equal(f.events.length, 0)
})
test('late blob parsing cannot expire a new session', async () => {
  const f = await fixture(), config = f.request({ _silent: true, responseType: 'blob' })
  let finish; const blob = new Blob(['{}']); blob.text = () => new Promise(resolve => { finish = resolve })
  const failure = f.error({ config, response: { status: 401, data: blob } })
  advanceCloudSessionScope(); finish(JSON.stringify(data))
  await assert.rejects(failure); assert.equal(f.events.length, 0)
})
test('business 401 and unavailable service do not send terminal events', async () => {
  const f = await fixture(), config = f.request({ _silent: true })
  await assert.rejects(f.error({ config, response: { status: 401, data: { detail: '邮箱或密码错误' } } }), /邮箱或密码错误/)
  await assert.rejects(f.error({ config, response: { status: 503, data } }))
  assert.equal(f.events.length, 0)
})
