import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { resolve } from 'path'
import { copyFileSync } from 'fs'

// 构建后把 floating-window.js 复制到 dist/，与 floating-window.html 同级
const copyFloatingScript = {
  name: 'copy-floating-script',
  closeBundle() {
    try {
      copyFileSync(
        resolve(__dirname, 'floating-window.js'),
        resolve(__dirname, 'dist/floating-window.js')
      )
    } catch (e) {
      // 开发模式下 dist/ 不存在，忽略
    }
  }
}

export default defineConfig({
  plugins: [vue(), copyFloatingScript],
  resolve: {
    alias: {
      '@': resolve(__dirname, 'src')
    }
  },
  server: {
    port: 5173,
    strictPort: false,
    proxy: {
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
      }
    },
    terserOptions: {
      compress: {
        drop_console: true,
        drop_debugger: true
      }
    }
  }
})
