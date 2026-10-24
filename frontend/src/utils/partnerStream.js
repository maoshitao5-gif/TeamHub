const MAX_EVENT_CHARACTERS = 4 * 1024 * 1024
const terminal = new Set(['completed', 'paused', 'failed', 'cancelled', 'interrupted'])

export async function readPartnerEvents(response, onTask, signal, onActivity = () => {}) {
  if (!response.body || !response.headers.get('Content-Type')?.includes('text/event-stream')) throw new Error('实时进度格式不正确。')
  const reader = response.body.getReader(), decoder = new TextDecoder()
  let buffer = '', lastState = '', finished = false
  const abort = () => { void reader.cancel().catch(() => {}) }
  signal?.addEventListener('abort', abort, { once: true })
  try {
    if (signal?.aborted) throw new DOMException('订阅已中止', 'AbortError')
    while (!finished) {
      const { value, done } = await reader.read()
      if (signal?.aborted) throw new DOMException('订阅已中止', 'AbortError')
      if (done) throw new Error('实时进度连接已断开。')
      onActivity()
      buffer += decoder.decode(value, { stream: true })
      let boundary
      while ((boundary = /\r?\n\r?\n/.exec(buffer))) {
        const frame = buffer.slice(0, boundary.index)
        if (frame.length > MAX_EVENT_CHARACTERS) throw new Error('实时进度事件过大。')
        buffer = buffer.slice(boundary.index + boundary[0].length)
        let kind = 'message'; const data = []
        for (const line of frame.split(/\r?\n/)) {
          if (line.startsWith('event:')) kind = line.slice(6).trim()
          else if (line.startsWith('data:')) data.push(line.slice(5).replace(/^ /, ''))
        }
        if (kind === 'done') {
          if (!terminal.has(lastState)) throw new Error('实时进度提前结束。')
          finished = true; break
        }
        if (kind !== 'task') continue
        let task
        try { task = JSON.parse(data.join('\n')) } catch { throw new Error('实时进度格式不正确。') }
        if (!task || typeof task !== 'object' || typeof task.id !== 'string' || typeof task.state !== 'string') throw new Error('实时进度格式不正确。')
        lastState = task.state
        onTask(task)
      }
      if (buffer.length > MAX_EVENT_CHARACTERS) throw new Error('实时进度事件超过上限。')
    }
  } finally {
    signal?.removeEventListener('abort', abort)
    await reader.cancel().catch(() => {})
    reader.releaseLock()
  }
}
