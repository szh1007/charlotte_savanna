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

/** 旅程输入类型: 纯文本 / 文件 / 网页链接 / 知识库 (PRD B-1, Issue 11). */
export type JourneyInputType = 'text' | 'file' | 'link' | 'kb'
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
  /** kb 旅程 (Issue 11): 知识库 id (重试时透传管道); 知识库被删后为 null. */
  kb_id: number | null
  knowledge_base: Topic | null
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

/** 关卡类型: 常规 / Boss (章节末尾高难度混合题型关, G-5). */
export type LevelType = 'regular' | 'boss'

/** 题目生成状态 (Issue 08 渐进生成): 待生成/生成中/已就绪/生成失败. */
export type QuestionsStatus = 'pending' | 'generating' | 'ready' | 'failed'

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
  seq: number
  level_type: LevelType
  kp_id: number | null
  kp_title: string
  chapter_title: string
  question_count: number
  questions_status: QuestionsStatus
  latest_task_id: string
  locked: boolean
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
  seq: number
  level_type: LevelType
  kp_id: number | null
  kp_title: string
  chapter_id: number
  chapter_title: string
  question_count: number
  questions_status: QuestionsStatus
  latest_task_id: string
  locked: boolean
  hearts: number
  current_index: number
  cleared: boolean
  status: LevelStatus
  /** 当前题; 通关/心扣完/已答完/题目未就绪时为 null (前端按状态分流). */
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

/** 关卡列表 (首次访问自动生成关卡骨架, 题目渐进生成, 后端幂等). */
export function getLevels(journeyId: number): Promise<{ levels: LevelSummary[] }> {
  return request(`/api/charplot/journeys/${journeyId}/levels/`)
}

/** 关卡详情 + 当前题 (断点续答定位源). */
export function getLevel(levelId: number): Promise<LevelDetail> {
  return request(`/api/charplot/levels/${levelId}/`)
}

/**
 * 触发出题生成任务 (Issue 08, POST /ai/levels/generate).
 * 渐进生成: 进入关卡/预生成下一关时调用; 幂等由后端 claim 保证
 * (已就绪或已有任务在跑时任务直接 done 跳过).
 */
export function startLevelGeneration(
  journeyId: number,
  levelSeq: number,
): Promise<{ task_id: string }> {
  return request(`/ai/levels/generate`, {
    method: 'POST',
    body: { journey_id: journeyId, level_seq: levelSeq },
  })
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

/** 重开关卡 (5 心扣完): 心与进度重置, 题目保持, Attempt 历史保留. */
export function restartLevel(levelId: number): Promise<LevelDetail> {
  return request(`/api/charplot/levels/${levelId}/restart/`, { method: 'POST' })
}

// ---- 复盘报告 (Issue 06) ----

/** 每关答题表现 (报告统计 levels 项). */
export interface LevelReportStat {
  level_id: number
  kp_id: number
  kp_title: string
  chapter_title: string
  answered: number
  correct: number
}

/** 答题统计快照 (与 Attempt 聚合一致, SPEC §8). */
export interface ReviewStats {
  answered: number
  correct: number
  wrong: number
  /** 整数百分比正确率. */
  accuracy: number
  /** 总耗时秒. */
  duration: number
  levels: LevelReportStat[]
}

/** 知识总结: 章节 → 知识点 (图谱确定性聚合, LLM 总结为 Issue 13). */
export interface ReviewKnowledgeSummary {
  chapters: {
    title: string
    summary: string
    knowledge_points: { title: string; summary: string }[]
  }[]
}

/** 复盘报告 (GET /api/charplot/journeys/{id}/report/). */
export interface ReviewReport {
  id: number
  journey_id: number
  slug: string
  knowledge_summary: ReviewKnowledgeSummary
  stats: ReviewStats
  og_title: string
  og_description: string
  og_image: string
  /** 公开分享链接 (相对路径, 复制时拼 location.origin). */
  share_url: string
  created_at: string
}

/** 复盘报告 (仅通关后存在; 未通关 404). */
export function getReviewReport(journeyId: number): Promise<ReviewReport> {
  return request(`/api/charplot/journeys/${journeyId}/report/`)
}

// ---- 知识库管理 (Issue 09, PRD C-1~C-4) ----

/** 知识库状态: 草稿 / 索引中 / 已就绪 / 索引失败 / 已下线 (SPEC §6.1). */
export type KbStatus = 'draft' | 'indexing' | 'ready' | 'failed' | 'offline'

/** 知识库文档 (软删标记 is_deleted, 管理页回收区展示). */
export interface KbDocument {
  id: number
  knowledge_base_id: number
  title: string
  filename: string
  file_size: number
  is_deleted: boolean
  deleted_at: string | null
  created_at: string
}

/** 知识库列表项 (管理员全部状态 / 普通用户仅就绪). */
export interface KnowledgeBaseSummary {
  id: number
  name: string
  description: string
  cover: string
  status: KbStatus
  collection_name: string
  latest_task_id: string
  error_message: string
  document_count: number
  created_at: string
  updated_at: string
}

/** 知识库详情 (管理页): documents 有效 / deleted_documents 软删分组. */
export interface KnowledgeBaseDetail extends KnowledgeBaseSummary {
  documents: KbDocument[]
  deleted_documents: KbDocument[]
}

/** 主题卡片 (用户端, GET /api/charplot/topics/: 仅就绪知识库). */
export interface Topic {
  id: number
  name: string
  description: string
  cover: string
}

/** 知识库列表 (GET /api/charplot/kb/: 双语义, 管理员含全部状态). */
export function getKnowledgeBases(): Promise<{ kbs: KnowledgeBaseSummary[] }> {
  return request('/api/charplot/kb/')
}

/** 知识库详情 + 文档分组 (管理页). */
export function getKnowledgeBase(id: number): Promise<KnowledgeBaseDetail> {
  return request(`/api/charplot/kb/${id}/`)
}

/** 创建知识库 (is_staff): {name, description, cover} → 201 KB. */
export function createKnowledgeBase(data: {
  name: string
  description?: string
  cover?: string
}): Promise<KnowledgeBaseSummary> {
  return request('/api/charplot/kb/', { method: 'POST', body: data })
}

/** 上传文档 (is_staff, multipart 多文件字段 files, all-or-nothing). */
export function uploadKbDocuments(
  kbId: number,
  files: File[],
): Promise<{ documents: KbDocument[] }> {
  const form = new FormData()
  for (const f of files) form.append('files', f)
  return requestForm(`/api/charplot/kb/${kbId}/documents/`, form)
}

/** 软删文档 (可恢复, 磁盘文件保留). */
export function deleteKbDocument(docId: number): Promise<void> {
  return request(`/api/charplot/kb/documents/${docId}/`, { method: 'DELETE' })
}

/** 恢复软删文档. */
export function restoreKbDocument(docId: number): Promise<KbDocument> {
  return request(`/api/charplot/kb/documents/${docId}/restore/`, {
    method: 'POST',
  })
}

/** 下线知识库 (仅就绪可下线, 用户端不可见). */
export function setKbOffline(kbId: number): Promise<KnowledgeBaseSummary> {
  return request(`/api/charplot/kb/${kbId}/offline/`, { method: 'POST' })
}

/** 恢复上线 (仅下线状态). */
export function setKbOnline(kbId: number): Promise<KnowledgeBaseSummary> {
  return request(`/api/charplot/kb/${kbId}/online/`, { method: 'POST' })
}

/** 主题卡片 (就绪知识库, 游客可浏览). */
export function getTopics(): Promise<{ topics: Topic[] }> {
  return request('/api/charplot/topics/')
}

/**
 * 触发索引任务 (POST /ai/kb/index, 全量重建 stub).
 * 幂等由后端 claim 保证 (索引中/下线/无文档 → 任务直接 done 跳过).
 */
export function startKbIndex(kbId: number): Promise<{ task_id: string }> {
  return request('/ai/kb/index', { method: 'POST', body: { kb_id: kbId } })
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

/**
 * 创建旅程: file → multipart, text/link/kb → JSON; 返回 {journey_id, status}.
 * kb 类型 (Issue 11): 必带 kb_id, 后端仅允许就绪知识库开旅程.
 */
export function createJourney(input: {
  input_type: JourneyInputType
  content?: string
  file?: File
  kb_id?: number
}): Promise<{ journey_id: number; status: JourneyStatus }> {
  if (input.input_type === 'file' && input.file) {
    const form = new FormData()
    form.append('input_type', 'file')
    form.append('source_file', input.file)
    return requestForm('/api/charplot/journeys/', form)
  }
  return request('/api/charplot/journeys/', {
    method: 'POST',
    body: {
      input_type: input.input_type,
      content: input.content,
      kb_id: input.kb_id,
    },
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

/** 启动知识管道 (FastAPI), 返回 {task_id}; kb 类型 (Issue 11) 透传 kb_id. */
export function startPipeline(
  journeyId: number,
  inputType: JourneyInputType,
  content?: string,
  kbId?: number,
): Promise<{ task_id: string }> {
  return request('/ai/pipeline', {
    method: 'POST',
    body: { journey_id: journeyId, input_type: inputType, content, kb_id: kbId },
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

// ---- 分析 Dashboard (Issue 12, SPEC §10) ----

/** 知识点掌握度 (Attempt 事实聚合, 复习题归属来源知识点). */
export interface MasteryPoint {
  kp_id: number
  title: string
  order: number
  answered: number
  correct: number
  /** 整数百分比正确率. */
  accuracy: number
  duration: number
  error_score: number
  /** 正确率 < 60% 薄弱点, 前端高亮. */
  weak: boolean
}

/** 章节掌握度 (章内知识点 Attempt 汇总). */
export interface MasteryChapter {
  chapter_id: number
  title: string
  order: number
  answered: number
  correct: number
  accuracy: number
  duration: number
  knowledge_points: MasteryPoint[]
}

/** 旅程掌握度分组 (仅含答题记录的知识点). */
export interface MasteryJourney {
  journey_id: number
  title: string
  cleared: boolean
  chapters: MasteryChapter[]
}

/** 近 N 天每日学习活跃 (ANSWER/LEVEL_CLEAR 事件按日聚合, 无学习天 active=false). */
export interface DailyActivity {
  date: string
  answers: number
  cleared: number
  active: boolean
}

/** 学习活动统计 (时长/通关数/活跃天数/连胜, 与事实表一致). */
export interface ActivityStats {
  duration_seconds: number
  cleared_levels: number
  active_days: number
  streak: number
  max_streak: number
  daily: DailyActivity[]
}

/** 易错点 (优先级公式与间隔复习同源, 全局聚合). */
export interface Weakpoint {
  kp_id: number
  title: string
  journey_id: number
  journey_title: string
  chapter_title: string
  error_score: number
  days_since_review: number
  priority: number
  priority_level: 'high' | 'medium' | 'low'
  wrong_count: number
}

/** 掌握度矩阵 (GET /api/charplot/dashboard/mastery/). */
export function getMasteryMatrix(): Promise<{ journeys: MasteryJourney[] }> {
  return request('/api/charplot/dashboard/mastery/')
}

/** 学习活动统计 (GET /api/charplot/dashboard/activity/). */
export function getActivityStats(): Promise<ActivityStats> {
  return request('/api/charplot/dashboard/activity/')
}

/** 易错点清单 (GET /api/charplot/dashboard/weakpoints/). */
export function getWeakpoints(): Promise<{ weakpoints: Weakpoint[] }> {
  return request('/api/charplot/dashboard/weakpoints/')
}

/**
 * LLM 状态总结 (POST /ai/report/summary, Issue 13, DESIGN.md §4.2).
 * 聚合在 Django 侧权威获取, FastAPI 只做 LLM 生成 (同步, 可重复生成).
 * 返回 markdown 报告 (强项 / 弱项 / 学习建议 三段), 由 MarkdownText 渲染.
 */
export function generateStatusSummary(
  userId: number,
): Promise<{ summary: string }> {
  return request('/ai/report/summary', {
    method: 'POST',
    body: { user_id: userId },
  })
}
