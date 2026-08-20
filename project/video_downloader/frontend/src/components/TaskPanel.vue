<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ConfirmDialog from './ConfirmDialog.vue'
import ErrorAlert from './ErrorAlert.vue'
import SummaryPanel from './SummaryPanel.vue'
import { retrySubtask } from '../api/client.js'

// 任务面板 (T08 重构): 同一视频的下载任务与 AI 总结任务按源链接 (url) 合并为
// 一个视频行, 左右两栏对齐展示 (左: 视频下载清晰度列表; 右: AI 总结四标签).
// 每个视频行可折叠 (默认展开, 折叠按钮在组头右侧).
// 清晰度 chip 五态: done 已完成可下载文件 / running 进行中 / locked 会员专属
// / idle 未下载 / redo|retry 过期或失败可重新下载; 各档位显示文件大小.
// 右区: 无总结任务 → 生成入口 (已总结过/进行中禁用, 后端幂等防重复);
// 有 → SummaryPanel 内嵌四标签 (总结/转录/思维导图/问答 独立进度与重试,
// 取代 SummaryDialog 弹窗). 已总结过的视频不可再次总结 (含过期, 需清除记录).
// 清除记录为整组清除 (删除该视频全部下载任务 + 总结任务), 任意状态均可清除;
// 「清除所有未完成记录」仍按任务级判定 (bugfix/0007).
// 状态与进度由 Home 经 SSE 实时更新, 倒计时本地每秒 tick (TTL 蓝粉配色).
const props = defineProps({
  // 任务列表 (kind=download|summary, 按 task_id 降序)
  tasks: { type: Array, default: () => [] },
  // 总结禁用判定 (ADR-0005 + 用户反馈: 已总结过不可再次总结): 入参 url,
  // 返回 '' 可总结 / 'active' 已有进行中任务 / 'done' 已总结过.
  // Home 传 summaryBlockReason 函数, 逐组判断 (跨标签页竞态兜底)
  summarizeDisabled: { type: Function, default: () => '' },
})

const emit = defineEmits([
  'clear-group',
  'clear-unfinished',
  'summarize',
  'download',
])

// 进行中状态集合: 弹窗取消提示共用 (下载任务五态 + 总结任务 running)
const RUNNING_STATUSES = [
  'pending',
  'resolving',
  'resolved',
  'queued',
  'downloading',
  'running',
]

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

// 视图完成判定: 已完成且未过期 (「清除所有未完成记录」的覆盖范围)
function isFinishedView(task) {
  return task.status === 'completed' && !isExpiredView(task)
}

// 未完成记录数 (视图判定): 决定「清除所有未完成记录」按钮显隐与弹窗文案
const unfinishedCount = computed(() => props.tasks.filter((t) => !isFinishedView(t)).length)
const hasUnfinished = computed(() => unfinishedCount.value > 0)

// 时长格式化: 秒 → "mm:ss" / "h:mm:ss"; 无时长返回空串
function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds)) return ''
  const total = Math.max(0, Math.round(seconds))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const mm = String(m).padStart(2, '0')
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`
}

// 播放量缩写: 万/亿 (123456 → 12.3万); 缺失返回空串
function formatCount(n) {
  if (n == null || Number.isNaN(n)) return ''
  if (n >= 1e8) return `${(n / 1e8).toFixed(1)}亿`
  if (n >= 1e4) return `${(n / 1e4).toFixed(1)}万`
  return String(n)
}

// 文件大小格式化: 字节 → GB/MB/KB (清晰度 chip 展示); 缺失显示「-」
function formatSize(bytes) {
  if (bytes == null || Number.isNaN(bytes)) return '-'
  if (bytes >= 1 << 30) return `${(bytes / (1 << 30)).toFixed(1)} GB`
  if (bytes >= 1 << 20) return `${(bytes / (1 << 20)).toFixed(1)} MB`
  if (bytes >= 1 << 10) return `${(bytes / (1 << 10)).toFixed(0)} KB`
  return `${bytes} B`
}

// 简介展开 (用户反馈): 默认一行省略, 超一行显示「展开」按钮.
// 超行判定用 scrollHeight > clientHeight (clamp 1 行时内容高度溢出可视区),
// 仅在收起态测量 (展开态全文可见会误判为不超行, 导致按钮消失)
const descOverflow = ref(new Set()) // 超一行 (需展开按钮) 的组 key 集合
const descExpanded = ref(new Set()) // 已展开的组 key 集合

function descEl(key, el) {
  if (!el || descExpanded.value.has(key)) return
  if (el.scrollHeight > el.clientHeight + 1) descOverflow.value.add(key)
  else descOverflow.value.delete(key)
}

function descIsOverflow(key) {
  return descOverflow.value.has(key)
}

function toggleDesc(key) {
  if (descExpanded.value.has(key)) descExpanded.value.delete(key)
  else descExpanded.value.add(key)
}

// 视频行折叠: 默认展开 (需求: 每个下载任务可折叠); key 为组链接
const collapsed = ref({})

function toggleCollapse(key) {
  collapsed.value[key] = !collapsed.value[key]
}

function isCollapsed(key) {
  return !!collapsed.value[key]
}

// 交付直链: 统一按任务 id 拼接 (完成后文件路由固定为 /api/files/{id})
function fileUrl(task) {
  return `/api/files/${task.task_id}`
}

// ---- 按源链接分组 (需求: 同一视频多清晰度合并一行, 左下载右总结) ----
// 分组键: SSE 事件带 source_url, 列表接口带 url (TaskOut); 无链接任务兜底按 id 单组.
// 不做链接归一化 (b23.tv 短链与完整链接分属两组), KISS
function groupKey(t) {
  return t.source_url || t.url || `task-${t.task_id}`
}

// 组: { key, url, title, cover, duration, site, uploader, view_count, description,
//       downloads: [], summary: null }
// formats 取组内第一个带 formats 的下载任务 (解析结果全量档位, 组内任意下载任务都带)
const groups = computed(() => {
  const map = new Map()
  for (const t of props.tasks) {
    const key = groupKey(t)
    let g = map.get(key)
    if (!g) {
      g = {
        key,
        url: t.source_url || t.url || '',
        title: t.title,
        cover: t.cover,
        duration: t.duration,
        site: t.site,
        uploader: t.uploader,
        view_count: t.view_count,
        description: t.description,
        downloads: [],
        summary: null,
        maxId: 0,
      }
      map.set(key, g)
    }
    // 元信息兜底: 组内任一任务携带即可 (总结任务创建时已回填)
    if (!g.uploader && t.uploader) g.uploader = t.uploader
    if (!g.view_count && t.view_count) g.view_count = t.view_count
    if (!g.description && t.description) g.description = t.description
    g.maxId = Math.max(g.maxId, t.task_id)
    if (t.kind === 'summary') {
      g.summary = t
    } else {
      g.downloads.push(t)
      // 组标题/封面兜底: 组内第一个有标题的任务
      if (!g.title && t.title) {
        g.title = t.title
        g.cover = t.cover
      }
    }
  }
  // 组排序: 按组内最大 task_id 降序 (新组在前, 等价于原列表序)
  return [...map.values()].sort((a, b) => b.maxId - a.maxId)
})

// 组内档位列表 (解析结果全量): 下载任务优先, 无下载任务时回退总结任务
// (仅 AI 总结不下载时, 下载区仍展示各清晰度档位, 锁定状态, 用户反馈)
function groupFormats(group) {
  const t = group.downloads.find((d) => d.formats && d.formats.length)
  if (t) return t.formats
  return group.summary?.formats || []
}

// 清晰度 chip 状态判定 (单档位五态)
// isTop: 最高档 chip 额外归并「最佳画质」任务 (format_id="best") 展示
function formatState(group, fmt, isTop) {
  let dl = group.downloads.find((t) => t.format_id === fmt.format_id)
  if (!dl && isTop) dl = group.downloads.find((t) => t.format_id === 'best')
  if (!dl) return { state: fmt.locked ? 'locked' : 'idle' }
  if (dl.status === 'completed' && !isExpiredView(dl)) return { state: 'done', task: dl }
  if (dl.status === 'expired') return { state: 'redo', task: dl } // 过期 → 重新下载
  if (dl.status === 'failed') return { state: 'retry', task: dl } // 失败 → 重试
  return { state: 'running', task: dl } // queued/downloading
}

// 渲染用 chip 列表: 真实档位 (过滤「最佳画质」伪档, 用户反馈: 下载记录里与
// 最高档重复, 该选项仅存在于解析卡下拉) + 每档状态预组装 (模板保持简单).
// 选「最佳画质」的任务 (format_id="best") 归并到最高档 chip, 文案显示
// best 档 label (用户反馈: 选择后显示「最佳画质 - xxxxp」而非仅「xxxxp」)
function groupChips(group) {
  const formats = groupFormats(group)
  const bestFmt = formats.find((f) => f.format_id === 'best')
  const real = formats.filter((f) => f.format_id !== 'best')
  return real.map((fmt, i, arr) => {
    const isTop = i === arr.length - 1 // formats 升序, 末尾为最高档
    const state = formatState(group, fmt, isTop)
    if (isTop && bestFmt && state.task?.format_id === 'best') {
      fmt = { ...fmt, label: bestFmt.label }
    }
    return { fmt, ...state }
  })
}

// ---- 整组清除 (需求: 一个视频一行, 清除 = 删该视频全部任务) ----
// 自定义确认弹窗 (bugfix/0006): kind: 'clear-group' (整组) | 'clear-unfinished' (批量)
// | 'error' (子任务重试失败等提示, 复用一个弹窗避免引入新组件)
const confirmState = ref(null)
const confirmVisible = ref(false)

function confirmClearGroup(group) {
  confirmState.value = { kind: 'clear-group', group }
  confirmVisible.value = true
}

// 清除所有未完成记录: 二次确认后由 Home 调后端批量清理
function confirmClearUnfinished() {
  confirmState.value = { kind: 'clear-unfinished' }
  confirmVisible.value = true
}

function onClearConfirm() {
  if (confirmState.value?.kind === 'error') {
    confirmState.value = null // 纯提示: 确认即关闭
    return
  }
  if (confirmState.value?.kind === 'clear-group') {
    emit('clear-group', confirmState.value.group)
  } else {
    emit('clear-unfinished')
  }
  confirmState.value = null
}

// 弹窗文案: 整组带任务构成统计与取消提示, 批量带数量, 错误为纯提示
const confirmTitle = computed(() => {
  if (confirmState.value?.kind === 'error') return '操作失败'
  return confirmState.value?.kind === 'clear-group'
    ? '确认清除记录'
    : '确认清除所有未完成记录'
})
const confirmMessage = computed(() => {
  if (confirmState.value?.kind === 'error') {
    return confirmState.value.message || '请稍后重试'
  }
  if (confirmState.value?.kind !== 'clear-group') {
    return `将删除 ${unfinishedCount.value} 条未完成记录对应的视频文件与任务记录, 不可恢复`
  }
  const g = confirmState.value.group
  const nDl = g.downloads.length
  const nSum = g.summary ? 1 : 0
  const running = [...g.downloads, g.summary].filter(Boolean).some((t) =>
    RUNNING_STATUSES.includes(t.status),
  )
  const hint = running ? ', 进行中任务将取消下载' : ''
  const sum = nSum ? ` + ${nSum} 个总结任务` : ''
  return `确认清除「${g.title || '该视频'}」的全部记录 (${nDl} 个下载任务${sum})?${hint}, 不可恢复`
})

// ---- 子任务重试 (SummaryPanel 四标签独立重试, 后端不扣配额) ----
async function handleRetry(taskId, name) {
  try {
    await retrySubtask(taskId, name)
    // 后端重置子任务为 pending 并重新入队, 状态经 SSE 推送自动恢复
  } catch (e) {
    confirmState.value = { kind: 'error', message: e.message || '重试失败, 请稍后重试' }
    confirmVisible.value = true
  }
}

</script>

<template>
  <section class="tasks fade-up" aria-label="下载任务">
    <h2 class="tasks__title">
      下载记录
      <button
        v-if="hasUnfinished"
        class="tasks__clear-unfinished"
        type="button"
        @click="confirmClearUnfinished"
      >
        清除所有未完成记录
      </button>
    </h2>

    <p v-if="groups.length === 0" class="tasks__empty">
      暂无下载任务, 解析视频后点击「开始下载」即可创建
    </p>

    <ul class="tasks__list">
      <!-- 一个视频一行: 组头 + 左右两栏 (左下载右总结) -->
      <li
        v-for="(group, index) in groups"
        :key="group.key"
        class="group-card"
      >
        <!-- 组头: 折叠按钮 + 封面 + 标题 + 元信息 (up主/播放量) + 整组清除 -->
        <div class="group-card__head">
          <button
            class="group-card__collapse"
            type="button"
            :title="isCollapsed(group.key) ? '展开' : '折叠'"
            @click="toggleCollapse(group.key)"
          >
            {{ isCollapsed(group.key) ? '▸' : '▾' }}
          </button>
          <div class="group-card__cover">
            <img
              v-if="group.cover"
              :src="group.cover"
              :alt="group.title"
              loading="lazy"
              referrerpolicy="no-referrer"
            />
            <div v-else class="group-card__cover-placeholder">🎬</div>
          </div>
          <div class="group-card__info">
            <h3 class="group-card__title">
              {{ group.title || `视频任务 #${index + 1}` }}
            </h3>
            <div class="group-card__meta">
              <span v-if="group.site" class="badge">{{ group.site }}</span>
              <span v-if="group.duration" class="badge badge--plain">
                {{ formatDuration(group.duration) }}
              </span>
              <span v-if="group.uploader" class="badge badge--plain">
                👤 {{ group.uploader }}
              </span>
              <span v-if="group.view_count" class="badge badge--plain">
                ▶ {{ formatCount(group.view_count) }}
              </span>
              <span class="badge badge--plain">
                {{ group.downloads.length }} 个下载{{ group.summary ? ' · 1 个总结' : '' }}
              </span>
            </div>
            <!-- 简介: 与标题同区 (标题下方, up主/播放量等元信息下一行),
                 默认一行省略, 超一行显示「展开」按钮 (点击展开/收起全文).
                 flex 行布局: 按钮贴简介文字行尾 (用户反馈: 不在第二行开头) -->
            <div class="group-card__desc-row">
              <p
                v-if="group.description"
                class="group-card__desc"
                :class="{ 'is-expanded': descExpanded.has(group.key) }"
                :ref="(el) => descEl(group.key, el)"
              >
                {{ group.description }}
              </p>
              <button
                v-if="group.description && descIsOverflow(group.key)"
                class="group-card__desc-toggle"
                type="button"
                @click="toggleDesc(group.key)"
              >
                {{ descExpanded.has(group.key) ? '收起' : '展开' }}
              </button>
            </div>
          </div>
          <button
            class="group-card__clear"
            type="button"
            @click="confirmClearGroup(group)"
          >
            清除记录
          </button>
        </div>

        <!-- 左右两栏 (折叠控制显示) -->
        <div v-show="!isCollapsed(group.key)" class="group-card__cols">
          <!-- 左侧: 视频信息 (简介) + 视频下载 (清晰度 chip 列表, 含文件大小) -->
          <div class="group-card__col group-card__col--dl">
            <h4 class="group-card__col-title">⬇ 视频下载</h4>
            <div v-if="groupChips(group).length" class="chips">
              <template v-for="chip in groupChips(group)" :key="chip.fmt.format_id">
                <!-- done: 整个 chip 可点击下载文件 (复制链接已删, 需求: 点击清晰度即可
                     下载); 内容均为 span, <a> 包裹合法, hover 有抬起反馈 -->
                <a
                  v-if="chip.state === 'done'"
                  class="chip chip--done"
                  :href="fileUrl(chip.task)"
                  :title="'点击下载视频文件'"
                >
                  <span class="chip__link">⬇ {{ chip.fmt.label }}</span>
                  <span class="chip__size">{{ formatSize(chip.fmt.filesize) }}</span>
                  <span
                    class="chip__ttl"
                    :class="{ 'ttl--warning': remainingMs(chip.task) < 3600000 }"
                    :title="'交付链接剩余有效时间'"
                  >
                    ⏳ {{ formatRemaining(remainingMs(chip.task)) }}
                  </span>
                </a>
                <!-- running: 下载中显示迷你进度条 (主题粉), 排队中无进度只示文字
                     (用户反馈: 排队任务 0% 进度条多余, 只保留真实下载进度) -->
                <div v-else-if="chip.state === 'running'" class="chip chip--running">
                  <span>{{ chip.fmt.label }}</span>
                  <template v-if="chip.task.status === 'downloading'">
                    <span class="chip__bar">
                      <span
                        class="chip__bar-fill"
                        :style="{ width: (chip.task.progress || 0) + '%' }"
                      ></span>
                    </span>
                    <span class="chip__pct">{{ Math.round(chip.task.progress) }}%</span>
                  </template>
                  <span v-else class="chip__status">排队中…</span>
                </div>
                <!-- locked: 会员专属档位, 置灰不可点 -->
                <button
                  v-else-if="chip.state === 'locked'"
                  class="chip chip--locked"
                  type="button"
                  disabled
                  title="会员专属清晰度, 解锁后可用"
                >
                  🔒 {{ chip.fmt.label }}
                </button>
                <!-- idle: 未下载档位, 置灰不可点 -->
                <button
                  v-else-if="chip.state === 'idle'"
                  class="chip chip--idle"
                  type="button"
                  disabled
                  title="该清晰度未下载"
                >
                  {{ chip.fmt.label }}
                </button>
                <!-- redo/retry: 过期或失败, 可点击重新下载 -->
                <button
                  v-else
                  class="chip chip--redo"
                  type="button"
                  :title="chip.state === 'redo' ? '交付已过期, 点击重新下载' : '下载失败, 点击重试'"
                  @click="emit('download', { url: group.url, formatId: chip.state.task?.format_id === 'best' ? 'best' : chip.fmt.format_id })"
                >
                  ↻ {{ chip.fmt.label }}
                </button>
              </template>
            </div>
            <p v-else class="group-card__empty">暂无下载记录</p>

            <!-- 组内下载任务错误 (message 冗余: 下载进度已在 chip 内展示) -->
            <template v-for="t in group.downloads" :key="`err-${t.task_id}`">
              <ErrorAlert v-if="t.error" :message="t.error" />
            </template>
          </div>

          <!-- 右侧: AI 总结 (无任务时空态, 有任务内嵌四标签面板) -->
          <div class="group-card__col group-card__col--ai">
            <!-- 无总结任务: 标题 + 空态 + 生成入口 (同 url 有活跃总结任务时禁用);
                 有任务时标题由 SummaryPanel 渲染 (与四标签同行) -->
            <template v-if="!group.summary">
              <h4 class="group-card__col-title">✨ AI 总结</h4>
              <div class="ai-empty">
                <p class="ai-empty__text">尚未生成 AI 总结</p>
                <button
                  class="btn-gradient ai-empty__btn"
                  type="button"
                  :disabled="!!summarizeDisabled(group.url)"
                  @click="emit('summarize', group.url)"
                >
                  {{
                    summarizeDisabled(group.url) === 'active'
                      ? '总结生成中…'
                      : summarizeDisabled(group.url) === 'done'
                        ? '已总结过'
                        : '✨ 生成 AI 总结'
                  }}
                </button>
                <p v-if="summarizeDisabled(group.url)" class="ai-empty__hint">
                  {{
                    summarizeDisabled(group.url) === 'active'
                      ? '该视频已有总结任务, 请等待完成'
                      : '该视频已总结过, 清除记录后可重新总结'
                  }}
                </p>
              </div>
            </template>
            <!-- 有总结任务: 内嵌四标签面板 (状态/进度/重试均在面板内) -->
            <div v-else class="ai-panel">
              <!-- 已总结过的视频不可再次总结 (用户反馈): 过期后提示, 需清除记录后重新生成 -->
              <ErrorAlert
                v-if="isExpiredView(group.summary)"
                message="总结结果已过期清理, 清除记录后可重新总结"
              />
              <SummaryPanel
                v-else
                :task="group.summary"
                @retry="(name) => handleRetry(group.summary.task_id, name)"
              />
            </div>
          </div>
        </div>
      </li>
    </ul>

    <ConfirmDialog
      v-model:visible="confirmVisible"
      :title="confirmTitle"
      :message="confirmMessage"
      :confirm-text="confirmState?.kind === 'error' ? '知道了' : '确认清除'"
      :hide-cancel="confirmState?.kind === 'error'"
      :danger="confirmState?.kind !== 'error'"
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

/* 视频行卡片 (一个视频一组: 组头 + 左右两栏) */
.group-card {
  padding: 16px;
  border-radius: var(--radius);
  background: var(--card);
  border: 1px solid var(--border);
  box-shadow: var(--shadow-card);
  transition:
    transform 0.2s ease,
    border-color 0.2s ease;
}

.group-card:hover {
  transform: translateY(-2px);
  border-color: rgba(0, 174, 236, 0.35);
}

/* 组头 (上下留白加大: 需求 区域拉长, 容纳更大的封面与字体).
   flex-start: 封面贴顶固定, 不随信息区高度变化 (简介展开/任务状态变化)
   垂直居中移动 (用户反馈: 封面要一直在上面不要动) */
.group-card__head {
  display: flex;
  align-items: flex-start;
  gap: 16px;
  padding-bottom: 18px;
  border-bottom: 1px solid var(--border);
}

/* 折叠按钮 (组头左侧, 默认展开) */
.group-card__collapse {
  flex: 0 0 auto;
  width: 26px;
  height: 26px;
  border-radius: 8px;
  border: 1px solid var(--border);
  background: rgba(31, 35, 41, 0.03);
  color: var(--text-sub);
  font-size: 12px;
  line-height: 1;
  transition:
    background 0.2s ease,
    color 0.2s ease;
}

.group-card__collapse:hover {
  background: var(--primary-soft);
  color: var(--primary);
}

/* 封面宽度加大 (用户反馈: 图片展示区域大一点) */
.group-card__cover {
  flex: 0 0 200px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgba(31, 35, 41, 0.04);
  aspect-ratio: 16 / 9;
}

.group-card__cover img {
  width: 100%;
  height: 100%;
  object-fit: cover;
}

.group-card__cover-placeholder {
  width: 100%;
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 20px;
}

.group-card__info {
  flex: 1;
  min-width: 0;
}

.group-card__title {
  font-size: 17px;
  font-weight: 700;
  line-height: 1.4;
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* 元信息 badge 局部放大 (需求: 组头字体清晰可读) */
.group-card__meta {
  margin-top: 10px;
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.group-card__meta .badge {
  font-size: 13px;
  padding: 4px 12px;
}

/* 整组清除记录 (右上角) */
.group-card__clear {
  flex: 0 0 auto;
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

.group-card__clear:hover {
  color: var(--danger);
  border-color: rgba(255, 77, 79, 0.45);
}

/* 左右两栏: 左下载 3 / 右 AI 总结 7 (需求: 占比 3:7);
   上下留白加大 (需求: 展开区域上下加长);
   min-width: 0 防 grid 子项被思维导图画布等大内容撑破卡片 (溢出修复) */
.group-card__cols {
  margin-top: 20px;
  padding: 6px 0 12px;
  display: grid;
  grid-template-columns: 3fr 7fr;
  gap: 20px;
}

.group-card__col {
  min-width: 0;
}

.group-card__col--dl {
  padding-right: 20px;
  border-right: 1px solid var(--border);
}

.group-card__col-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-sub);
  margin-bottom: 10px;
}

/* 视频简介: 与标题同区 (组头内, 元信息下一行), 默认一行省略,
   超一行显示「展开」按钮 (用户反馈: 一行预览 + 点击展开全文) */
.group-card__desc-row {
  margin-top: 10px;
  display: flex;
  align-items: flex-start;
  gap: 8px;
}

.group-card__desc {
  flex: 1;
  min-width: 0;
  font-size: 14px;
  line-height: 1.6;
  color: var(--text-sub);
  display: -webkit-box;
  -webkit-line-clamp: 1;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

.group-card__desc.is-expanded {
  -webkit-line-clamp: unset;
}

/* 展开/收起: 贴简介行尾 (flex 行内), 展开态仍与首行对齐 */
.group-card__desc-toggle {
  flex: 0 0 auto;
  font-size: 12px;
  line-height: 1.6;
  color: var(--primary);
  white-space: nowrap;
  cursor: pointer;
}

.group-card__desc-toggle:hover {
  text-decoration: underline;
}

.group-card__empty {
  font-size: 13px;
  color: var(--text-dim);
}

/* 清晰度 chip 列表 */
.chips {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.chip {
  display: flex;
  align-items: center;
  gap: 10px;
  min-height: 42px;
  padding: 0 14px;
  border-radius: var(--radius-sm);
  border: 1px solid var(--border);
  background: rgba(31, 35, 41, 0.03);
  font-size: 14px;
  text-align: left;
}

/* done: 整行可点击下载 (渐变描边 + 悬停抬起, 呼应主按钮) */
.chip--done {
  border-color: rgba(0, 174, 236, 0.4);
  background: rgba(0, 174, 236, 0.06);
  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.chip--done:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(0, 174, 236, 0.2);
}

.chip__link {
  font-weight: 600;
  font-size: 15px;
  color: var(--primary);
  white-space: nowrap;
}

/* 文件大小 (GB/MB/KB, 无数据时显示 -) */
.chip__size {
  margin-left: auto;
  font-size: 13px;
  color: var(--text-dim);
  white-space: nowrap;
}

/* TTL 蓝粉倒计时: 默认粉色, 剩 1h 内转蓝 (需求: 倒计时用网页蓝粉色调) */
.chip__ttl {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--primary);
  white-space: nowrap;
}

.chip__ttl.ttl--warning {
  color: var(--blue);
}

/* running: 迷你进度条 */
.chip--running {
  border-color: rgba(0, 174, 236, 0.25);
  background: rgba(0, 174, 236, 0.04);
}

.chip__bar {
  flex: 1;
  min-width: 40px;
  height: 7px;
  border-radius: 999px;
  background: rgba(31, 35, 41, 0.12);
  overflow: hidden;
}

/* 主题浅粉色 (用户反馈: 进度条要网页主题色, 写死 --primary 色值保证必现,
   不依赖 var(--gradient) 变量: 变量解析异常时 fill 透明只剩灰底) */
.chip__bar-fill {
  /* display: block 必须: span 默认 inline, 百分比 width/height 不生效,
      fill 宽度恒为 0 导致进度条一直没颜色 (bugfix: 与 SummaryPanel .bar__fill
      用 absolute 同理, 这里用 block + 父级固定高度更简单) */
  display: block;
  height: 100%;
  border-radius: 999px;
  background: #fb7299;
  transition: width 0.3s ease;
}

.chip__pct {
  font-family: var(--font-mono);
  font-size: 13px;
  color: var(--text-sub);
  min-width: 36px;
  text-align: right;
}

/* 排队中: 无进度可展示, 只示文字 (避免无意义的 0% 进度条) */
.chip__status {
  margin-left: auto;
  font-size: 13px;
  color: var(--text-dim);
}

/* locked / idle: 置灰不可点 */
.chip--locked,
.chip--idle {
  color: var(--text-dim);
  background: rgba(31, 35, 41, 0.03);
  opacity: 0.65;
  cursor: not-allowed;
}

/* redo/retry: 可点击重新下载 */
.chip--redo {
  border-color: rgba(0, 174, 236, 0.35);
  color: var(--blue);
  font-weight: 600;
  cursor: pointer;
  transition:
    transform 0.2s ease,
    box-shadow 0.2s ease;
}

.chip--redo:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 16px rgba(0, 174, 236, 0.2);
}

/* 右侧 AI 总结区 */
.ai-empty {
  padding: 22px 16px;
  text-align: center;
  border: 1px dashed var(--border);
  border-radius: var(--radius-sm);
}

.ai-empty__text {
  font-size: 13px;
  color: var(--text-dim);
}

.ai-empty__btn {
  margin-top: 12px;
  padding: 7px 18px;
  font-size: 13px;
}

.ai-empty__hint {
  margin-top: 8px;
  font-size: 12px;
  color: var(--text-dim);
}

@media (max-width: 640px) {
  .group-card__head {
    align-items: flex-start;
  }

  .group-card__cover {
    flex-basis: 0;
    max-width: 96px;
  }

  .group-card__clear {
    margin-left: auto;
  }

  /* 左右两栏收成单列, 分隔线改上边 */
  .group-card__cols {
    grid-template-columns: 1fr;
    gap: 14px;
  }

  .group-card__col--dl {
    padding-right: 0;
    padding-bottom: 14px;
    border-right: none;
    border-bottom: 1px solid var(--border);
  }
}
</style>
