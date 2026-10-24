import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs/promises'

const location = new URL('../src/utils/partnerStream.js', import.meta.url)
let readPartnerEvents
try { await fs.access(location); ({ readPartnerEvents } = await import(location.href)) }
catch (error) {
  if (error.code !== 'ENOENT') throw error
  // The pre-streaming page only obtains completed snapshots by polling.
  readPartnerEvents = async () => { throw new Error('实时进度尚未接入') }
}
const event = task => `event: task\ndata: ${JSON.stringify(task)}\n\n`
function response(chunks) {
  const encoder = new TextEncoder()
  return new Response(new ReadableStream({ start(controller) { for (const chunk of chunks) controller.enqueue(typeof chunk === 'string' ? encoder.encode(chunk) : chunk); controller.close() } }), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('SSE分片与UTF8分片能实时产生快照，只有done结束订阅', async () => {
  const bytes = new TextEncoder().encode(event({ id: 'a', state: 'running', answer_preview: '中文回答' }) + event({ id: 'a', state: 'completed', answer: '最终回答' }) + 'event: done\ndata: {}\n\n')
  const received = []
  await readPartnerEvents(response([...bytes].map(byte => new Uint8Array([byte]))), value => received.push(value))
  assert.equal(received.length, 2); assert.equal(received[0].answer_preview, '中文回答'); assert.equal(received[1].state, 'completed')
})
test('心跳及CRLF可混合，非task事件不进入对话', async () => {
  const received = []
  await readPartnerEvents(response([': heartbeat\r\n\r\nevent: unrelated\r\ndata: ignored\r\n\r\n', event({ id:'a', state:'cancelled' }).replaceAll('\n','\r\n'), 'event: done\r\ndata: {}\r\n\r\n']), value => received.push(value))
  assert.deepEqual(received, [{ id:'a', state:'cancelled' }])
})
test('断流、无效JSON及超长事件会明确报错以触发有界状态查询', async () => {
  await assert.rejects(readPartnerEvents(response([event({ id:'a',state:'running' })]), () => {}), /中断|结束|断开/)
  await assert.rejects(readPartnerEvents(response(['event: task\ndata: {bad}\n\n']), () => {}), /格式/)
  await assert.rejects(readPartnerEvents(response(['data: ' + 'x'.repeat(4 * 1024 * 1024 + 1)]), () => {}), /过大|上限/)
})
test('中止订阅会释放流读取器，不取消后台任务', async () => {
  const control = new AbortController(); let released = false
  const pending = new Response(new ReadableStream({ cancel() { released = true } }), { headers: { 'Content-Type':'text/event-stream' } })
  const reading = readPartnerEvents(pending, () => {}, control.signal)
  control.abort()
  await assert.rejects(reading, error => error.name === 'AbortError')
  assert.equal(released, true)
})
