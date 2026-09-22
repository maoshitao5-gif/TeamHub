import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import 'element-plus/dist/index.css'
import 'element-plus/theme-chalk/dark/css-vars.css'
import * as ElementPlusIconsVue from '@element-plus/icons-vue'
import zhCn from 'element-plus/dist/locale/zh-cn.mjs'
import App from './App.vue'
import router from './router'
import './styles/global.css'

const app = createApp(App)
const pinia = createPinia()

// 注册所有图标
try {
  for (const [key, component] of Object.entries(ElementPlusIconsVue)) {
    app.component(key, component)
  }
} catch (e) {
  console.error('Icon registration error:', e)
}

try {
  app.use(pinia)
} catch (e) {
  console.error('Pinia installation error:', e)
}

try {
  app.use(ElementPlus, { locale: zhCn })
} catch (e) {
  console.error('ElementPlus installation error:', e)
}

try {
  app.use(router)
} catch (e) {
  console.error('Router installation error:', e)
}

// 全局错误处理
app.config.errorHandler = (err, instance, info) => {
  console.error('Vue error:', err, info, instance)
}

// 全局警告处理
app.config.warnHandler = (msg, instance, trace) => {
  console.warn('Vue warning:', msg, trace, instance)
}

try {
  app.mount('#app')
} catch (e) {
  console.error('Mount error:', e)
}
