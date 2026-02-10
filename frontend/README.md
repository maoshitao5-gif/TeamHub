# 团队文件管理系统 - 前端

基于 Vue 3 + Vite + Element Plus 构建的现代化文件管理前端界面。

## 技术栈

- **Vue 3** - 渐进式 JavaScript 框架（使用 Composition API）
- **Vite** - 下一代前端构建工具
- **Element Plus** - 基于 Vue 3 的组件库
- **Axios** - HTTP 客户端

## 功能特性

- ✅ 拖拽上传文件
- ✅ 标签管理（上传前添加标签）
- ✅ 文件查重（上传前检测重复）
- ✅ 关键词搜索
- ✅ 标签筛选（渐进式搜索）
- ✅ 文件列表展示
- ✅ 现代化 UI 设计

## 快速开始

### 1. 安装依赖

```bash
cd frontend
npm install
```

### 2. 启动开发服务器

```bash
npm run dev
```

前端将在 `http://localhost:5173` 启动。

### 3. 构建生产版本

```bash
npm run build
```

构建产物将输出到 `dist/` 目录。

### 4. 预览生产版本

```bash
npm run preview
```

## 项目结构

```
frontend/
├── src/
│   ├── api/           # API 请求封装
│   │   ├── request.js # Axios 配置
│   │   └── file.js    # 文件相关 API
│   ├── components/    # Vue 组件
│   │   ├── FileUpload.vue  # 文件上传组件
│   │   ├── FileSearch.vue  # 文件搜索组件
│   │   └── FileList.vue    # 文件列表组件
│   ├── utils/         # 工具函数
│   │   └── format.js  # 格式化工具
│   ├── App.vue        # 根组件
│   └── main.js        # 入口文件
├── index.html         # HTML 模板
├── vite.config.js     # Vite 配置
└── package.json       # 项目配置
```

## 开发说明

### API 代理配置

前端通过 Vite 代理访问后端 API，配置在 `vite.config.js` 中：

```javascript
server: {
  proxy: {
    '/api': {
      target: 'http://localhost:8080',
      changeOrigin: true,
      rewrite: (path) => path.replace(/^\/api/, '')
    }
  }
}
```

### 后端服务

确保后端服务运行在 `http://localhost:8080`，并且已配置 CORS 支持。

## 浏览器支持

- Chrome (最新版本)
- Firefox (最新版本)
- Safari (最新版本)
- Edge (最新版本)

## 注意事项

1. 确保后端服务已启动并运行在 8080 端口
2. 上传文件前必须添加至少一个标签
3. 文件上传支持查重功能，重复文件会提示用户
