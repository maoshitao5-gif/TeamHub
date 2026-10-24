import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { copyFileSync, readdirSync, readFileSync } from 'fs'

// 构建后把 floating-window.js 复制到 dist/，与 floating-window.html 同级
const copyFloatingScript = {
  name: 'copy-floating-script',
  apply: 'build',
  closeBundle() {
    copyFileSync(
      resolve(__dirname, 'floating-window.js'),
      resolve(__dirname, 'dist/floating-window.js')
    )
  }
}

// PDF 预览的字体与 CMap 跟随应用打包，内网和桌面均无需访问外部资源。
const pdfPreviewAssets = {
  name: 'pdf-preview-assets',
  configureServer(server) {
    server.middlewares.use('/assets/pdfjs', (request, response, next) => {
      const match = /^\/(cmaps|standard_fonts)\/([A-Za-z0-9_-]+\.[A-Za-z0-9]+)$/.exec((request.url || '').split('?')[0])
      if (!match) { next(); return }
      try {
        const content = readFileSync(resolve(__dirname, 'node_modules/pdfjs-dist', match[1], match[2]))
        response.setHeader('Content-Type', 'application/octet-stream')
        response.setHeader('X-Content-Type-Options', 'nosniff')
        response.end(content)
      } catch { response.statusCode = 404; response.end() }
    })
  },
  generateBundle() {
    for (const directory of ['cmaps', 'standard_fonts']) {
      const source = resolve(__dirname, 'node_modules/pdfjs-dist', directory)
      for (const entry of readdirSync(source, { withFileTypes: true })) {
        if (!entry.isFile() || /^README/i.test(entry.name)) continue
        this.emitFile({
          type: 'asset', fileName: `assets/pdfjs/${directory}/${entry.name}`,
          source: readFileSync(resolve(source, entry.name)),
        })
      }
    }
    this.emitFile({ type: 'asset', fileName: 'assets/pdfjs/LICENSE', source: readFileSync(resolve(__dirname, 'node_modules/pdfjs-dist/LICENSE')) })
  },
}

export default defineConfig({
  // Electron 生产模式以 file:// 加载打包产物,资源须用相对路径
  base: './',
  plugins: [vue(), copyFloatingScript, pdfPreviewAssets, {
    name: 'packaged-csp', apply: 'build',
    transformIndexHtml(html, context) {
      const cloudPage = /(?:cloud|admin)\.html$/.test(context.filename)
      return [{ tag: 'meta', injectTo: 'head-prepend', attrs: {
        'http-equiv': 'Content-Security-Policy',
        content: cloudPage
          ? "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; font-src 'self' data:; object-src 'none'; base-uri 'none'; form-action 'self'"
          : "default-src 'self'; script-src 'self'; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: http://127.0.0.1:*; connect-src 'self' http://127.0.0.1:*; font-src 'self' data:; object-src 'none'; base-uri 'none'",
      } }]
    },
  }],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:9000',
        // 保留浏览器 Host，服务端据此核对同源 Origin。
        changeOrigin: false,
      },
      // 将 /cloud-backend 前缀的请求代理到云服务（9000 端口）
      // 浏览器只与 Vite dev server 通信，绕过系统代理问题
      '/cloud-backend': {
        target: 'http://127.0.0.1:9000',
        rewrite: (path) => path.replace(/^\/cloud-backend/, ''),
        changeOrigin: true,
      }
    }
  },
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        'floating-window': resolve(__dirname, 'floating-window.html'),
        admin: resolve(__dirname, 'admin.html'),
        cloud: resolve(__dirname, 'cloud.html'),
      }
    }
  }
})
