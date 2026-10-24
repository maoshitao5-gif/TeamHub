import test from 'node:test'
import assert from 'node:assert/strict'
import http from 'node:http'
import { once } from 'node:events'
import { createRequire } from 'node:module'
import { createHmac, randomBytes } from 'node:crypto'

const require = createRequire(import.meta.url)
const { startFrontend } = require('../../start-electron-delay.js')

test('launcher owns a distinct Vite instance when the requested port belongs to another service', async () => {
  const foreign = http.createServer((_request, response) => response.end('<html>other application</html>'))
  foreign.listen(0, '127.0.0.1')
  await once(foreign, 'listening')
  let launched
  try {
    const foreignPort = foreign.address().port
    launched = await startFrontend({ port: foreignPort })
    assert.notEqual(new URL(launched.url).port, String(foreignPort))
    const challenge = randomBytes(32).toString('hex')
    const response = await fetch(`${launched.url}__teamhub_desktop_challenge?challenge=${challenge}`, { redirect: 'error' })
    assert.equal(response.status, 200)
    assert.equal(response.headers.get('cache-control'), 'no-store')
    assert.equal(await response.text(), createHmac('sha256', launched.secret).update(challenge).digest('hex'))
    const invalid = await fetch(`${launched.url}__teamhub_desktop_challenge?challenge=invalid`)
    assert.equal(invalid.status, 400)
    const page = await fetch(launched.url)
    assert.equal(page.status, 200)
    assert.match(await page.text(), /src\/main\.js/)
    assert.equal(await (await fetch(`http://127.0.0.1:${foreignPort}/`)).text(), '<html>other application</html>')
  } finally {
    if (launched) {
      await launched.server.environments.client.depsOptimizer?.scanProcessing
      await launched.server.close()
    }
    foreign.closeAllConnections()
    await new Promise(resolve => foreign.close(resolve))
  }
})
