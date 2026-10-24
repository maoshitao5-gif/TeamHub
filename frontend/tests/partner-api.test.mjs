import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

test('智能伙伴请求沿用本机能力凭证；启动、取消与成果保存使用独立明确动作', async () => {
  const calls = []
  globalThis.window = { electron: { aiCapability: 'partner-capability-a' } }
  globalThis.__partner_api_fixture = Object.fromEntries(['get', 'post', 'delete'].map(method => [method, (...args) => {
    calls.push({ method, args }); return Promise.resolve({})
  }]))
  try {
    const source = (await fs.readFile(new URL('../src/api/partner.js', import.meta.url), 'utf8'))
      .replace("import request from './request'", 'const request = globalThis.__partner_api_fixture')
      .replace("'../utils/partnerStream.js'", JSON.stringify(new URL('../src/utils/partnerStream.js', import.meta.url).href))
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}`)
    const signal = new AbortController().signal
    await api.listPartnerSessions(signal)
    await api.createPartnerSession('设备资料分析', signal)
    await api.getPartnerSession('session /a', signal)
    await api.deletePartnerSession('session /a', signal)
    const preparation = { message: '比较两份资料', scope_mode: 'selected', document_ids: ['doc-a', 'doc-b'] }
    await api.preparePartnerTurn('session /a', preparation, signal)
    window.electron.aiCapability = 'partner-capability-b'
    await api.startPartnerTurn('session /a', 'prepare-a', 'request-a', signal)
    await api.getPartnerTask('task /a', signal)
    await api.cancelPartnerTask('task /a', signal)
    await api.savePartnerArtifact('task /a', 'artifact /a', 'save-a', signal)
    assert.deepEqual(calls.map(call => [call.method, call.args[0]]), [
      ['get', '/api/ai/partner/sessions'], ['post', '/api/ai/partner/sessions'],
      ['get', '/api/ai/partner/sessions/session%20%2Fa'], ['delete', '/api/ai/partner/sessions/session%20%2Fa'],
      ['post', '/api/ai/partner/sessions/session%20%2Fa/prepare'], ['post', '/api/ai/partner/sessions/session%20%2Fa/turns'],
      ['get', '/api/ai/partner/tasks/task%20%2Fa'], ['post', '/api/ai/partner/tasks/task%20%2Fa/cancel'],
      ['post', '/api/ai/partner/tasks/task%20%2Fa/artifacts/artifact%20%2Fa/save'],
    ])
    assert.deepEqual(calls[1].args[1], { title: '设备资料分析' })
    assert.deepEqual(calls[4].args[1], preparation)
    assert.deepEqual(calls[5].args[1], { preparation_id: 'prepare-a', consent: true, request_id: 'request-a' })
    assert.deepEqual(calls[8].args[1], { consent: true, request_id: 'save-a' })
    calls.forEach((call, index) => {
      const options = call.args.at(-1)
      assert.equal(options.headers['X-TeamHub-AI'], '1')
      assert.equal(options.headers['X-TeamHub-AI-Capability'], `partner-capability-${index < 5 ? 'a' : 'b'}`)
      assert.equal(options.signal, signal)
      assert.equal(options._silent, true)
      assert.equal(options.timeout, 30000)
    })
    window.electron = undefined
    await api.listPartnerSessions(signal)
    assert.equal('X-TeamHub-AI-Capability' in calls.at(-1).args.at(-1).headers, false)
  } finally { delete globalThis.__partner_api_fixture }
})

test('预算策略读取不启动模型；继续前独立准备与明确授权，并传递同一次幂等请求', async () => {
  const calls = [], request = Object.fromEntries(['get', 'post', 'delete'].map(method => [method, (...args) => { calls.push({ method, args }); return Promise.resolve({}) }]))
  globalThis.__partner_budget_api_fixture = request
  globalThis.window = { electron: { aiCapability: 'budget-capability' } }
  try {
    const source = (await fs.readFile(new URL('../src/api/partner.js', import.meta.url), 'utf8')).replace("import request from './request'", 'const request = globalThis.__partner_budget_api_fixture').replace("'../utils/partnerStream.js'", JSON.stringify(new URL('../src/utils/partnerStream.js', import.meta.url).href))
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#budget`)
    const signal = new AbortController().signal, budget = { mode: 'custom', model_requests: 48, tool_calls: 100, output_tokens: 60000, time_seconds: 900 }
    await api.getPartnerBudgetPolicy(signal)
    await api.preparePartnerContinuation('task /b', { budget }, signal)
    await api.continuePartnerTask('task /b', 'continuation-preparation', 'same-request-id', signal)
    assert.deepEqual(calls.map(({ method, args }) => [method, args[0]]), [['get', '/api/ai/partner/budget-policy'], ['post', '/api/ai/partner/tasks/task%20%2Fb/prepare-continue'], ['post', '/api/ai/partner/tasks/task%20%2Fb/continue']])
    assert.deepEqual(calls[1].args[1], { budget })
    assert.deepEqual(calls[2].args[1], { preparation_id: 'continuation-preparation', request_id: 'same-request-id', consent: true })
    for (const { args } of calls) {
      assert.equal(args.at(-1).signal, signal); assert.equal(args.at(-1).headers['X-TeamHub-AI-Capability'], 'budget-capability')
    }
  } finally { delete globalThis.__partner_budget_api_fixture }
})

test('实时订阅使用当前本机能力凭证与动态端口，拒绝错任务且失败不重发', async () => {
  const originalFetch = globalThis.fetch, calls = []
  globalThis.__partner_stream_api_fixture = { defaults:{ baseURL:'http://127.0.0.1:19876/' } }
  globalThis.window = { electron:{ aiCapability:'stream-capability-a' } }
  const feed = id => new Response(`event: task\ndata: ${JSON.stringify({ id, state:'completed' })}\n\nevent: done\ndata: {}\n\n`, { headers:{ 'Content-Type':'text/event-stream' } })
  globalThis.fetch = async (...args) => { calls.push(args); return feed('task /a') }
  try {
    const source = (await fs.readFile(new URL('../src/api/partner.js', import.meta.url), 'utf8')).replace("import request from './request'", 'const request = globalThis.__partner_stream_api_fixture').replace("'../utils/partnerStream.js'", JSON.stringify(new URL('../src/utils/partnerStream.js', import.meta.url).href))
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#stream`)
    const received = []
    await api.streamPartnerTask('task /a', value => received.push(value))
    assert.equal(calls[0][0], 'http://127.0.0.1:19876/api/ai/partner/tasks/task%20%2Fa/events')
    assert.equal(calls[0][1].headers['X-TeamHub-AI-Capability'], 'stream-capability-a')
    assert.equal(calls[0][1].credentials, 'omit'); assert.equal(calls[0][1].redirect, 'error')
    assert.deepEqual(received, [{ id:'task /a',state:'completed' }])
    window.electron.aiCapability = 'stream-capability-b'
    globalThis.fetch = async (...args) => { calls.push(args); return feed('wrong-task') }
    await assert.rejects(api.streamPartnerTask('task /a', () => assert.fail('错任务不得送入UI')), /不一致/)
    assert.equal(calls.at(-1)[1].headers['X-TeamHub-AI-Capability'], 'stream-capability-b')
    globalThis.fetch = async (...args) => { calls.push(args); return new Response('private secret', { status:403 }) }
    await assert.rejects(api.streamPartnerTask('task /a', () => {}), error => error.message.includes('403') && !error.message.includes('private secret'))
    assert.equal(calls.length, 3)
  } finally { globalThis.fetch = originalFetch; delete globalThis.__partner_stream_api_fixture }
})

test('直接发送和续行绑定可见服务、范围及幂等编号，一次调用不触发旧准备接口', async () => {
  const calls = []
  globalThis.__partner_direct_fixture = Object.fromEntries(['get', 'post'].map(method => [method, (...args) => { calls.push({ method, args }); return Promise.resolve({}) }]))
  globalThis.window = { electron: { aiCapability: 'direct-capability' } }
  try {
    const source = (await fs.readFile(new URL('../src/api/partner.js', import.meta.url), 'utf8')).replace("import request from './request'", 'const request = globalThis.__partner_direct_fixture').replace("'../utils/partnerStream.js'", JSON.stringify(new URL('../src/utils/partnerStream.js', import.meta.url).href))
    const api = await import(`data:text/javascript;base64,${Buffer.from(source).toString('base64')}#direct`)
    const signal = new AbortController().signal
    const send = { message: '继续比较', scope_mode: 'selected', document_ids: ['a'], thinking_mode: 'disabled', request_id: 'send-a', expected_service_revision: 'service-a', consent: true }
    const resume = { request_id: 'resume-a', expected_service_revision: 'service-a', consent: true }
    await api.getPartnerChatConfig(signal); await api.sendPartnerMessage('session /a', send, signal); await api.resumePartnerTask('task /a', resume, signal); await api.getPartnerRequestTask('session /a', 'request /a', signal)
    assert.deepEqual(calls.map(({ method, args }) => [method, args[0]]), [['get', '/api/ai/partner/chat-config'], ['post', '/api/ai/partner/sessions/session%20%2Fa/send'], ['post', '/api/ai/partner/tasks/task%20%2Fa/resume'], ['get', '/api/ai/partner/sessions/session%20%2Fa/requests/request%20%2Fa']])
    assert.deepEqual(calls[1].args[1], send); assert.deepEqual(calls[2].args[1], resume)
    for (const { args } of calls) { assert.equal(args.at(-1).signal, signal); assert.equal(args.at(-1).headers['X-TeamHub-AI-Capability'], 'direct-capability') }
  } finally { delete globalThis.__partner_direct_fixture }
})
