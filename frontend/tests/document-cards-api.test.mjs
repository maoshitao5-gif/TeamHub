import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('资料卡 API 逐次读取能力凭证，保留授权、版本、取消信号和路径编码', async () => {
  const calls = []
  globalThis.window = { electron: { aiCapability: 'card-a' } }
  globalThis.__card_api = Object.fromEntries(['get', 'post', 'put'].map(method => [method, (...args) => calls.push({ method, args })]))
  try {
    const source = (await fs.readFile(new URL('../src/api/documentCards.js', import.meta.url), 'utf8')).replace("import request from './request'", 'const request = globalThis.__card_api')
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
    const signal = new AbortController().signal
    await api.getDocumentCardsConfig(signal)
    window.electron.aiCapability = 'card-b'
    await api.getDocumentCard('doc /a', signal)
    const data = { library_token: 'library', service_revision: 'service', consent: true, request_id: 'request', auto_update: false }
    await api.generateDocumentCard('doc /a', data, signal)
    await api.cancelDocumentCard('doc /a', { library_token: 'library' }, signal)
    await api.setDocumentCardPolicy('doc /a', { enabled: false }, signal)
    await api.saveDocumentCardAnnotation('doc /a', { version_id: 'v1', expected_revision: 2, text: '人工补充' }, signal)
    assert.deepEqual(calls.map(call => call.args[0]), ['/api/ai/cards/config', '/api/ai/cards/doc%20%2Fa', '/api/ai/cards/doc%20%2Fa/generate', '/api/ai/cards/doc%20%2Fa/cancel', '/api/ai/cards/doc%20%2Fa/policy', '/api/ai/cards/doc%20%2Fa/annotation'])
    assert.equal(calls[2].args[1], data)
    for (const [i, call] of calls.entries()) {
      const options = call.args.at(-1)
      assert.equal(options.headers['X-TeamHub-AI'], '1')
      assert.equal(options.headers['X-TeamHub-AI-Capability'], i ? 'card-b' : 'card-a')
      assert.equal(options.signal, signal)
      assert.equal(options._silent, true)
      assert.equal(options.timeout, 30000)
    }
    window.electron = undefined
    await api.getDocumentCard('browser', signal)
    assert.equal('X-TeamHub-AI-Capability' in calls.at(-1).args.at(-1).headers, false)
  } finally { delete globalThis.__card_api }
})
