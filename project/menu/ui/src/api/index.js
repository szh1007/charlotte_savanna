import axios from 'axios'

// 创建axios实例
const api = axios.create({
  baseURL: '/api',  // 使用代理路径
  // timeout: 10000,  // 移除超时限制
  headers: {
    'Content-Type': 'application/json',
    'Cache-Control': 'no-cache',
    Pragma: 'no-cache'
  }
})

// 请求拦截器
api.interceptors.request.use(
  config => {
    if ((config.method || '').toLowerCase() === 'get') {
      config.params = {
        ...(config.params || {}),
        _t: Date.now()
      }
    }
    console.log('API请求:', config.method.toUpperCase(), config.url, config.data)
    return config
  },
  error => {
    console.error('请求错误:', error)
    return Promise.reject(error)
  }
)

// 响应拦截器
api.interceptors.response.use(
  response => {
    console.log('API响应:', response.status, response.data)
    return response.data
  },
  error => {
    console.error('响应错误:', error.response?.status, error.response?.data)
    if (error.response?.status === 500) {
      throw new Error('处理中遇到问题，请稍后再试')
    } else if (error.response?.status === 404) {
      throw new Error('请求的资源不存在')
    } else {
      // 移除超时和服务器不可用的通用提醒
      throw new Error(error.response?.data?.detail || '请求处理中，请稍候...')
    }
  }
)

// 智能对话API
export const chatAPI = {
  // 发送聊天消息
  sendMessage: async (query, threadId) => {
    return await api.post('/chat', { query, thread_id: threadId })
  },
  // 发送流式聊天消息
  sendStreamMessage: async (query, threadId, onMessage, onError, onDone) => {
    try {
      const response = await fetch('/api/chat', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ query, thread_id: threadId })
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        // Keep the last partial line in the buffer
        buffer = lines.pop() || '';

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            const data = line.slice(6);
            if (data.trim()) {
              try {
                const parsed = JSON.parse(data);
                onMessage(parsed);
              } catch (e) {
                console.error('Parse error', e);
              }
            }
          }
        }
      }
      if (onDone) onDone();
    } catch (error) {
      if (onError) onError(error);
    }
  }
}

// 菜品API
export const menuAPI = {
  // 获取菜品列表
  getMenuList: async () => {
    return await api.get('/menu/list')
  }
}

// 固定问题/FAQ 推荐
export const faqAPI = {
  // POST /faq/suggest, 请求体 { query, thread_id }, 返回 { success, query, suggestions: [{ question, answer }] }
  suggest: async (query, threadId) => {
    return await api.post('/faq/suggest', {
      query,
      thread_id: threadId
    })
  }
}

export const reservationAPI = {
  list: async (limit = 10) => {
    return await api.get('/reservation/list', {
      params: { limit }
    })
  }
}

// 健康检查API
export const healthAPI = {
  // 检查服务状态
  checkHealth: async () => {
    return await api.get('/health')
  }
}

export default api
