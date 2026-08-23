// 双后端请求封装: Django 业务侧 /api, FastAPI AI 侧 /ai (DESIGN.md §4).
// Vite dev server 已配置代理, 同源请求即可.
// Issue 02: 新增 request<T>() 统一封装 (CSRF token 注入 + 错误归一) 与认证 API.

/** 健康检查响应 (Django / FastAPI 双端同构). */
export interface HealthStatus {
  status: 'ok' | 'degraded'
  service: string
  db?: 'ok' | 'error'
  redis?: 'ok' | 'error'
  time: string
}

/** 会话用户 (与后端 auth/session 及登录响应同构). */
export interface SessionUser {
  id: number
  username: string
  email: string
  is_staff: boolean
}

/** 会话探测响应. */
export interface SessionStatus {
  authenticated: boolean
  user: SessionUser | null
}

/** 统计面板 (SPEC §8, Issue 05 后答题类字段自然流入). */
export interface ProfileStats {
  login_days: number
  answered: number
  correct: number
  wrong: number
  cleared_levels: number
}

/** 连胜中断警告. */
export interface StreakLossWarning {
  warning: boolean
  missed_days: number
  freeze_until: string | null
}

/** 个人主页载荷 (GET /api/charplot/profile). */
export interface Profile {
  id: number
  username: string
  email: string
  is_staff: boolean
  xp: number
  level: number
  streak: number
  max_streak: number
  hearts: number
  coins: number
  last_study_date: string | null
  freeze_until: string | null
  stats: ProfileStats
  streak_loss_warning: StreakLossWarning
  created_at: string
  updated_at: string
}

/** 带 status 与原始载荷的请求错误 (DRF 字段级错误可逐字段映射). */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly payload: unknown,
  ) {
    super(message)
    this.name = 'ApiError'
  }
}

/** 从 DRF 错误载荷提取人话: detail > non_field_errors > 首个字段错误. */
function extractDetail(payload: unknown, status: number): string {
  if (payload && typeof payload === 'object') {
    const p = payload as Record<string, unknown>
    if (typeof p.detail === 'string') return p.detail
    for (const key of ['non_field_errors', 'username', 'email', 'password']) {
      const v = p[key]
      if (Array.isArray(v) && typeof v[0] === 'string') return v[0]
    }
  }
  return `请求失败 (${status})`
}

/** 读取 cookie (CSRF token 存于 csrftoken, 登录后 Django 轮换, 每次现读). */
function getCookie(name: string): string | null {
  const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'))
  return match && match[1] ? decodeURIComponent(match[1]) : null
}

/** 带超时的 fetch, 超时视为服务不可达. */
async function fetchWithTimeout(
  url: string,
  init?: RequestInit,
  ms = 8000,
): Promise<Response> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), ms)
  try {
    return await fetch(url, { ...init, signal: controller.signal })
  } finally {
    clearTimeout(timer)
  }
}

/** 统一请求封装: JSON 收发 + CSRF token 注入 + 错误归一. */
export async function request<T>(
  path: string,
  opts: { method?: string; body?: unknown } = {},
): Promise<T> {
  const method = opts.method ?? 'GET'
  const headers: Record<string, string> = {}
  if (method !== 'GET' && method !== 'HEAD') {
    // Django CSRF middleware 校验 X-CSRFToken 与 csrftoken cookie 匹配
    const token = getCookie('csrftoken')
    if (token) headers['X-CSRFToken'] = token
  }
  if (opts.body !== undefined) {
    // DRF 按 Content-Type 选择解析器, 缺省时 JSON body 会 415
    headers['Content-Type'] = 'application/json'
  }
  const res = await fetchWithTimeout(path, {
    method,
    headers,
    body: opts.body === undefined ? undefined : JSON.stringify(opts.body),
  })
  if (!res.ok) {
    // middleware 403 是 HTML 响应, json() 需容错
    let payload: unknown = null
    try {
      payload = await res.json()
    } catch {
      /* 非 JSON (HTML 错误页), 用状态码兜底 */
    }
    throw new ApiError(extractDetail(payload, res.status), res.status, payload)
  }
  if (res.status === 204) return undefined as T
  return (await res.json()) as T
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

// ---- 账号体系 (Issue 02) ----

/** 会话探测 + CSRF cookie 引导 (SPA 启动必调). */
export function getSession(): Promise<SessionStatus> {
  return request<SessionStatus>('/api/charplot/auth/session/')
}

/** 注册 (201 → 用户信息). */
export function register(data: {
  username: string
  email: string
  password: string
}): Promise<{ id: number; username: string; email: string }> {
  return request('/api/charplot/auth/register/', { method: 'POST', body: data })
}

/** 登录 (Django session 建立, 返回用户信息). */
export function login(data: {
  username: string
  password: string
}): Promise<SessionUser> {
  return request<SessionUser>('/api/charplot/auth/login/', {
    method: 'POST',
    body: data,
  })
}

/** 登出. */
export function logout(): Promise<void> {
  return request<void>('/api/charplot/auth/logout/', { method: 'POST' })
}

/** 个人主页. */
export function getProfile(): Promise<Profile> {
  return request<Profile>('/api/charplot/profile/')
}

/** 学习币兑换连胜冻结 → 剩余币 + 冻结截止日. */
export function buyStreakFreeze(): Promise<{ coins: number; frozen: string }> {
  return request('/api/charplot/profile/streak-freeze/', { method: 'POST' })
}

// ---- 旅程与生成链路 (Issue 03) ----

/** 旅程输入类型: 纯文本 / 文件 / 网页链接 (PRD B-1). */
export type JourneyInputType = 'text' | 'file' | 'link'
/** 旅程状态: 生成中 / 已就绪 / 生成失败. */
export type JourneyStatus = 'generating' | 'ready' | 'failed'
/** 管道任务状态 (CONTRACT.md §2). */
export type TaskRunStatus = 'running' | 'done' | 'error'

/** 旅程列表项 (GET /api/charplot/journeys). */
export interface JourneySummary {
  id: number
  title: string
  input_type: JourneyInputType
  status: JourneyStatus
  cleared: boolean
  chapter_count: number
  kp_count: number
  created_at: string
}

/** 知识点 (prerequisites 为 DB 主键 int 列表, 前端本地映射标题). */
export interface KnowledgePoint {
  id: number
  title: string
  summary: string
  order: number
  error_score: number
  prerequisites: number[]
}

export interface Chapter {
  id: number
  title: string
  summary: string
  order: number
  knowledge_points: KnowledgePoint[]
}

/** 旅程详情 (GET /api/charplot/journeys/{id}). */
export interface JourneyDetail {
  id: number
  title: string
  input_type: JourneyInputType
  content: string
  status: JourneyStatus
  cleared: boolean
  latest_task_id: string
  error_message: string
  created_at: string
  updated_at: string
  chapters: Chapter[]
}

// ---- 技能树 (Issue 04) ----

/** 技能树节点状态: 锁定 / 可解锁 / 进行中(Issue 05) / 已通关点亮. */
export type SkillNodeStatus = 'locked' | 'unlocked' | 'in_progress' | 'cleared'

/** 技能树节点 (GET /api/charplot/journeys/{id}/skill-tree/). */
export interface SkillTreeNode {
  id: number
  chapter_id: number
  chapter_title: string
  title: string
  order: number
  status: SkillNodeStatus
  /** 已通关关卡数 (Issue 05 流入; 大知识点多关时节点徽章显示 cleared/total). */
  cleared_levels: number
  total_levels: number
}

/** 前置依赖边 (source → target, DAG). */
export interface SkillTreeEdge {
  id: string
  source: number
  target: number
}

/** 技能树图数据: 节点 + 依赖边, 闯关地图渲染源 (PRD D-1). */
export interface SkillTreeData {
  nodes: SkillTreeNode[]
  edges: SkillTreeEdge[]
}

// ---- 闯关答题 (Issue 05) ----

/** 题型: 选择 / 判断 / 填空 (PRD D-3). */
export type QuestionType = 'choice' | 'judge' | 'fill'

/** 关卡状态: 已通关 / 心扣完失败 / 进行中 / 未开始. */
export type LevelStatus = 'cleared' | 'failed' | 'in_progress' | 'pending'

/** 题目载荷 (不含标准答案, 判分在后端; options 仅选择类型使用). */
export interface Question {
  id: number
  question_type: QuestionType
  content: string
  options: string[]
  order: number
}

/** 关卡列表项 (GET /api/charplot/journeys/{id}/levels/). */
export interface LevelSummary {
  id: number
  kp_id: number
  kp_title: string
  chapter_title: string
  question_count: number
  hearts: number
  current_index: number
  cleared: boolean
  status: LevelStatus
}

/** 通关结算奖励 (POST answer 的 reward 字段, 通关时非 null). */
export interface LevelReward {
  xp: number
  coins: number
  streak: number
  max_streak: number
  level: number
  journey_cleared: boolean
}

/** 关卡详情 (GET /api/charplot/levels/{id}/): 进度/心 + 当前题. */
export interface LevelDetail {
  id: number
  kp_id: number
  kp_title: string
  chapter_id: number
  chapter_title: string
  question_count: number
  hearts: number
  current_index: number
  cleared: boolean
  status: LevelStatus
  /** 当前题; 通关/心扣完/已答完时为 null (前端渲染结算或重开视图). */
  question: Question | null
}

/** 提交答案结果 (POST /api/charplot/levels/{id}/answer/). */
export interface AnswerResult {
  correct: boolean
  explanation: string
  sources: string[]
  hearts: number
  level_status: LevelStatus
  cleared: boolean
  reward: LevelReward | null
  progress: { current_index: number; question_count: number }
}

/** 技能树图数据 (闯关地图页). */
export function getSkillTree(journeyId: number): Promise<SkillTreeData> {
  return request(`/api/charplot/journeys/${journeyId}/skill-tree/`)
}

/** 关卡列表 (首次访问自动生成 stub 关卡, 后端幂等). */
export function getLevels(journeyId: number): Promise<{ levels: LevelSummary[] }> {
  return request(`/api/charplot/journeys/${journeyId}/levels/`)
}

/** 关卡详情 + 当前题 (断点续答定位源). */
export function getLevel(levelId: number): Promise<LevelDetail> {
  return request(`/api/charplot/levels/${levelId}/`)
}

/** 提交答案: 判分 + 讲解/来源 + 心动值扣减 + 通关结算. */
export function answerQuestion(
  levelId: number,
  data: { question_id: number; answer: number[] | string[]; duration?: number },
): Promise<AnswerResult> {
  return request(`/api/charplot/levels/${levelId}/answer/`, {
    method: 'POST',
    body: data,
  })
}

/** 重开关卡 (5 心扣完): 心与进度重置, 题目不变, Attempt 历史保留. */
export function restartLevel(levelId: number): Promise<LevelDetail> {
  return request(`/api/charplot/levels/${levelId}/restart/`, { method: 'POST' })
}

/** SSE 管道进度事件 (CONTRACT.md §2). */
export interface PipelineEvent {
  task_id: string
  stage: string
  progress: number
  message: string
}

/** 任务状态 (GET /ai/tasks/{id}). */
export interface TaskStatus {
  task_id: string
  status: TaskRunStatus
  stage: string
  progress: number
  error_message: string | null
}

/**
 * 文件上传用: 与 request 同款 CSRF/错误归一, 但 body 为 FormData.
 * request 现有实现强制 JSON Content-Type, multipart 会 415, 必须走此路.
 */
async function requestForm<T>(path: string, form: FormData): Promise<T> {
  const token = getCookie('csrftoken')
  const headers: Record<string, string> = {}
  if (token) headers['X-CSRFToken'] = token
  const res = await fetchWithTimeout(path, { method: 'POST', headers, body: form })
  if (!res.ok) {
    let payload: unknown = null
    try {
      payload = await res.json()
    } catch {
      /* 非 JSON, 状态码兜底 */
    }
    throw new ApiError(extractDetail(payload, res.status), res.status, payload)
  }
  return (await res.json()) as T
}

/** 创建旅程: file → multipart, text/link → JSON; 返回 {journey_id, status}. */
export function createJourney(input: {
  input_type: JourneyInputType
  content?: string
  file?: File
}): Promise<{ journey_id: number; status: JourneyStatus }> {
  if (input.input_type === 'file' && input.file) {
    const form = new FormData()
    form.append('input_type', 'file')
    form.append('source_file', input.file)
    return requestForm('/api/charplot/journeys/', form)
  }
  return request('/api/charplot/journeys/', {
    method: 'POST',
    body: { input_type: input.input_type, content: input.content },
  })
}

/** 旅程列表 (进行中/已通关分组在前端渲染, CONTRACT.md §4). */
export function getJourneys(): Promise<{ journeys: JourneySummary[] }> {
  return request('/api/charplot/journeys/')
}

/** 旅程详情 + 图谱 (GET /api/charplot/journeys/{id}/). */
export function getJourney(id: number): Promise<JourneyDetail> {
  return request(`/api/charplot/journeys/${id}/`)
}

/** 启动知识管道 (FastAPI), 返回 {task_id}. */
export function startPipeline(
  journeyId: number,
  inputType: JourneyInputType,
  content?: string,
): Promise<{ task_id: string }> {
  return request('/ai/pipeline', {
    method: 'POST',
    body: { journey_id: journeyId, input_type: inputType, content },
  })
}

/** 任务状态 (GET /ai/tasks/{id}). */
export function getTaskStatus(taskId: string): Promise<TaskStatus> {
  return request(`/ai/tasks/${taskId}`)
}

/**
 * SSE 订阅管道进度 (CONTRACT.md §2). 返回 close 函数.
 * EventSource 断线自动重连并携带 Last-Event-ID (服务端增量续推);
 * 收到终端事件 (done/error) 或组件卸载时必须主动 close, 阻止无限重连.
 */
export function subscribePipeline(
  taskId: string,
  handlers: {
    onEvent: (ev: PipelineEvent) => void
    onStateChange?: (state: 'connecting' | 'reconnecting' | 'closed') => void
  },
): () => void {
  const source = new EventSource(`/ai/tasks/${taskId}/events`)
  let closed = false

  const close = () => {
    closed = true
    source.close()
  }

  source.addEventListener('pipeline-progress', (event) => {
    try {
      const data = JSON.parse((event as MessageEvent).data) as PipelineEvent
      handlers.onEvent(data)
      if (data.stage === 'done' || data.stage === 'error') close()
    } catch {
      /* 忽略无法解析的事件帧 */
    }
  })
  source.onopen = () => handlers.onStateChange?.('connecting')
  source.onerror = () => {
    // readyState: 0 CONNECTING (重连中) / 2 CLOSED (失败)
    handlers.onStateChange?.(source.readyState === 2 ? 'closed' : 'reconnecting')
  }
  return close
}
