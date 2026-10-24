/**
 * 本地后端 Axios 实例（连接本地 FastAPI，端口 8001-8010）
 *
 * 功能：
 *   - 自动适配 Electron 环境动态端口（通过 window.electron.apiBaseURL）
 *   - 响应统一解包（返回 response.data）
 *   - 错误消息自动弹 ElMessage.error（可通过 _silent: true 关闭）
 *   - 支持 blob 下载（responseType: 'blob' 时返回完整 Response）
 *   - 重复文件错误（error_type: duplicate_file）不弹全局提示，由页面自行处理
 */
import axios from 'axios'
import { ElMessage } from 'element-plus'
import { getCloudSessionScope, notifyCloudSessionExpiry } from '@/utils/cloudSession'

// 获取 API 基础 URL
// 优先使用 Electron 环境提供的 URL，否则使用默认值
const getBaseURL = () => {
  if (window.electron?.apiBaseURL) {
    return window.electron.apiBaseURL
  }
  return import.meta.env.VITE_LOCAL_API_URL || 'http://127.0.0.1:8001'
}

// 创建 axios 实例
const request = axios.create({
  baseURL: getBaseURL(),  // 支持 Electron 环境的动态 URL
  timeout: 30000,   // 30秒超时
  proxy: false,     // 绕过系统代理，直连本地后端
  // 确保请求和响应都使用 UTF-8 编码
  headers: {
    'Content-Type': 'application/json; charset=utf-8'
  }
})

// 仅展示实际连接的本机地址，避免把路径、凭据或错误原文带入提示。
const getLocalServiceOrigin = (config) => {
  const baseURL = config?.baseURL || request.defaults?.baseURL || getBaseURL()
  try {
    const url = new URL(baseURL)
    const isLoopback = url.hostname === 'localhost' || url.hostname === '[::1]' || /^127(?:\.\d{1,3}){3}$/.test(url.hostname)
    return ['http:', 'https:'].includes(url.protocol) && isLoopback ? url.origin : ''
  } catch {
    return ''
  }
}

const getConnectionErrorMessage = (error) => {
  const download = error.config?.responseType === 'blob' ? '下载未完成，' : ''
  const origin = getLocalServiceOrigin(error.config)
  const address = origin ? `（服务地址：${origin}）` : ''
  if (['ECONNABORTED', 'ETIMEDOUT'].includes(error.code)) {
    return `${download}本机服务响应超时，请稍后重试${address}`
  }
  const isNetworkError = ['ERR_NETWORK', 'ECONNREFUSED'].includes(error.code) ||
    (typeof error.message === 'string' && error.message.includes('Network'))
  if (isNetworkError) {
    return `${download}本机服务暂时失去连接，请稍后重试或重新打开 TeamHub${address}`
  }
  return `${download}请求未完成，请稍后重试${address}`
}

// 为每次请求记录无凭据的会话代次，防止迟到的旧请求影响新账号。
request.interceptors.request.use(
  (config) => {
    config._cloudSessionScope = getCloudSessionScope()
    // 能力凭据仅发给 Electron 启动的个人服务，绝不跟随绝对 URL 到云端。
    const header = 'X-TeamHub-AI-Capability'
    config.headers ??= {}
    for (const key of Object.keys(config.headers)) {
      if (key.toLowerCase() === header.toLowerCase()) delete config.headers[key]
    }
    try {
      const trusted = new URL(window.electron?.apiBaseURL)
      const target = new URL(config.url || '', config.baseURL || request.defaults?.baseURL || getBaseURL())
      const loopback = trusted.hostname === '127.0.0.1' || trusted.hostname === 'localhost' || trusted.hostname === '[::1]'
      if (loopback && ['http:', 'https:'].includes(trusted.protocol) &&
          !trusted.username && !trusted.password && !target.username && !target.password &&
          target.origin === trusted.origin && window.electron.aiCapability) {
        config.headers[header] = window.electron.aiCapability
      }
    } catch { /* 普通浏览器或无效地址不附带桌面凭据。 */ }
    return config
  },
  (error) => Promise.reject(error),
  { synchronous: true }
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    // 对于 blob 类型的响应，返回完整的 response 对象
    // 因为下载文件需要访问 response.data (blob) 和 response.headers
    if (response.config.responseType === 'blob') {
      return response
    }
    return response.data
  },
  async (error) => {
    // 首先处理网络错误（没有 response 的情况），包括 blob 类型的请求
    if (!error.response) {
      const message = getConnectionErrorMessage(error)
      if (!error.config?._silent) ElMessage.error(message)
      const enhancedError = new Error(message, { cause: error })
      enhancedError.code = error.code
      enhancedError.response = error.response
      enhancedError.config = error.config
      enhancedError.request = error.request
      return Promise.reject(enhancedError)
    }
    
    // Blob 请求也可能返回 JSON 认证错误；只有明确终态码才影响云端登录。
    if (error.config?.responseType === 'blob' && error.response.data instanceof Blob) {
      try {
        const text = await error.response.data.text()
        error.response = { ...error.response, data: JSON.parse(text) }
      } catch {
        // 非 JSON 下载错误保留原响应，按状态码提示。
      }
    }
    const terminalExpiry = notifyCloudSessionExpiry(error.response, error.config?._cloudSessionScope)

    // 对于非 blob 类型的错误，使用标准错误处理
    const status = error.response?.status
    let message = '请求失败'
    
    // 处理有响应但状态码错误的情况
    if (error.response?.data) {
      const structuredMessage = error.response.data?.detail?.error?.message || error.response.data?.error?.message
      if (typeof structuredMessage === 'string') {
        message = structuredMessage
      } else if (typeof error.response.data === 'object' && error.response.data.detail) {
        // detail 可能是字符串或对象
        if (typeof error.response.data.detail === 'string') {
          message = error.response.data.detail
        } else if (typeof error.response.data.detail === 'object') {
          // 如果是对象，尝试提取消息
          message = error.response.data.detail.message || error.response.data.detail.detail || JSON.stringify(error.response.data.detail)
        } else {
          message = String(error.response.data.detail)
        }
      } else if (typeof error.response.data === 'string') {
        message = error.response.data
      } else if (error.response.data instanceof Blob) {
        message = status === 404 ? '文件不存在' : status === 403 ? '无权限访问' : '下载失败'
      }
    } else if (status === 404) {
      message = '文件不存在'
    } else if (error.message) {
      message = error.message
    }
    
    // 确保 message 是字符串
    if (typeof message !== 'string') {
      message = String(message)
    }
    
    // 保留原始错误信息，以便组件可以访问 error.response
    // 只有在不是重复文件错误时才显示通用错误消息
    // 重复文件错误由组件自己处理
    const isDuplicateError = error.response?.status === 400 && 
                            error.response?.data?.detail &&
                            typeof error.response.data.detail === 'object' &&
                            error.response.data.detail.error_type === 'duplicate_file'
    
    // 如果错误消息包含换行符，不自动显示，让组件自己处理（使用 MessageBox）
    // 这样可以显示多行错误消息
    const isMultiLineError = message.includes('\n')
    
    // _silent: true 表示调用方自己处理错误，不弹全局 toast
    const isSilent = error.config?._silent === true

    if (!terminalExpiry && status !== 401 && !isDuplicateError && !isMultiLineError && !isSilent) {
      ElMessage.error(message)
    }
    
    // 保留原始错误对象，以便组件可以访问 error.response
    const enhancedError = new Error(message)
    enhancedError.response = error.response
    enhancedError.config = error.config
    enhancedError.request = error.request
    return Promise.reject(enhancedError)
  }
)

export default request
