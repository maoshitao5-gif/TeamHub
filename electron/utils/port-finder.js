const net = require('net');

class PortFinder {
  /**
   * 检查端口是否可用
   * @param {number} port - 要检查的端口号
   * @returns {Promise<boolean>} - 端口是否可用
   */
  static async isPortAvailable(port) {
    return new Promise((resolve) => {
      const server = net.createServer();

      server.once('error', (err) => {
        if (err.code === 'EADDRINUSE') {
          resolve(false);
        } else {
          resolve(false);
        }
      });

      server.once('listening', () => {
        server.close();
        resolve(true);
      });

      server.listen(port, '127.0.0.1');
    });
  }

  /**
   * 在指定范围内查找可用端口
   * @param {number} startPort - 起始端口
   * @param {number} endPort - 结束端口
   * @returns {Promise<number|null>} - 可用端口号，如果都不可用则返回 null
   */
  static async findAvailablePort(startPort, endPort) {
    for (let port = startPort; port <= endPort; port++) {
      const available = await this.isPortAvailable(port);
      if (available) {
        console.log(`[PortFinder] Found available port: ${port}`);
        return port;
      }
    }

    console.error(`[PortFinder] No available port found in range ${startPort}-${endPort}`);
    return null;
  }

  /**
   * 等待端口变为可用（用于等待服务启动）
   * @param {number} port - 端口号
   * @param {number} timeout - 超时时间（毫秒）
   * @param {number} interval - 检查间隔（毫秒）
   * @returns {Promise<boolean>} - 端口是否在超时前变为可用
   */
  static async waitForPort(port, timeout = 30000, interval = 500) {
    const startTime = Date.now();

    while (Date.now() - startTime < timeout) {
      const available = await this.isPortAvailable(port);
      if (!available) {
        // 端口被占用，说明服务已启动
        console.log(`[PortFinder] Port ${port} is now in use (service started)`);
        return true;
      }
      await new Promise(resolve => setTimeout(resolve, interval));
    }

    console.error(`[PortFinder] Timeout waiting for port ${port} to be in use`);
    return false;
  }
}

module.exports = PortFinder;
