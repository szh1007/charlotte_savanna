// 双后端请求封装: Django 业务侧 /api, FastAPI AI 侧 /ai (DESIGN.md §4).
// Vite dev server 已配置代理, 同源请求即可.

/** 健康检查响应 (Django / FastAPI 双端同构). */
export interface HealthStatus {
  status: 'ok' | 'degraded'
  service: string
  db?: 'ok' | 'error'
  redis?: 'ok' | 'error'
  time: string
}

/** 带超时的 fetch, 超时视为服务不可达. */
async function fetchWithTimeout(url: string, ms = 4000): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  try {
    return await fetch(url, { signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

/** 探活 Django 业务侧. */
export async function checkDjangoHealth(): Promise<HealthStatus> {
  const res = await fetchWithTimeout('/api/charplot/health')
  return (await res.json()) as HealthStatus
}

/** 探活 FastAPI AI 能力侧. */
export async function checkAiHealth(): Promise<HealthStatus> {
  const res = await fetchWithTimeout('/ai/health')
  return (await res.json()) as HealthStatus
}

// TODO(Issue 03): SSE 客户端 (EventSource) 在此实现, 消费 /ai/tasks/{id}/events 进度流.
