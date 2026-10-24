const { test } = require('node:test');
const assert = require('node:assert/strict');
const net = require('node:net');
const PortFinder = require('../electron/utils/port-finder');

async function listen(server, port = 0) {
  await new Promise((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, '127.0.0.1', resolve);
  });
  return server.address().port;
}

test('occupied ports are skipped without closing the existing listener', async t => {
  const server = net.createServer();
  const port = await listen(server);
  t.after(() => new Promise(resolve => server.close(resolve)));
  assert.equal(await PortFinder.isPortAvailable(port), false);
  assert.equal(await PortFinder.findAvailablePort(port, port), null);
  assert.equal(server.listening, true);
});

test('successful probe releases the port before handing it to the application', async t => {
  const probe = net.createServer();
  const port = await listen(probe);
  await new Promise(resolve => probe.close(resolve));
  assert.equal(await PortFinder.isPortAvailable(port), true);
  const application = net.createServer();
  t.after(() => new Promise(resolve => application.close(resolve)));
  assert.equal(await listen(application, port), port);
});
