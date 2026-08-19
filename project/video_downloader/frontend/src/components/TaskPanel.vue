<script setup>
import { ref } from 'vue'

// 下载任务面板 (T08): 任务卡片列表
// 卡片: 封面缩略图 + 标题 + 状态徽章 + 进度条 + 失败原因
// completed 显示「下载到手机/电脑」+「复制链接」操作
// 状态与进度由 Home 经 SSE 实时更新, 本组件纯展示
const props = defineProps({
  // 下载任务列表 (kind=download, 按 task_id 降序)
  tasks: { type: Array, default: () => [] },
})

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

// 进度条宽度: 完成固定 100, 其余按任务进度
// (模板与百分比文本共用同一规则)
function widthPercent(task) {
  return task.status === 'completed' ? 100 : task.progress
}

// 进度百分比文本: 下载中按任务进度, 完成固定 100%, 其余无
function progressText(task) {
  if (task.status === 'completed') return '100%'
  if (task.status === 'downloading') return `${Math.round(task.progress)}%`
  return ''
}

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
    </h2>

    <p v-if="tasks.length === 0" class="tasks__empty">
      暂无下载任务, 解析视频后点击「开始下载」即可创建
    </p>

    <ul class="tasks__list">
      <li v-for="task in tasks" :key="task.task_id" class="task-card">
        <div class="task-card__cover">
          <img
            v-if="task.cover"
            :src="task.cover"
            :alt="task.title"
            loading="lazy"
          />
          <div v-else class="task-card__cover-placeholder">🎬</div>
        </div>

        <div class="task-card__body">
          <div class="task-card__head">
            <h3 class="task-card__title">
              {{ task.title || `下载任务 #${task.task_id}` }}
            </h3>
            <span class="badge" :class="STATUS_TONE[task.status]">
              {{ STATUS_TEXT[task.status] || task.status }}
            </span>
          </div>

          <div
            v-if="task.status === 'downloading' || task.status === 'completed'"
            class="task-card__progress"
          >
            <div class="task-card__bar">
              <div
                class="task-card__bar-fill"
                :style="{ width: widthPercent(task) + '%' }"
              ></div>
            </div>
            <span class="task-card__percent">{{ progressText(task) }}</span>
          </div>

          <p
            v-if="task.status === 'downloading' && task.message"
            class="task-card__message"
          >
            {{ task.message }}
          </p>
          <p v-if="task.status === 'failed' && task.error" class="task-card__error">
            {{ task.error }}
          </p>
          <p v-if="task.status === 'expired'" class="task-card__error">
            交付链接已过期, 文件已清理, 如需请重新下载
          </p>

          <div v-if="task.status === 'completed'" class="task-card__actions">
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
    border-color 0.2s ease;
}

.task-card:hover {
  transform: translateY(-2px);
  border-color: rgba(235, 47, 150, 0.35);
}

.task-card__cover {
  flex: 0 0 120px;
  border-radius: var(--radius-sm);
  overflow: hidden;
  background: rgba(255, 255, 255, 0.04);
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
  background: rgba(255, 255, 255, 0.08);
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

.task-card__error {
  font-size: 13px;
  color: var(--danger);
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
