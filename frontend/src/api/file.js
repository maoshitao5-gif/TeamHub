/**
 * 文件相关 API
 */
import request from './request'

/**
 * 上传文件
 * @param {FormData} formData - 包含文件和标签的表单数据
 * @param {Function} onUploadProgress - 上传进度回调函数
 * @returns {Promise}
 */
export function uploadFile(formData, onUploadProgress) {
  return request.post('/upload', formData, {
    headers: {
      'Content-Type': 'multipart/form-data; charset=utf-8'
    },
    onUploadProgress: onUploadProgress ? (progressEvent) => {
      // 确保进度事件正确传递
      if (progressEvent.total) {
        onUploadProgress(progressEvent)
      }
    } : undefined
  })
}

/**
 * 上传文件夹（批量上传）
 * @param {FormData} formData - 包含文件列表、相对路径和标签的表单数据
 * @param {Function} onUploadProgress - 上传进度回调函数
 * @returns {Promise}
 */
export function uploadFolder(formData, onUploadProgress) {
  return request.post('/upload-folder', formData, {
    headers: {
      'Content-Type': 'multipart/form-data; charset=utf-8'
    },
    onUploadProgress: onUploadProgress ? (progressEvent) => {
      // 确保进度事件正确传递
      if (progressEvent.total) {
        onUploadProgress(progressEvent)
      }
    } : undefined
  })
}

/**
 * 获取所有标签（按使用频率排序）
 * @returns {Promise}
 */
export function getTags() {
  return request.get('/tags')
}

/**
 * 获取标签统计信息
 * @returns {Promise}
 */
export function getTagsStats() {
  return request.get('/tags/stats')
}

/**
 * 创建标签
 * @param {string} name - 标签名称
 * @returns {Promise}
 */
export function createTag(name) {
  return request.post('/tags', {
    name
  })
}

/**
 * 更新标签
 * @param {number} tagId - 标签ID
 * @param {string} name - 新标签名称
 * @returns {Promise}
 */
export function updateTag(tagId, name) {
  return request.put(`/tags/${tagId}`, {
    name
  })
}

/**
 * 删除标签
 * @param {number} tagId - 标签ID
 * @returns {Promise}
 */
export function deleteTag(tagId) {
  return request.delete(`/tags/${tagId}`)
}

/**
 * 批量删除标签
 * @param {number[]} tagIds - 标签ID列表
 * @returns {Promise}
 */
export function batchDeleteTags(tagIds) {
  return request.post('/tags/batch-delete', {
    ids: tagIds
  })
}

/**
 * 搜索文件
 * @param {Object} params - 搜索参数
 * @param {Array<string>} params.keywords - 关键词列表
 * @param {Array<string>} params.tags - 标签列表
 * @returns {Promise}
 */
export function searchFiles(params) {
  return request.post('/search', params)
}

/**
 * 下载文件（通过文件ID）
 * @param {number} fileId - 文件ID
 * @param {string} filename - 文件名
 * @returns {Promise}
 */
export function downloadFile(fileId, filename) {
  // 确保 fileId 是数字类型
  const fileIdNum = parseInt(fileId, 10)
  if (isNaN(fileIdNum)) {
    return Promise.reject(new Error('无效的文件ID'))
  }
  
  // 使用统一的 BaseURL，调用 GET /download/{file_id} 接口
  const url = `/download/${fileIdNum}`
  const fullUrl = `${request.defaults.baseURL}${url}`
  console.log('下载请求:', { 
    fileId: fileIdNum, 
    filename, 
    url, 
    fullUrl,
    baseURL: request.defaults.baseURL 
  })
  
  return request.get(url, {
    responseType: 'blob',
    // 接受所有状态码，让错误拦截器处理
    validateStatus: function (status) {
      return true  // 接受所有状态码，包括错误状态码
    }
  }).then(response => {
    // 检查响应状态码
    if (response.status >= 200 && response.status < 300) {
      // 成功响应
      console.log('下载响应成功:', { 
        status: response.status, 
        headers: response.headers,
        dataType: response.data?.constructor?.name,
        dataSize: response.data?.size
      })
      return handleDownloadResponse(response, filename)
    } else {
      // 错误状态码，需要解析错误信息
      console.error('下载响应错误状态码:', response.status)
      // 对于 blob 类型的错误响应，需要读取 blob 内容
      if (response.data instanceof Blob) {
        return new Promise((resolve, reject) => {
          const reader = new FileReader()
          reader.onload = () => {
            try {
              const text = reader.result
              const errorData = JSON.parse(text)
              const error = new Error(errorData.detail || '下载失败')
              error.response = response
              reject(error)
            } catch (e) {
              reject(new Error(`下载失败: HTTP ${response.status}`))
            }
          }
          reader.onerror = () => reject(new Error(`下载失败: HTTP ${response.status}`))
          reader.readAsText(response.data)
        })
      } else {
        return Promise.reject(new Error(`下载失败: HTTP ${response.status}`))
      }
    }
  }).catch(error => {
    console.error('下载错误详情:', {
      message: error.message,
      code: error.code,
      response: error.response ? {
        status: error.response.status,
        statusText: error.response.statusText,
        headers: error.response.headers,
        dataType: error.response.data?.constructor?.name
      } : null,
      config: {
        url: error.config?.url,
        method: error.config?.method,
        baseURL: error.config?.baseURL,
        responseType: error.config?.responseType
      },
      request: error.request
    })
    throw error
  })
}

/**
 * 处理下载响应，创建下载链接
 * @param {Object} response - axios 响应对象
 * @param {string} filename - 默认文件名
 * @returns {Promise}
 */
function handleDownloadResponse(response, filename) {
  return new Promise((resolve, reject) => {
    try {
      // 对于 blob 响应，response 是完整的 axios 响应对象
      // response.data 才是 blob 数据
      const blob = response.data
      
      // 验证 blob 数据是否有效
      if (!blob || !(blob instanceof Blob)) {
        throw new Error('无效的文件数据')
      }
      
      // 策略：从自定义响应头 X-Original-Filename 中读取真实的中文文件名
      let downloadFilename = filename
      
      // 优先尝试获取 X-Original-Filename（不区分大小写）
      const xFilename = response.headers['x-original-filename'] || 
                        response.headers['X-Original-Filename'] ||
                        response.headers['X-ORIGINAL-FILENAME']
      
      if (xFilename) {
        // 使用 decodeURIComponent() 将其还原为中文
        try {
          downloadFilename = decodeURIComponent(xFilename)
        } catch (e) {
          console.warn('解码 X-Original-Filename 失败，使用默认文件名:', e)
        }
      }
      
      // 如果获取失败，退而求其次使用传参进来的 filename（已在上面设置）
      
      // 创建下载链接
      const url = window.URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.setAttribute('download', downloadFilename)
      document.body.appendChild(link)
      link.click()
      document.body.removeChild(link)
      window.URL.revokeObjectURL(url)
      resolve(response)
    } catch (error) {
      reject(error)
    }
  })
}

/**
 * 预览文件（图片和PDF）
 * @param {number} fileId - 文件ID
 * @returns {string} 预览URL
 */
export function getPreviewUrl(fileId) {
  // 使用统一的 BaseURL，返回完整URL
  return `http://127.0.0.1:8001/files/${fileId}/preview`
}

/**
 * 在服务器所在机器的文件管理器中定位（选中）文件
 * 注意：这是在后端服务器机器上执行，不是浏览器本机能力。
 * @param {number} fileId - 文件ID
 * @returns {Promise}
 */
export function revealFile(fileId) {
  const fileIdNum = parseInt(fileId, 10)
  if (isNaN(fileIdNum)) {
    return Promise.reject(new Error('无效的文件ID'))
  }
  return request.post(`/files/${fileIdNum}/reveal`)
}

/**
 * 在服务器所在机器上用默认程序打开文件
 * @param {number} fileId - 文件ID
 * @returns {Promise}
 */
export function openFile(fileId) {
  const fileIdNum = parseInt(fileId, 10)
  if (isNaN(fileIdNum)) {
    return Promise.reject(new Error('无效的文件ID'))
  }
  return request.post(`/files/${fileIdNum}/open`)
}

/**
 * 删除文件（通过文件ID）
 * @param {number} fileId - 文件ID
 * @returns {Promise}
 */
export function deleteFile(fileId) {
  // 确保 fileId 是数字类型
  const fileIdNum = parseInt(fileId, 10)
  if (isNaN(fileIdNum)) {
    return Promise.reject(new Error('无效的文件ID'))
  }
  
  // 使用统一的 BaseURL，调用 DELETE /files/{file_id} 接口
  return request.delete(`/files/${fileIdNum}`)
}

/**
 * 批量下载文件（打包成ZIP）
 * @param {Array<number>} fileIds - 文件ID列表
 * @returns {Promise}
 */
export function batchDownload(fileIds) {
  return request.post('/files/batch-download', {
    file_ids: fileIds
  }, {
    responseType: 'blob'
  }).then(response => {
    // 对于 blob 响应，response 是完整的 axios 响应对象
    // response.data 才是 blob 数据
    const blob = response.data
    
    // 验证 blob 数据是否有效
    if (!blob || !(blob instanceof Blob)) {
      throw new Error('无效的文件数据')
    }
    
    // 创建下载链接
    const url = window.URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', 'files.zip')
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    window.URL.revokeObjectURL(url)
    return response
  })
}

/**
 * 更新文件标签
 * @param {number} fileId - 文件ID
 * @param {Array<string>} tags - 标签列表
 * @returns {Promise}
 */
export function updateFileTags(fileId, tags) {
  // 确保 fileId 是数字类型
  const fileIdNum = parseInt(fileId, 10)
  if (isNaN(fileIdNum)) {
    return Promise.reject(new Error('无效的文件ID'))
  }
  
  return request.put(`/files/${fileIdNum}/tags`, {
    tags: tags
  })
}

/**
 * 批量更新文件标签
 * @param {Array<number>} fileIds - 文件ID列表
 * @param {Array<string>} tags - 标签列表
 * @returns {Promise}
 */
export function batchUpdateTags(fileIds, tags) {
  const formData = new FormData()
  fileIds.forEach(id => formData.append('file_ids', id))
  tags.forEach(tag => formData.append('tags', tag))
  
  return request.post('/files/batch-update-tags', formData, {
    headers: {
      'Content-Type': 'multipart/form-data'
    }
  })
}
