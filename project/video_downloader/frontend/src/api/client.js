// 后端 API 客户端: fetch 封装, 统一错误解析
// 开发期经 Vite 代理同源访问 (vite.config.js), 生产由反向代理处理
const BASE = '/api'

/**
 * 通用请求: 非 2xx 时抛出带后端 detail 的 Error, 网络失败给出明确提示.
 * @param {string} path 以 / 开头的 API 路径
 * @param {object} [options] fetch 选项 (method / body / headers 等)
 * @returns {Promise<any>} 解析后的 JSON 响应体
 */
async function request(path, options = {}) {
  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
      ...options,
    })
  } catch {
    throw new Error('无法连接服务器, 请确认后端服务已启动')
  }

  if (!res.ok) {
    // 后端错误统一为 {detail: "..."} (FastAPI HTTPException)
    let detail = `请求失败 (${res.status})`
    try {
      const body = await res.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* 响应体非 JSON 时保留默认提示 */
    }
    throw new Error(detail)
  }
  return res.json()
}

/** 解析视频链接元信息 (标题 / 封面 / 时长 / 站点 / 清晰度档位). */
export function resolveUrl(url) {
  return request('/resolve', { method: 'POST', body: JSON.stringify({ url }) })
}

/** 查询支持平台列表. */
export function fetchSites() {
  return request('/sites')
}

/** 查询当前会话会员状态 (T09 接入会员入口后使用). */
export function fetchMemberStatus() {
  return request('/member/status')
}
