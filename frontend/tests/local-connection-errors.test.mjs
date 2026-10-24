import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

let serial = 0
async function fixture({ electronURL, browserURL } = {}) {
  globalThis.window = new EventTarget()
  if (electronURL) window.electron = { apiBaseURL: electronURL }
  const messages = [], interceptors = {}
  const hook = `__local_connection_fixture_${++serial}`
  globalThis[hook] = {
    axios: { create: options => ({ defaults: { ...options }, interceptors: {
      request: { use: ok => { interceptors.request = ok } },
      response: { use: (_ok, fail) => { interceptors.error = fail } },
    } }) }, ElMessage: { error: value => messages.push(value) },
  }
  let source = await fs.readFile(new URL('../src/api/request.js', import.meta.url), 'utf8')
  source = source.replace("import axios from 'axios'", `const axios = globalThis.${hook}.axios`)
    .replace("import { ElMessage } from 'element-plus'", `const ElMessage = globalThis.${hook}.ElMessage`)
    .replace(/from ['"]@\/utils\/cloudSession['"]/, `from '${new URL('../src/utils/cloudSession.js', import.meta.url)}'`)
    .replace(/import\.meta\.env\.VITE_LOCAL_API_URL/g, JSON.stringify(browserURL) ?? 'undefined')
  const { default: request } = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#local-${serial}`)
  return { ...interceptors, messages, defaults: request.defaults }
}

async function rejection(fixture, error) {
  try { await fixture.error(error) } catch (caught) { return caught }
  assert.fail('request should reject')
}

test('desktop disconnection displays the actual request origin and preserves Axios context', async () => {
  const f = await fixture({ electronURL: 'http://127.0.0.1:8001' })
  const config = { baseURL: 'http://127.0.0.1:8002/api', url: '/ai/partner/tasks', method: 'post' }
  const request = { readyState: 4 }
  const original = { code: 'ERR_NETWORK', message: 'Network Error', config, request }
  const error = await rejection(f, original)
  assert.match(error.message, /本机服务暂时失去连接/)
  assert.match(error.message, /http:\/\/127\.0\.0\.1:8002/)
  assert.doesNotMatch(error.message, /8001|\/api|\/ai\/|CORS|控制台/)
  assert.equal(error.code, 'ERR_NETWORK')
  assert.equal(error.config, config)
  assert.equal(error.request, request)
  assert.equal(error.response, undefined)
  assert.deepEqual(f.messages, [error.message])
})

test('missing request baseURL uses the current Axios endpoint after a port change', async () => {
  const f = await fixture({ electronURL: 'http://127.0.0.1:8001' })
  f.defaults.baseURL = 'http://127.0.0.1:8004/'
  const error = await rejection(f, { code: 'ECONNREFUSED', config: {} })
  assert.match(error.message, /http:\/\/127\.0\.0\.1:8004/)
  assert.doesNotMatch(error.message, /8001/)
})

test('browser configuration and the unconfigured browser fallback both report their own endpoint', async () => {
  const configured = await fixture({ browserURL: 'http://localhost:8137/api' })
  assert.match((await rejection(configured, { code: 'ERR_NETWORK', config: {} })).message, /http:\/\/localhost:8137/)
  const fallback = await fixture()
  assert.match((await rejection(fallback, { code: 'ERR_NETWORK', config: {} })).message, /http:\/\/127\.0\.0\.1:8001/)
})

test('timeouts are identified separately without claiming the service is disconnected', async () => {
  const f = await fixture({ electronURL: 'http://127.0.0.1:8002' })
  for (const code of ['ECONNABORTED', 'ETIMEDOUT']) {
    const error = await rejection(f, { code, message: 'timeout of 30000ms exceeded', config: {} })
    assert.match(error.message, /本机服务响应超时/)
    assert.match(error.message, /8002/)
    assert.doesNotMatch(error.message, /失去连接/)
    assert.equal(error.code, code)
  }
})

test('silent and blob requests preserve failure details without CORS troubleshooting or retries', async () => {
  const f = await fixture({ electronURL: 'http://127.0.0.1:8002' })
  const config = { responseType: 'blob', _silent: true, method: 'post' }
  const error = await rejection(f, { code: 'ERR_NETWORK', message: 'Network Error', config })
  assert.match(error.message, /下载.*本机服务暂时失去连接/)
  assert.match(error.message, /8002/)
  assert.doesNotMatch(error.message, /CORS|控制台|8001/)
  assert.equal(error.config, config)
  assert.equal(error.code, 'ERR_NETWORK')
  assert.equal(f.messages.length, 0)
})

test('connection messages expose only a local origin, never credentials, paths or raw error text', async () => {
  const f = await fixture({ electronURL: 'http://127.0.0.1:8002' })
  const origin = await rejection(f, {
    code: 'ERR_NETWORK', message: 'Network Error token=raw-secret',
    config: { baseURL: 'http://alice:password@localhost:8005/private?token=query-secret#fragment' },
  })
  assert.match(origin.message, /http:\/\/localhost:8005/)
  assert.doesNotMatch(origin.message, /alice|password|private|token|secret|fragment/)
  for (const baseURL of ['https://alice:password@example.com/private?token=secret',
    'http://127.example.com:8002/private', 'http://127.0.0.1.example.com:8002/private',
    'javascript:alert(1)', 'invalid-secret']) {
    const error = await rejection(f, { code: 'ERR_NETWORK', config: { baseURL } })
    assert.doesNotMatch(error.message, /https?:\/\/|example|alice|password|secret|javascript/)
  }
  const unknown = await rejection(f, { message: 'fetch failed https://alice:password@localhost/?token=secret', config: {} })
  assert.doesNotMatch(unknown.message, /alice|password|token|secret/)
})
