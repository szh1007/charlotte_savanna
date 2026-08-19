<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from './ConfirmDialog.vue'
import ErrorAlert from './ErrorAlert.vue'

// 下载任务面板 (T08): 任务卡片列表
// 卡片: 封面缩略图 + 标题 + 清晰度 + 状态徽章 + 进度条 + 失败原因
// completed 显示「下载到手机/电脑」+「复制链接」+ 交付过期倒计时
// 任意状态卡均可清除记录 (进行中任务 = 取消下载, bugfix/0007)
// 状态与进度由 Home 经 SSE 实时更新, 清除记录事件 (clear/clear-unfinished)
// 由 Home 调用后端后移除卡片 (本组件负责二次确认 UI)
const props = defineProps({
  // 下载任务列表 (kind=download, 按 task_id 降序)
  tasks: { type: Array, default: () => [] },
})

const emit = defineEmits(['clear', 'clear-unfinished'])

// 状态 → 中文文案 (状态机见 CONTEXT.md)
const STATUS_TEXT = {
  pending: '待解析',
  resolving: '解析中',
  resolved: '已解析',
  queued: '排队中',
  downloading: '下载中',
  completed: '已完成',
  failed: '失败',
  expired: '已过期',
}

// 状态 → 徽章着色类 (scoped 样式按类定义)
const STATUS_TONE = {
  downloading: 'badge--active',
  completed: 'badge--success',
  failed: 'badge--danger',
  expired: 'badge--danger',
}

// 倒计时基准时刻 (每秒 tick, 驱动剩余时间与本地过期判定)
const now = ref(Date.now())
let ttlTimer = null
onMounted(() => {
  ttlTimer = setInterval(() => (now.value = Date.now()), 1000)
})
onBeforeUnmount(() => clearInterval(ttlTimer))

// 剩余交付时间 (ms): expires_at 缺失 (旧数据) 视为 0
function remainingMs(task) {
  if (!task.expires_at) return 0
  return Math.max(0, task.expires_at * 1000 - now.value)
}

// 秒 → hh:mm:ss (交付过期倒计时格式)
function formatRemaining(ms) {
  const total = Math.floor(ms / 1000)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  return [h, m, s].map((v) => String(v).padStart(2, '0')).join(':')
}

// 视图过期判定: 后端已标 expired, 或倒计时归零 (后端周期清理最迟 60s 后
// 广播 expired, 本地先行显示过期避免残留 00:00:00)
function isExpiredView(task) {
  if (task.status === 'expired') return true
  return task.status === 'completed' && !!task.expires_at && remainingMs(task) <= 0
}

// 徽章文案: 本地已归零的 completed 卡按过期展示, 与右侧「已过期」一致
function statusText(task) {
  return STATUS_TEXT[isExpiredView(task) ? 'expired' : task.status] || task.status
}

// 清晰度标注: 按选定档位从 formats 列表取 label (如 "720p MP4" / "最佳画质 - 1080p")
function formatLabel(task) {
  const fmt = (task.formats || []).find((f) => f.format_id === task.format_id)
  return fmt ? fmt.label : ''
}

// 视图完成判定: 已完成且未过期. 其余均视为未完成 (排队中/下载中/失败/
// 已过期/倒计时归零), 是「清除所有未完成记录」的覆盖范围 (bugfix/0007)
function isFinishedView(task) {
  return task.status === 'completed' && !isExpiredView(task)
}

// 未完成记录数 (视图判定): 决定「清除所有未完成记录」按钮显隐与弹窗文案
const unfinishedCount = computed(() => props.tasks.filter((t) => !isFinishedView(t)).length)
const hasUnfinished = computed(() => unfinishedCount.value > 0)

// 徽章着色按视图状态: 倒计时归零的 completed 卡按 expired 显示红色,
// 与文案 (statusText) 保持一致 (bugfix/0006)
function statusTone(task) {
  return STATUS_TONE[isExpiredView(task) ? 'expired' : task.status]
}

// 自定义确认弹窗 (bugfix/0006): 替代浏览器原生 confirm.
// kind: 'clear' (单条) | 'clear-unfinished' (批量清除未完成)
const confirmState = ref(null)
const confirmVisible = ref(false)

// 进行中状态集合: 弹窗文案提示「清除将取消下载」
const RUNNING_STATUSES = ['pending', 'resolving', 'resolved', 'queued', 'downloading']

// 清除记录: 无交付资产的终态 (failed / expired) 直接清除 (无需确认,
// 无文件可删); 其余状态弹二次确认 — 进行中任务确认后取消下载,
// completed 确认后删除视频文件, 均不可恢复
function confirmClear(task) {
  if (task.status === 'failed' || isExpiredView(task)) {
    emit('clear', task)
    return
  }
  confirmState.value = { kind: 'clear', task }
  confirmVisible.value = true
}

// 清除所有未完成记录: 二次确认后由 Home 调后端批量清理
// (排队中/下载中/失败/已过期全部清除, 仅保留已完成未过期)
function confirmClearUnfinished() {
  confirmState.value = { kind: 'clear-unfinished' }
  confirmVisible.value = true
}

function onClearConfirm() {
  if (confirmState.value?.kind === 'clear') {
    emit('clear', confirmState.value.task)
  } else {
    emit('clear-unfinished')
  }
  confirmState.value = null
}

// 弹窗文案: 按清除目标组装 (单条带任务名与取消提示, 批量带数量)
const confirmTitle = computed(() =>
  confirmState.value?.kind === 'clear'
    ? '确认清除记录'
    : '确认清除所有未完成记录',
)
const confirmMessage = computed(() => {
  if (confirmState.value?.kind !== 'clear') {
    return `将删除 ${unfinishedCount.value} 条未完成记录对应的视频文件与任务记录, 不可恢复`
  }
  const t = confirmState.value.task
  const running = RUNNING_STATUSES.includes(t.status)
  const hint = running ? '该任务正在执行, 清除将取消下载' : '视频文件将被删除'
  return `确认清除「${t.title || '该任务'}」的记录? ${hint}, 不可恢复`
})

// 交付直链: 统一按任务 id 拼接 (页面恢复时 SSE 事件无 url 字段,
// 完成后文件路由固定为 /api/files/{id})
function fileUrl(task) {
  return `/api/files/${task.task_id}`
}

// 复制反馈状态: '' | 'copied' | 'failed' (2s 后恢复)
const copyState = ref('')
let copyTimer = null

async function copyLink(task) {
  try {
    await navigator.clipboard.writeText(`${location.origin}${fileUrl(task)}`)
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => (copyState.value = ''), 2000)
}
</script>

<template>
  <section class="tasks fade-up" aria-label="下载任务">
    <h2 class="tasks__title">
      下载任务
      <span v-if="tasks.length" class="tasks__count">{{ tasks.length }}</span>
      <button
        v-if="hasUnfinished"
        class="tasks__clear-unfinished"
        type="button"
        @click="confirmClearUnfinished"
      >
        清除所有未完成记录
      </button>
    </h2>

    <p v-if="tasks.length === 0" class="tasks__empty">
      暂无下载任务, 解析视频后点击「开始下载」即可创建
    </p>

    <ul class="tasks__list">
      <li
        v-for="(task, index) in tasks"
        :key="task.task_id"
        class="task-card"
        :class="{ 'task-card--expired': isExpiredView(task) }"
      >
        <div class="task-card__cover">
          <img
            v-if="task.cover"
            :src="task.cover"
            :alt="task.title"
            loading="lazy"
            referrerpolicy="no-referrer"
          />
          <div v-else class="task-card__cover-placeholder">🎬</div>
        </div>

        <div class="task-card__body">
          <div class="task-card__head">
            <h3 class="task-card__title">
              <!-- 占位序号按列表顺序 (#1 起), 不用全局 task_id:
                   resolve 任务会占掉初始序号, 从 #2 开始 (用户反馈) -->
              {{ task.title || `下载任务 #${index + 1}` }}
              <span v-if="formatLabel(task)" class="task-card__format">
                {{ formatLabel(task) }}
              </span>
            </h3>
            <div class="task-card__meta">
              <!-- 倒计时仅未过期 completed 显示; 归零后徽章转「已过期」
                   (statusText 视图判定), 此处不再重复展示 (bugfix/0006) -->
              <span
                v-if="task.status === 'completed' && !isExpiredView(task)"
                class="task-card__ttl"
                :class="{ 'ttl--warning': remainingMs(task) < 3600000 }"
                :title="`交付链接剩余有效时间`"
              >
                ⏳ {{ formatRemaining(remainingMs(task)) }}
              </span>
              <span class="badge" :class="statusTone(task)">
                {{ statusText(task) }}
              </span>
              <!-- 任意状态可清除: 进行中任务点击后弹确认 (取消下载) (bugfix/0007) -->
              <button
                class="task-card__clear"
                type="button"
                @click="confirmClear(task)"
              >
                清除记录
              </button>
            </div>
          </div>

          <!-- 进度条仅下载中显示: 完成后信息由倒计时/操作按钮替代 (bugfix/0006) -->
          <div
            v-if="task.status === 'downloading'"
            class="task-card__progress"
          >
            <div class="task-card__bar">
              <div
                class="task-card__bar-fill"
                :style="{ width: (task.progress || 0) + '%' }"
              ></div>
            </div>
            <span class="task-card__percent">{{ Math.round(task.progress) }}%</span>
          </div>

          <p
            v-if="task.status === 'downloading' && task.message"
            class="task-card__message"
          >
            {{ task.message }}
          </p>
          <ErrorAlert :message="task.error" />
          <ErrorAlert
            v-if="task.status === 'expired'"
            message="交付链接已过期, 文件已清理, 如需请重新下载"
          />

          <div
            v-if="task.status === 'completed' && !isExpiredView(task)"
            class="task-card__actions"
          >
            <a class="btn-outline-gradient task-card__btn" :href="fileUrl(task)">
              ⬇ 下载到手机/电脑
            </a>
            <button class="btn-outline-gradient task-card__btn" @click="copyLink(task)">
              {{
                copyState === 'copied'
                  ? '✓ 已复制'
                  : copyState === 'failed'
                    ? '复制失败, 请手动复制'
                    : '复制链接'
              }}
            </button>
          </div>
        </div>
      </li>
    </ul>

    <ConfirmDialog
      v-model:visible="confirmVisible"
      :title="confirmTitle"
      :message="confirmMessage"
      confirm-text="确认清除"
      danger
      @confirm="onClearConfirm"
    />
  </section>
</template>

<style scoped>
.tasks {
  margin-top: 40px;
}

.tasks__title {
  font-size: 20px;
  font-weight: 700;
  display: flex;
  align-items: center;
  gap: 10px;
}

.tasks__count {
  padding: 1px 10px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 600;
  color: var(--primary);
  background: var(--primary-soft);
}

/* 一键清除全部未完成记录 (标题行右侧, 仅存在未完成记录时显示) */
.tasks__clear-unfinished {
  margin-left: auto;
  padding: 5px 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
  font-size: 12px;
  cursor: pointer;
  transition:
    color 0.2s ease,
    border-color 0.2s ease;
}

.tasks__clear-unfinished:hover {
  color: var(--danger);
  border-color: rgba(255, 77, 79, 0.45);
}

.tasks__empty {
  margin-top: 16px;
  padding: 28px 20px;
  text-align: center;
  font-size: 14px;
  color: var(--text-dim);
  border: 1px dashed var(--border);
  border-radius: var(--radius);
}

.tasks__list {
  margin-top: 16px;
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 12px;
}

/* 任务卡片 */
.task-card {
  display: flex;
  gap: 16px;
  padding: 16px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  transition:
    transform 0.2s ease,
    border-color 0.2s ease,
    background 0.3s ease;
}

.task-card:hover {
  transform: translateY(-2px);
  border-color: rgba(0, 174, 236, 0.35);
}

/* 已过期卡片 (视图判定, 倒计时归零立即生效): 红色底色 + 边框 (bugfix/0006) */
.task-card--expired {
  background: rgba(255, 77, 79, 0.08);
  border-color: rgba(255, 77, 79, 0.35);
}

.task-card__cover {
  flex: 0 0 120px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgba(31, 35, 41, 0.04);
  aspect-ratio: 16 / 9;
}

.task-card__cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.task-card__cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 26px;
}

.task-card__body {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.task-card__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
}

.task-card__title {
  font-size: 15px;
  font-weight: 600;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 标题后的清晰度标注 (如 "720p MP4"), 区分同名多档任务 */
.task-card__format {
  display: inline-block;
  margin-left: 8px;
  padding: 1px 8px;
  border-radius: 999px;
  font-size: 11px;
  font-weight: 500;
  color: var(--text-sub);
  background: rgba(31, 35, 41, 0.06);
  vertical-align: 2px;
  white-space: nowrap;
}

/* 头部右侧: 倒计时 + 徽章 + 清除记录 */
.task-card__meta {
  flex: 0 0 auto;
  display: flex;
  align-items: center;
  gap: 8px;
}

/* 交付过期倒计时 (等宽数字, 避免每秒跳动宽度变化) */
.task-card__ttl {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-sub);
  white-space: nowrap;
}

.task-card__ttl.ttl--warning {
  color: var(--danger);
}

/* 清除记录 (右上角): 未过期弹二次确认, 已过期直接清除 */
.task-card__clear {
  padding: 4px 12px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: transparent;
  color: var(--text-dim);
  font-size: 12px;
  cursor: pointer;
  transition:
    color 0.2s ease,
    border-color 0.2s ease;
}

.task-card__clear:hover {
  color: var(--danger);
  border-color: rgba(255, 77, 79, 0.45);
}

/* 进度条 */
.task-card__progress {
  display: flex;
  align-items: center;
  gap: 12px;
}

.task-card__bar {
  flex: 1;
  height: 6px;
  border-radius: 999px;
  background: rgba(31, 35, 41, 0.08);
  overflow: hidden;
}

.task-card__bar-fill {
  height: 100%;
  border-radius: 999px;
  background: var(--gradient);
  transition: width 0.3s ease;
}

.task-card__percent {
  flex: 0 0 auto;
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-sub);
  min-width: 42px;
  text-align: right;
}

/* 提示文案 */
.task-card__message {
  font-size: 13px;
  color: var(--text-sub);
}

/* 完成操作 */
.task-card__actions {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
}

.task-card__btn {
  padding: 8px 18px;
  font-size: 13px;
}

/* 徽章着色 (状态 → 色相) */
.badge--active {
  background: var(--primary-soft);
  color: var(--primary);
}

.badge--success {
  background: rgba(82, 196, 26, 0.14);
  color: var(--success);
}

.badge--danger {
  background: rgba(255, 77, 79, 0.14);
  color: var(--danger);
}

@media (max-width: 640px) {
  .task-card {
    flex-direction: column;
  }

  .task-card__cover {
    flex-basis: auto;
    max-width: 220px;
  }

  .task-card__head {
    flex-direction: column;
    gap: 6px;
  }

  .task-card__btn {
    flex: 1;
    justify-content: center;
  }
}
</style>
