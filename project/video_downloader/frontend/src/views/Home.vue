<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import HeroSection from '../components/HeroSection.vue'
import MemberSection from '../components/MemberSection.vue'
import NavBar from '../components/NavBar.vue'
import PlatformWall from '../components/PlatformWall.vue'
import ResolveResult from '../components/ResolveResult.vue'
import SiteFooter from '../components/SiteFooter.vue'
import TaskPanel from '../components/TaskPanel.vue'
import { useMember } from '../composables/useMember.js'
import { createDownload, fetchTasks, resolveUrl } from '../api/client.js'

// 单页布局 (PRD §10): 导航 → Hero → 解析结果 → 任务面板 → 平台墙 → 会员区 → 页脚
const resolving = ref(false)
const result = ref(null)
const apiError = ref('')
const downloading = ref(false)
const downloadError = ref('')
const tasks = ref([])

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

// 会员区组件实例: 滚动定位后聚焦密钥输入框
const memberSection = ref(null)

// SSE 连接: 任务进度实时推送 (无轮询)
let eventSource = null

// 任务列表防抖刷新: 创建失败 / SSE 未知任务时拉取真实列表补齐
// (失败任务已落库, 恢复与即时展示保持一致)
let refreshTimer = null

async function refreshTasks() {
  try {
    const { tasks: list } = await fetchTasks()
    tasks.value = list.filter((t) => t.kind === 'download')
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

async function handleDownload({ url, formatId }) {
  downloading.value = true
  downloadError.value = ''
  try {
    const { task_id } = await createDownload(url, formatId)
    // 本地构造任务卡片立即展示 (完整元信息来自解析结果,
    // 后续状态/进度由 SSE 事件覆盖)
    tasks.value.unshift({
      task_id,
      kind: 'download',
      status: 'queued',
      title: result.value?.title ?? '',
      cover: result.value?.cover,
      duration: result.value?.duration,
      site: result.value?.site,
      progress: 0,
      message: null,
      error: null,
    })
  } catch (e) {
    downloadError.value = e.message
    // 创建失败时后端任务已落库 (failed), 补拉列表使其上卡,
    // 与刷新后恢复的展示一致 (失败卡不会只在刷新后出现)
    scheduleRefresh()
  } finally {
    downloading.value = false
  }
}

// 导航栏会员入口 / 结果卡解锁引导 (US 35): 滚动到会员营销区,
// 未解锁时聚焦密钥输入框
function scrollToMember() {
  memberSection.value?.$el.scrollIntoView({ behavior: 'smooth', block: 'start' })
  if (!isMember.value) {
    // 平滑滚动时长不定, 等滚动完成后聚焦输入框
    setTimeout(() => memberSection.value?.focusInput(), 400)
  }
}

// SSE task-update: 合并状态字段到已有任务; 未知任务 (竞态先于
// POST 响应到达的 queued 事件等) 触发防抖刷新补齐, 避免状态滞后
function handleTaskUpdate(evt) {
  let data
  try {
    data = JSON.parse(evt.data)
  } catch {
    return
  }
  const idx = tasks.value.findIndex((t) => t.task_id === data.task_id)
  if (idx >= 0) {
    tasks.value[idx] = { ...tasks.value[idx], ...data }
  } else {
    scheduleRefresh()
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
    tasks.value = list.filter((t) => t.kind === 'download')
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
  <NavBar :is-member="isMember" @go-member="scrollToMember" />
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
      @download="handleDownload"
      @go-member="scrollToMember"
    />
    <TaskPanel :tasks="tasks" />
    <PlatformWall />
    <MemberSection
      ref="memberSection"
      :is-member="isMember"
      :expires-at="memberExpires"
      :submitting="memberSubmitting"
      :error="memberError"
      @unlock="handleUnlock"
    />
  </main>
  <SiteFooter />
</template>
