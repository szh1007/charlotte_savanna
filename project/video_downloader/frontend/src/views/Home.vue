<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from '../components/ConfirmDialog.vue'
import HeroSection from '../components/HeroSection.vue'
import MemberSection from '../components/MemberSection.vue'
import NavBar from '../components/NavBar.vue'
import ResolveResult from '../components/ResolveResult.vue'
import SiteFooter from '../components/SiteFooter.vue'
import SummaryDialog from '../components/SummaryDialog.vue'
import TaskPanel from '../components/TaskPanel.vue'
import { useMember } from '../composables/useMember.js'
import {
  createDownload,
  createSummarize,
  deleteTask,
  fetchTasks,
  purgeUnfinishedTasks,
  resolveUrl,
} from '../api/client.js'

// 单页布局 (PRD §10): 导航 → Hero → 解析结果 → 任务面板 → 页脚
// (平台墙已移除, 会员区改弹窗由导航栏触发)
const resolving = ref(false)
const result = ref(null)
const apiError = ref('')
const downloading = ref(false)
const downloadError = ref('')
const tasks = ref([])

// AI 总结 (ADR-0005): 创建中防重复点击, 免费档每日配额用尽 (429) 透传提示
const summarizing = ref(false)
const summarizeError = ref('')
// 总结视图弹窗: 打开的目标任务 (TaskPanel「查看总结」触发)
const summaryTask = ref(null)

// 最近一次解析的链接 (「开始下载」发起请求用; 会员解锁后自动重新解析)
const lastUrl = ref('')

// 会员状态 (T09): 全站共享, 解锁后 NavBar / 解析锁定档位 / 营销区联动
const {
  isMember,
  memberExpires,
  memberSubmitting,
  memberError,
  handleUnlock,
  restoreMember,
} = useMember({
  onUnlocked() {
    // 解锁后自动重新解析: 后端按会员身份返回无锁定档位
    // (🔒 标识消失, 会员专属档位可选); 失败时保留旧结果卡
    if (lastUrl.value) {
      handleResolve(lastUrl.value, { keepOld: true })
    }
  },
})

// 会员弹窗显隐: 导航栏会员入口 / 结果卡解锁引导 (US 35) 均打开弹窗
const memberDialogVisible = ref(false)

// SSE 连接: 任务进度实时推送 (无轮询)
let eventSource = null

// 任务列表防抖刷新: 创建失败 / SSE 未知任务时拉取真实列表补齐
// (失败任务已落库, 恢复与即时展示保持一致)
let refreshTimer = null

async function refreshTasks() {
  try {
    const { tasks: list } = await fetchTasks()
    // 合并式更新: 本地任务优先保留 (SSE 实时状态 + 解析结果完整标题),
    // 后端列表仅补充本地缺失的任务. 全量替换会把解析期间 title 为空的
    // 后端快照覆盖到本地卡片上, 标题丢失显示成「下载任务 #N」占位
    // (bugfix/0004)
    const merged = new Map(tasks.value.map((t) => [t.task_id, t]))
    for (const t of list) {
      // 面板展示下载 + 总结任务 (ADR-0005), 排除瞬时 resolve 短任务
      if (t.kind === 'resolve') continue
      const local = merged.get(t.task_id)
      merged.set(t.task_id, local ? { ...t, ...local } : t)
    }
    tasks.value = [...merged.values()]
  } catch {
    // 后端不可用时保留现有列表
  }
}

function scheduleRefresh() {
  clearTimeout(refreshTimer)
  refreshTimer = setTimeout(refreshTasks, 300)
}

async function handleResolve(url, { keepOld = false } = {}) {
  lastUrl.value = url
  resolving.value = true
  // 正常解析清掉旧结果卡; keepOld (解锁联动) 时保留旧卡,
  // 避免重解析失败 (如网络抖动) 后旧档位信息丢失
  if (!keepOld) result.value = null
  apiError.value = ''
  downloadError.value = ''
  try {
    result.value = await resolveUrl(url)
  } catch (e) {
    apiError.value = e.message
  } finally {
    resolving.value = false
  }
}

async function handleSummarize(url) {
  summarizing.value = true
  summarizeError.value = ''
  try {
    const { task_id } = await createSummarize(url)
    // 本地构造总结任务卡片 (元信息来自解析结果, 状态/进度由 SSE 覆盖;
    // 与下载任务同模式, 见 handleDownload 竞态说明)
    const meta = {
      title: result.value?.title ?? '',
      cover: result.value?.cover,
      duration: result.value?.duration,
      site: result.value?.site,
    }
    const idx = tasks.value.findIndex((t) => t.task_id === task_id)
    if (idx >= 0) {
      tasks.value[idx] = { ...tasks.value[idx], ...meta }
    } else {
      tasks.value.unshift({
        task_id,
        kind: 'summary',
        status: 'queued',
        ...meta,
        progress: 0,
        message: null,
        error: null,
      })
    }
  } catch (e) {
    // 免费档每日配额用尽等 (429 detail 透传, 提示明日再试)
    summarizeError.value = e.message
    // 创建失败时后端任务已落库 (failed), 补拉列表使其上卡
    scheduleRefresh()
  } finally {
    summarizing.value = false
  }
}

function handleViewSummary(task) {
  summaryTask.value = task
}

async function handleDownload({ url, formatId }) {
  downloading.value = true
  downloadError.value = ''
  try {
    const { task_id } = await createDownload(url, formatId)
    // 本地构造任务卡片立即展示完整元信息 (标题/封面来自解析结果,
    // 后续状态/进度由 SSE 事件覆盖). 竞态: 后端先发 resolving 事件后
    // 返回响应, SSE 事件先到时防抖刷新 (refreshTasks) 已把后端 resolving
    // 快照拉回列表 —— resolve 未完成时该快照 title/cover 为空, 若此处
    // 去重跳过, 卡片将长期显示占位标题且无封面 (bugfix/0006).
    // 同 id 卡已存在时补全缺失字段, 否则 unshift 完整卡
    const meta = {
      title: result.value?.title ?? '',
      cover: result.value?.cover,
      duration: result.value?.duration,
      site: result.value?.site,
      formats: result.value?.formats ?? [],
    }
    const idx = tasks.value.findIndex((t) => t.task_id === task_id)
    if (idx >= 0) {
      const t = tasks.value[idx]
      tasks.value[idx] = {
        ...t,
        format_id: formatId,
        title: t.title || meta.title,
        cover: t.cover || meta.cover,
        duration: t.duration ?? meta.duration,
        site: t.site || meta.site,
        formats: t.formats?.length ? t.formats : meta.formats,
      }
    } else {
      tasks.value.unshift({
        task_id,
        kind: 'download',
        status: 'queued',
        ...meta,
        format_id: formatId,
        progress: 0,
        message: null,
        error: null,
      })
    }
  } catch (e) {
    downloadError.value = e.message
    // 创建失败时后端任务已落库 (failed), 补拉列表使其上卡,
    // 与刷新后恢复的展示一致 (失败卡不会只在刷新后出现)
    scheduleRefresh()
  } finally {
    downloading.value = false
  }
}

// 导航栏会员入口 / 结果卡解锁引导 (US 35): 打开会员弹窗
// (未解锁态弹窗自动聚焦密钥输入框, 已解锁态查看权益详情)
function openMemberDialog() {
  memberDialogVisible.value = true
}

// SSE task-update: 合并状态字段到已有任务; 未知任务 (竞态先于
// POST 响应到达的 queued 事件等) 触发防抖刷新补齐, 避免状态滞后;
// removed 事件 (清除记录广播) 直接从列表移除卡片
function handleTaskUpdate(evt) {
  let data
  try {
    data = JSON.parse(evt.data)
  } catch {
    return
  }
  if (data.status === 'removed') {
    tasks.value = tasks.value.filter((t) => t.task_id !== data.task_id)
    return
  }
  const idx = tasks.value.findIndex((t) => t.task_id === data.task_id)
  if (idx >= 0) {
    tasks.value[idx] = { ...tasks.value[idx], ...data }
  } else {
    scheduleRefresh()
  }
}

// 自定义错误提示 (bugfix/0006): 替代浏览器原生 alert
const errorDialog = ref({ visible: false, title: '', message: '' })

function showError(title, message) {
  errorDialog.value = { visible: true, title, message }
}

// 清除单条记录 (TaskPanel 已做二次确认): 后端删文件 + 移除任务,
// 成功后本地移除卡片; 404 (任务已不存在) 同样移除, 保持两端一致
async function handleClearTask(task) {
  try {
    await deleteTask(task.task_id)
  } catch (e) {
    if (e.status !== 404) {
      showError('清除记录失败', e.message || '请稍后重试')
      return
    }
  }
  tasks.value = tasks.value.filter((t) => t.task_id !== task.task_id)
}

// 一键清除全部未完成记录: 后端取消/移除全部非已完成任务并返回 removed
// 列表, 本地按 id 精确过滤 (SSE removed 事件同源广播, 双保险)
async function handleClearUnfinished() {
  try {
    const { removed } = await purgeUnfinishedTasks()
    tasks.value = tasks.value.filter((t) => !removed.includes(t.task_id))
  } catch (e) {
    showError('清除未完成记录失败', e.message || '请稍后重试')
  }
}

function connectEvents() {
  // EventSource 断线自动重连, 重连后服务端先推快照恢复现场
  eventSource = new EventSource('/api/events')
  eventSource.addEventListener('task-update', handleTaskUpdate)
}

onMounted(async () => {
  // 先恢复任务列表 (含标题/封面等完整元信息), 再连 SSE:
  // 初始快照只更新状态字段, 不会覆盖列表详情
  try {
    const { tasks: list } = await fetchTasks()
    // 面板展示下载 + 总结任务, 排除瞬时 resolve 短任务 (与 refreshTasks 一致)
    tasks.value = list.filter((t) => t.kind !== 'resolve')
  } catch {
    // 后端不可用时页面主体仍可用, 任务列表留空即可
  }
  restoreMember()
  connectEvents()
})

onBeforeUnmount(() => {
  eventSource?.close()
})
</script>

<template>
  <NavBar :is-member="isMember" @go-member="openMemberDialog" />
  <HeroSection
    :resolving="resolving"
    :api-error="apiError"
    @resolve="handleResolve"
  />
  <main class="container">
    <ResolveResult
      v-if="result"
      :result="result"
      :url="lastUrl"
      :downloading="downloading"
      :download-error="downloadError"
      :summarizing="summarizing"
      :summarize-error="summarizeError"
      @download="handleDownload"
      @go-member="openMemberDialog"
      @summarize="handleSummarize"
    />
    <TaskPanel
      :tasks="tasks"
      @clear="handleClearTask"
      @clear-unfinished="handleClearUnfinished"
      @view-summary="handleViewSummary"
    />
  </main>
  <SiteFooter />
  <MemberSection
    v-model:visible="memberDialogVisible"
    :is-member="isMember"
    :expires-at="memberExpires"
    :submitting="memberSubmitting"
    :error="memberError"
    @unlock="handleUnlock"
  />
  <ConfirmDialog
    v-model:visible="errorDialog.visible"
    :title="errorDialog.title"
    :message="errorDialog.message"
    hide-cancel
  />
  <!-- 总结视图: v-if 控制挂载 (null = 关闭销毁), close 事件重置 -->
  <SummaryDialog
    v-if="summaryTask"
    :task="summaryTask"
    @close="summaryTask = null"
  />
</template>
