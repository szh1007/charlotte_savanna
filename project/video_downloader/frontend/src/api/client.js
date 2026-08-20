// 后端 API 客户端: fetch 封装, 统一错误解析
// 开发期经 Vite 代理同源访问 (vite.config.js),
// 生产由反向代理处理
const BASE = '/api'

// 会员会话 token 存储键 (localStorage): 当前会话持久,
// 页面刷新后仍通过该 token 向后端恢复会员状态 (T09)
export const MEMBER_TOKEN_KEY = 'vd_member_token'

// 匿名客户端身份存储键 (localStorage): 免费档每日配额按此计数 (ADR-0005)
export const CLIENT_ID_KEY = 'vd_client_id'

// AI 总结字幕来源存储键 (localStorage): official 官方字幕 / model 模型生成
// (ADR-0006, 默认官方字幕; 未配置 B 站 Cookie 时官方字幕不可用)
export const SUBTITLE_SOURCE_KEY = 'vd_subtitle_source'

/** 读取匿名客户端 ID, 不存在则生成并持久化 (配额身份, 刷新后保持不变). */
export function getClientId() {
  let id = localStorage.getItem(CLIENT_ID_KEY)
  if (!id) {
    id = crypto.randomUUID
      ? crypto.randomUUID()
      : `c-${Date.now()}-${Math.random().toString(36).slice(2)}`
    localStorage.setItem(CLIENT_ID_KEY, id)
  }
  return id
}

/** 读取本地会员 token (无则返回 null). */
export function getMemberToken() {
  return localStorage.getItem(MEMBER_TOKEN_KEY)
}

/** 保存 / 清除本地会员 token. */
export function setMemberToken(token) {
  if (token) {
    localStorage.setItem(MEMBER_TOKEN_KEY, token)
  } else {
    localStorage.removeItem(MEMBER_TOKEN_KEY)
  }
}

/**
 * 通用请求: 非 2xx 时抛出带后端 detail 的 Error, 网络失败给出明确提示.
 * 自动附加 X-Member-Token header (有本地 token 时), 使解析/下载/状态
 * 等接口按会员身份计算能力 (T05 后端强制校验).
 * @param {string} path 以 / 开头的 API 路径
 * @param {object} [options] fetch 选项 (method / body / headers 等)
 * @returns {Promise<any>} 解析后的 JSON 响应体
 */
async function request(path, options = {}) {
  const token = getMemberToken()
  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      headers: {
        'Content-Type': 'application/json',
        // 免费档每日配额按匿名客户端身份计数 (ADR-0005); 其它接口忽略该头
        'X-Client-Id': getClientId(),
        ...(token ? { 'X-Member-Token': token } : {}),
        ...(options.headers || {}),
      },
      ...options,
    })
  } catch {
    throw new Error('无法连接服务器, 请确认后端服务已启动')
  }

  if (!res.ok) {
    // 后端错误统一为 {detail: "..."}
    // (FastAPI HTTPException 响应)
    let detail = `请求失败 (${res.status})`
    try {
      const body = await res.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* 响应体非 JSON 时保留默认提示 */
    }
    const err = new Error(detail)
    err.status = res.status // 调用方按状态码区分 (如清除记录 404 = 任务已不存在)
    throw err
  }
  if (res.status === 204) return null // 无响应体 (如 DELETE 成功)
  try {
    return await res.json()
  } catch {
    // 2xx 但响应体非 JSON (如代理返回 HTML),
    // 统一为可读错误而非原始 SyntaxError
    throw new Error('服务器返回了无法解析的响应')
  }
}

/** 解析视频链接元信息 (标题 / 封面 / 时长 / 站点 / 清晰度档位). */
export function resolveUrl(url) {
  return request('/resolve', { method: 'POST', body: JSON.stringify({ url }) })
}

/** 创建下载任务 (选定清晰度档位发起下载). */
export function createDownload(url, formatId) {
  return request('/downloads', {
    method: 'POST',
    body: JSON.stringify({ url, format_id: formatId }),
  })
}

/** 查询任务列表 (创建时间降序), 页面恢复时使用. */
export function fetchTasks() {
  return request('/tasks')
}

/** 清除单条任务记录: 删除视频文件 + 移除任务 (未过期时的二次确认由调用方负责). */
export function deleteTask(taskId) {
  return request(`/tasks/${taskId}`, { method: 'DELETE' })
}

/** 一键清除所有未完成记录 (排队中/下载中/失败/过期, 后端同时清理孤儿文件). */
export function purgeUnfinishedTasks() {
  return request('/tasks/purge-unfinished', { method: 'POST' })
}

/** 查询支持平台列表. */
export function fetchSites() {
  return request('/sites')
}

/** 查询当前会话会员状态 (刷新页面后恢复会员身份用). */
export function fetchMemberStatus() {
  return request('/member/status')
}

/** 提交会员密钥: 通过则返回会话 token (由调用方持久化到 localStorage). */
export function submitMemberKey(key) {
  return request('/member', { method: 'POST', body: JSON.stringify({ key }) })
}

// ---- AI 视频总结 (ADR-0005): 字幕/ASR 转录 + LLM 结构化总结 ----

/**
 * 创建总结任务 (kind=summary): 免费档超每日配额时后端 429 拒绝.
 * @param {string} url 视频链接
 * @param {'official'|'model'} subtitleSource 字幕来源 (ADR-0006):
 *   official 官方字幕 (快路径, 无字幕自动回退模型生成) / model 模型生成
 *   (缓存优先, 模型缺失自动触发下载)
 */
export function createSummarize(url, subtitleSource = 'official') {
  return request('/summarize', {
    method: 'POST',
    body: JSON.stringify({ url, subtitle_source: subtitleSource }),
  })
}

// ---- 语音转写模型 (ADR-0006): 全局状态 / 手动下载 ----

/** 查询语音转写模型状态: {status, progress, has_official_subtitle}. */
export function fetchModelStatus() {
  return request('/model/status')
}

/** 下载语音转写模型 (幂等): 已就绪/下载中不重复触发, 进度经 SSE model-update 广播. */
export function downloadModel() {
  return request('/model/download', { method: 'POST' })
}

/** 查询结构化总结 (概述 / 章节时间线 / 要点, 思维导图数据源). */
export function fetchSummary(taskId) {
  return request(`/tasks/${taskId}/summary`)
}

/** 查询带时间戳转录全文 (查看 / 复制 / 导出原料). */
export function fetchTranscript(taskId) {
  return request(`/tasks/${taskId}/transcript`)
}

/** 查询思维导图结构 (LLM 基于结构化总结生成, mindmap 依赖 summary). */
export function fetchMindmap(taskId) {
  return request(`/tasks/${taskId}/mindmap`)
}

/** 重试失败/阻塞的总结子任务 (不扣配额, 仅 failed/blocked 可重试). */
export function retrySubtask(taskId, subtask) {
  return request(`/tasks/${taskId}/retry`, {
    method: 'POST',
    body: JSON.stringify({ subtask }),
  })
}

/** 导出直链 (浏览器直接下载): md 总结 / txt|srt|vtt 转录. */
export function exportUrl(taskId, format) {
  return `${BASE}/tasks/${taskId}/export?format=${format}`
}

/**
 * SSE 流式读取 (总结流 / 问答流, ADR-0007): fetch 后逐帧解析并回调.
 *
 * 帧协议: `event: <name>\ndata: <单行 JSON>\n\n` (后端 json.dumps 保证
 * data 单行); 无 data 行的帧忽略 (协议扩展预留). 错误语义与 request()
 * 一致: 非 2xx 抛带 detail/status 的 Error; 网络失败抛明确提示;
 * AbortError 原样透传, 调用方区分「用户取消」与「断线」.
 * @param {string} path 以 / 开头的 API 路径
 * @param {object} [options] { method, body, signal, onFrame }
 * @param {(event: string, data: any) => void} options.onFrame 每帧回调
 * @returns {Promise<void>} 流正常结束 (done 帧后服务端关闭) 时 resolve
 */
async function readSseStream(path, { method = 'GET', body, signal, onFrame } = {}) {
  const token = getMemberToken()
  let res
  try {
    res = await fetch(`${BASE}${path}`, {
      method,
      signal,
      headers: {
        'Content-Type': 'application/json',
        // 免费档每日配额按匿名客户端身份计数 (ADR-0005)
        'X-Client-Id': getClientId(),
        ...(token ? { 'X-Member-Token': token } : {}),
      },
      body,
    })
  } catch (err) {
    if (err.name === 'AbortError') throw err // 用户取消, 透传
    throw new Error('无法连接服务器, 请确认后端服务已启动')
  }
  if (!res.ok) {
    let detail = `请求失败 (${res.status})`
    try {
      const body = await res.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* 响应体非 JSON 时保留默认提示 */
    }
    const err = new Error(detail)
    err.status = res.status // 调用方按状态码区分 (如 409 子任务未就绪)
    throw err
  }
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      // 帧以空行分隔; TextDecoder 解码后的帧边界不随网络 chunk 对齐
      let sep
      while ((sep = buffer.indexOf('\n\n')) !== -1) {
        const frame = buffer.slice(0, sep)
        buffer = buffer.slice(sep + 2)
        handleFrame(frame, onFrame)
      }
    }
    if (buffer.trim()) handleFrame(buffer, onFrame) // 断流残留的末帧
  } finally {
    reader.releaseLock()
  }
}

/** 解析单帧 (event/data 行), 回调命名事件与 JSON 数据. */
function handleFrame(frame, onFrame) {
  let event = 'message' // SSE 默认事件名
  let data = null
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) {
      event = line.slice('event: '.length)
    } else if (line.startsWith('data: ')) {
      data = JSON.parse(line.slice('data: '.length))
    }
  }
  if (data !== null) onFrame(event, data)
}

/** 订阅总结生成流 (SSE, ADR-0007): snapshot 首帧累积全文 / delta 增量 / done / error. */
export function streamSummary(taskId, { signal, onFrame }) {
  return readSseStream(`/tasks/${taskId}/summary/stream`, { signal, onFrame })
}

/** 订阅字幕重排流 (SSE, 帧协议同总结流): 模型生成字幕精修增量实时可见. */
export function streamTranscript(taskId, { signal, onFrame }) {
  return readSseStream(`/tasks/${taskId}/transcript/stream`, { signal, onFrame })
}

/** 针对视频内容提问, SSE 流式回答 (ADR-0007): delta 增量 → done 收尾; 超配额 429. */
export function askQuestionStream(taskId, question, { signal, onFrame }) {
  return readSseStream(`/tasks/${taskId}/qa`, {
    method: 'POST',
    body: JSON.stringify({ question }),
    signal,
    onFrame,
  })
}
