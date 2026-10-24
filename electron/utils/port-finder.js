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

      server.once('error', () => resolve(false));

      server.once('listening', () => {
        server.close(error => resolve(!error));
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

}

module.exports = PortFinder;
