const fs = require('node:fs');
const path = require('node:path');
const { spawnSync } = require('node:child_process');

// 构建只使用工程内预先准备的环境；发布后的客户端不安装依赖、不下载代码。
function buildHermes({ root = path.resolve(__dirname, '..'), platform = process.platform,
  execute = spawnSync, logger = console } = {}) {
  root = path.resolve(root);
  const python = path.join(root, '.mvp-sandbox/hermes-venv',
    platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  const source = path.join(root, '.mvp-sandbox/hermes-src');
  const worker = path.join(root, 'integrations/hermes/worker.py');
  const spec = path.join(root, 'teamhub-hermes.spec');
  const preparation = path.join(root, 'scripts/prepare-hermes.py');
  const inputs = [python, path.join(source, 'run_agent.py'),
    path.join(source, 'LICENSE'), worker, path.join(root, 'integrations/hermes/upstream.json'), spec, preparation];
  for (const input of inputs) {
    try {
      if (!fs.statSync(input).isFile()) throw new Error('not a regular file');
      if (input === python) fs.accessSync(input, fs.constants.X_OK);
    } catch {
      logger.error(`Hermes 构建输入缺失或不可用：${input}。请先准备锁定版本的 Hermes 源码及独立构建环境。`);
      return 1;
    }
  }
  const options = { cwd: root, stdio: 'inherit',
    env: { ...process.env, TEAMHUB_HERMES_SOURCE: source, TEAMHUB_HERMES_WORKER: worker } };
  const verification = execute(python, [preparation, '--source-only', '--offline'], options);
  if (verification.error) logger.error(`Hermes 源码校验启动失败：${verification.error.message}`);
  if (verification.status !== 0) return verification.status ?? 1;
  const result = execute(python, ['-m', 'PyInstaller', '--noconfirm',
    '--distpath', path.join(root, 'dist'),
    '--workpath', path.join(root, 'build/hermes-pyinstaller'), spec], options);
  if (result.error) logger.error(`Hermes 构建进程启动失败：${result.error.message}`);
  return result.status ?? 1;
}

if (require.main === module) process.exitCode = buildHermes();

module.exports = { buildHermes };
