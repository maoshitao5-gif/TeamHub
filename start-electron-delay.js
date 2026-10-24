const { spawn } = require('child_process');
const { randomBytes } = require('crypto');
const path = require('path');
const { pathToFileURL } = require('url');
const { createChallengeMiddleware } = require('./electron/utils/dev-frontend');

// 启动器持有 Vite 实例与随机凭据，不探测/借用其他应用占用的端口。
async function startFrontend({ port = 5173 } = {}) {
  const frontend = path.join(__dirname, 'frontend');
  const vitePackage = require.resolve('vite/package.json', { paths: [frontend] });
  const { createServer } = await import(pathToFileURL(path.join(path.dirname(vitePackage), 'dist/node/index.js')).href);
  const secret = randomBytes(32).toString('hex');
  const server = await createServer({
    root: frontend,
    configFile: path.join(frontend, 'vite.config.js'),
    server: { host: '127.0.0.1', port, strictPort: false },
    plugins: [{
      name: 'teamhub-desktop-launch-challenge',
      configureServer(vite) { vite.middlewares.use(createChallengeMiddleware(secret)); },
    }],
  });
  try {
    await server.listen();
    const address = server.httpServer.address();
    return { server, secret, url: `http://127.0.0.1:${address.port}/` };
  } catch (error) {
    await server.close();
    throw error;
  }
}

async function startElectron() {
  const { server, secret, url } = await startFrontend();
  console.log(`[Launcher] Desktop frontend ready at ${url}`);
  const env = { ...process.env, TEAMHUB_DEV_FRONTEND_URL: url, TEAMHUB_DEV_FRONTEND_SECRET: secret };
  delete env.ELECTRON_RUN_AS_NODE;
  let child;
  try {
    child = spawn(require('electron'), ['.'], { stdio: 'inherit', cwd: __dirname, env });
  } catch (error) {
    await server.close();
    throw error;
  }
  let closing = false;
  const shutdown = async code => {
    if (closing) return;
    closing = true;
    if (child.exitCode === null && child.signalCode === null) child.kill('SIGTERM');
    await server.close();
    process.exitCode = code;
  };
  child.on('error', error => {
    console.error('Failed to start Electron:', error.message);
    void shutdown(1);
  });
  child.on('exit', code => { void shutdown(code ?? 1); });
  process.once('SIGINT', () => { void shutdown(130); });
  process.once('SIGTERM', () => { void shutdown(143); });
}

if (require.main === module) {
  startElectron().catch(error => {
    console.error('Error starting desktop development:', error.message);
    process.exitCode = 1;
  });
}

module.exports = { startFrontend };
