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

// 获取 API 基础 URL
// 优先使用 Electron 环境提供的 URL，否则使用默认值
const getBaseURL = () => {
  if (window.electron?.apiBaseURL) {
    return window.electron.apiBaseURL
  }
  return 'http://127.0.0.1:8001'
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

// 请求拦截器（本地后端无认证，直接放行）
request.interceptors.request.use(
  (config) => config,
  (error) => Promise.reject(error)
)

// 响应拦截器
request.interceptors.response.use(
  (response) => {
    // 对于 blob 类型的响应，返回完整的 response 对象
    // 因为下载文件需要访问 response.data (blob) 和 response.headers
    if (response.config.responseType === 'blob') {
      return response
    }
    // 对于普通响应，返回 response.data
    // 但保留完整的 response 对象在 data 上，以便需要时访问
    const data = response.data
    if (data && typeof data === 'object') {
      // 将完整的 response 对象附加到 data 上，以便错误处理时能访问
      data._response = response
    }
    return data
  },
  (error) => {
    // 首先处理网络错误（没有 response 的情况），包括 blob 类型的请求
    if (!error.response) {
      const isBlobRequest = error.config?.responseType === 'blob'
      let message = '请求失败'
      
      if (error.code === 'ERR_NETWORK' || error.message === 'Network Error' || error.message?.includes('Network')) {
        if (isBlobRequest) {
          message = '下载请求失败，可能是 CORS 问题或后端服务未运行。请检查：1) 后端服务是否运行在 http://127.0.0.1:8001；2) 浏览器控制台是否有 CORS 错误'
        } else {
          message = '网络连接失败，请检查后端服务是否运行在 http://127.0.0.1:8001'
        }
        console.error('网络错误详情:', {
          message: error.message,
          code: error.code,
          config: error.config,
          isBlobRequest
        })
      } else if (error.code === 'ECONNREFUSED') {
        message = '无法连接到后端服务，请确认后端服务已启动'
      } else {
        message = `网络错误: ${error.message || '未知错误'}`
      }
      ElMessage.error(message)
      return Promise.reject(new Error(message))
    }
    
    // 对于 blob 类型的错误响应，尝试解析错误信息
    if (error.config?.responseType === 'blob' && error.response) {
      // 当 responseType 是 blob 时，error.response.data 已经是 Blob 对象
      // FastAPI 的错误响应通常是 JSON 格式，需要读取并解析
      return new Promise((resolve, reject) => {
        const blob = error.response.data
        const status = error.response?.status || 500
        
        if (blob instanceof Blob && blob.size > 0) {
          const reader = new FileReader()
          reader.onload = () => {
            try {
              // 尝试解析 JSON 错误信息
              const text = reader.result
              if (text && typeof text === 'string' && text.trim()) {
                try {
                  const errorData = JSON.parse(text)
                  const message = errorData.detail || errorData.message || '请求失败'
                  ElMessage.error(message)
                  reject(new Error(message))
                  return
                } catch (parseError) {
                  // JSON 解析失败，检查是否是纯文本错误信息
                  if (text.includes('detail') || text.includes('message')) {
                    // 尝试提取错误信息
                    const detailMatch = text.match(/"detail"\s*:\s*"([^"]+)"/)
                    if (detailMatch) {
                      const message = detailMatch[1]
                      ElMessage.error(message)
                      reject(new Error(message))
                      return
                    }
                  }
                }
              }
              // 如果无法解析，使用状态码判断
              const message = status === 404 ? '文件不存在' : status === 403 ? '无权限访问' : status === 500 ? '服务器错误' : '下载失败'
              ElMessage.error(message)
              reject(new Error(message))
            } catch (handlerError) {
              // 处理函数本身出错，使用状态码判断
              const message = status === 404 ? '文件不存在' : '下载失败'
              ElMessage.error(message)
              reject(new Error(message))
            }
          }
          reader.onerror = () => {
            // 读取失败，使用状态码判断
            const message = status === 404 ? '文件不存在' : '下载失败'
            ElMessage.error(message)
            reject(new Error(message))
          }
          reader.readAsText(blob)
        } else {
          // 不是 Blob 或 Blob 为空，直接使用状态码判断
          const message = status === 404 ? '文件不存在' : status === 403 ? '无权限访问' : '下载失败'
          ElMessage.error(message)
          reject(new Error(message))
        }
      })
    }
    
    // 对于非 blob 类型的错误，使用标准错误处理
    const status = error.response?.status
    let message = '请求失败'
    
    // 处理 401 未授权错误（不再需要跳转到登录页）
    if (status === 401) {
      message = error.response?.data?.detail || '未授权访问'
      const enhancedError = new Error(message)
      enhancedError.response = error.response
      enhancedError.config = error.config
      enhancedError.request = error.request
      return Promise.reject(enhancedError)
    }
    
    // 处理有响应但状态码错误的情况
    if (error.response?.data) {
      if (typeof error.response.data === 'object' && error.response.data.detail) {
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

    if (!isDuplicateError && !isMultiLineError && !isSilent) {
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
