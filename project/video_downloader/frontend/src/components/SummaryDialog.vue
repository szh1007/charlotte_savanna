<script setup>
import { computed, onBeforeUnmount, onMounted, nextTick, ref } from 'vue'
import ErrorAlert from './ErrorAlert.vue'
import {
  askQuestion,
  fetchSummary,
  fetchTranscript,
} from '../api/client.js'

// AI 总结视图 (ADR-0005): 概述 / 章节时间线 / 思维导图 / 转录全文 / 问答.
// 挂载驱动 (父组件 v-if 控制): 挂载即拉取, close 事件通知父组件销毁.
// 数据按需拉取 (summary + transcript 并行), 任务过期 (410) 提示重新总结.
// 导出走后端直链下载 (md = 总结, txt = 转录全文), 无鉴权可 a 标签直链.
const props = defineProps({
  // 总结任务 (含 task_id / title, SSE 更新后的最新状态)
  task: { type: Object, required: true },
})

const emit = defineEmits(['close'])

function close() {
  emit('close')
}

// 加载态: 打开时并行拉取总结 + 转录
const loading = ref(false)
const loadError = ref('')
const expired = ref(false) // 结果已过期清理 (410): 提示重新总结
const summary = ref(null) // SummaryOut: {title, duration, summary:{overview, chapters, key_points, conclusion}}
const transcript = ref(null) // TranscriptOut: {text, segments}

async function load() {
  loading.value = true
  loadError.value = ''
  expired.value = false
  try {
    const [s, t] = await Promise.all([
      fetchSummary(props.task.task_id),
      fetchTranscript(props.task.task_id),
    ])
    summary.value = s
    transcript.value = t
  } catch (e) {
    if (e.status === 410) {
      expired.value = true
    } else {
      loadError.value = e.message || '总结结果加载失败'
    }
  } finally {
    loading.value = false
  }
}

// 挂载即加载 + 挂全局 Esc 监听 (Teleport 到 body); 卸载时移除
function onKeydown(e) {
  if (e.key === 'Escape') close()
}

onMounted(() => {
  load()
  document.addEventListener('keydown', onKeydown)
})

onBeforeUnmount(() => document.removeEventListener('keydown', onKeydown))

// 章节时间线格式化: 秒 → "mm:ss" (导出与展示一致)
function fmtTime(seconds) {
  const total = Math.max(0, Math.round(seconds || 0))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// 转录段首尾时间 (思维导图/时间线可定位, 纯展示)
const chapters = computed(() => summary.value?.summary?.chapters || [])

// 转录全文: 默认折叠 (长文本省流), 展开 + 一键复制
const transcriptOpen = ref(false)
const copyState = ref('') // '' | 'copied' | 'failed'
let copyTimer = null

async function copyTranscript() {
  try {
    await navigator.clipboard.writeText(transcript.value?.text || '')
    copyState.value = 'copied'
  } catch {
    copyState.value = 'failed'
  }
  clearTimeout(copyTimer)
  copyTimer = setTimeout(() => (copyState.value = ''), 2000)
}

onBeforeUnmount(() => clearTimeout(copyTimer))

// ---- AI 问答 (免费档每日配额, 429 由后端拒绝并提示) ----
const messages = ref([]) // [{role: 'user'|'assistant', text}]
const question = ref('')
const asking = ref(false)
const qaError = ref('')
const qaBox = ref(null)

async function sendQuestion() {
  const q = question.value.trim()
  if (!q || asking.value) return
  messages.value.push({ role: 'user', text: q })
  question.value = ''
  asking.value = true
  qaError.value = ''
  try {
    const { answer } = await askQuestion(props.task.task_id, q)
    messages.value.push({ role: 'assistant', text: answer })
  } catch (e) {
    // 429 (每日问答配额用尽) 等: 保留问题, 提示重试或明日再问
    qaError.value = e.message || '提问失败, 请稍后重试'
  } finally {
    asking.value = false
    nextTick(() => {
      qaBox.value?.scrollTo({ top: qaBox.value.scrollHeight, behavior: 'smooth' })
    })
  }
}

// 导出直链 (后端 Content-Disposition attachment 触发下载)
function exportUrl(format) {
  return `/api/tasks/${props.task.task_id}/export?format=${format}`
}
</script>

<template>
  <Teleport to="body">
    <!-- 挂载即展示 (父组件 v-if 控制生命周期) -->
    <div class="summary-overlay" @click.self="close">
      <div class="summary-dialog" role="dialog" aria-modal="true" aria-label="视频总结">
        <button
          class="summary-dialog__close"
          type="button"
          aria-label="关闭"
          @click="close"
        >
          ✕
        </button>

        <!-- 头部: 标题 + 导出 -->
        <header class="summary__head">
          <div class="summary__headline">
            <span class="summary__badge">✨ AI 总结</span>
            <h2 class="summary__title">{{ summary?.title || task.title || '视频总结' }}</h2>
            <p v-if="summary?.duration" class="summary__duration">
              ⏱ {{ fmtTime(summary.duration) }}
            </p>
          </div>
          <div class="summary__export">
            <a
              class="btn-outline-gradient summary__export-btn"
              :href="exportUrl('md')"
              :download="`summary_${task.task_id}.md`"
            >
              ⬇ 导出总结 (Markdown)
            </a>
            <a
              class="btn-outline-gradient summary__export-btn"
              :href="exportUrl('txt')"
              :download="`transcript_${task.task_id}.txt`"
            >
              ⬇ 导出转录 (TXT)
            </a>
          </div>
        </header>

        <!-- 加载中 -->
        <div v-if="loading" class="summary__loading">
          <span class="summary__spinner" aria-hidden="true"></span>
          正在加载总结…
        </div>

        <!-- 结果已过期清理 -->
        <div v-else-if="expired" class="summary__expired">
          <p>总结结果已过期清理 (免费 24h / 会员 72h), 请重新生成</p>
          <button class="btn-gradient summary__expired-close" type="button" @click="close">
            知道了
          </button>
        </div>

        <!-- 加载失败 -->
        <ErrorAlert v-else-if="loadError" :message="loadError" />

        <template v-else-if="summary">
          <div class="summary__body">
            <!-- 概述 -->
            <section class="summary__section">
              <h3 class="summary__section-title">📌 视频概述</h3>
              <p class="summary__overview">{{ summary.summary?.overview }}</p>
            </section>

            <!-- 章节时间线 -->
            <section class="summary__section">
              <h3 class="summary__section-title">⏱ 章节时间线</h3>
              <ul class="timeline">
                <li
                  v-for="(ch, i) in chapters"
                  :key="i"
                  class="timeline__item"
                >
                  <span class="timeline__time">
                    {{ fmtTime(ch.start) }} ~ {{ fmtTime(ch.end) }}
                  </span>
                  <span class="timeline__title">{{ ch.title }}</span>
                </li>
              </ul>
            </section>

            <!-- 思维导图 (CSS 树: 章节为分支, 要点为叶子) -->
            <section class="summary__section">
              <h3 class="summary__section-title">🧠 思维导图</h3>
              <div class="mindmap">
                <div class="mindmap__node mindmap__node--root">📺 {{ summary.title }}</div>
                <div
                  v-for="(ch, i) in chapters"
                  :key="i"
                  class="mindmap__branch"
                >
                  <div class="mindmap__node mindmap__node--branch">
                    <span class="mindmap__time">{{ fmtTime(ch.start) }}</span>
                    {{ ch.title }}
                  </div>
                  <ul class="mindmap__leaves">
                    <li v-for="(p, j) in ch.points" :key="j" class="mindmap__leaf">
                      {{ p }}
                    </li>
                  </ul>
                </div>
              </div>
            </section>

            <!-- 转录全文 (默认折叠) -->
            <section class="summary__section">
              <div class="summary__section-head">
                <h3 class="summary__section-title">📝 转录全文</h3>
                <div class="summary__transcript-actions">
                  <button
                    v-if="transcript && transcript.text"
                    class="summary__link-btn"
                    type="button"
                    @click="copyTranscript"
                  >
                    {{
                      copyState === 'copied'
                        ? '✓ 已复制'
                        : copyState === 'failed'
                          ? '复制失败'
                          : '📋 复制全文'
                    }}
                  </button>
                  <button
                    class="summary__link-btn"
                    type="button"
                    @click="transcriptOpen = !transcriptOpen"
                  >
                    {{ transcriptOpen ? '收起' : `展开 (${transcript?.segments?.length ?? 0} 段)` }}
                  </button>
                </div>
              </div>
              <p v-if="transcriptOpen" class="summary__transcript">
                {{ transcript?.text }}
              </p>
              <p v-else class="summary__transcript-hint">转录文本较长, 点击「展开」查看</p>
            </section>

            <!-- AI 问答 -->
            <section class="summary__section">
              <h3 class="summary__section-title">💬 AI 问答</h3>
              <p class="summary__qa-hint">
                针对视频内容提问 (基于转录 + 总结上下文, 免费档每日限 10 次)
              </p>
              <div ref="qaBox" class="qa">
                <div
                  v-for="(msg, i) in messages"
                  :key="i"
                  class="qa__msg"
                  :class="msg.role === 'user' ? 'qa__msg--user' : 'qa__msg--assistant'"
                >
                  {{ msg.text }}
                </div>
                <div v-if="asking" class="qa__msg qa__msg--assistant">
                  <span class="summary__spinner summary__spinner--small" aria-hidden="true"></span>
                  思考中…
                </div>
                <p v-if="messages.length === 0 && !asking" class="qa__empty">
                  还没有提问, 试试问「核心知识点有哪些?」
                </p>
              </div>
              <ErrorAlert :message="qaError" />
              <form class="qa__form" @submit.prevent="sendQuestion">
                <input
                  v-model="question"
                  class="qa__input"
                  type="text"
                  placeholder="输入关于视频内容的问题…"
                  :disabled="asking"
                />
                <button class="btn-gradient qa__send" type="submit" :disabled="asking">
                  {{ asking ? '提问中…' : '提问' }}
                </button>
              </form>
            </section>
          </div>
        </template>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
/* 遮罩 + 弹窗 (与 MemberSection 同模式: Teleport + overlay + dialog) */
.summary-overlay {
  position: fixed;
  inset: 0;
  z-index: 1000;
  display: flex;
  align-items: center;
  justify-content: center;
  padding: 20px;
  background: rgba(31, 35, 41, 0.45);
  backdrop-filter: blur(2px);
}

.summary-dialog {
  position: relative;
  width: 100%;
  max-width: 860px;
  max-height: 88vh;
  overflow-y: auto;
  padding: 30px 28px 28px;
  border-radius: var(--radius);
  background-color: #ffffff;
  background-image: radial-gradient(
    420px 220px at 90% 0%,
    rgba(251, 114, 153, 0.12),
    transparent 65%
  );
  border: 2px solid var(--blue);
  box-shadow: var(--shadow-card);
  animation: dialog-in 0.25s ease both;
}

@keyframes dialog-in {
  from {
    opacity: 0;
    transform: translateY(14px) scale(0.97);
  }
  to {
    opacity: 1;
    transform: translateY(0) scale(1);
  }
}

.summary-dialog__close {
  position: absolute;
  top: 14px;
  right: 14px;
  width: 30px;
  height: 30px;
  border: none;
  border-radius: 50%;
  font-size: 13px;
  color: var(--text-dim);
  background: rgba(31, 35, 41, 0.06);
  cursor: pointer;
  transition:
    color 0.2s ease,
    background 0.2s ease;
}

.summary-dialog__close:hover {
  color: var(--text-main);
  background: rgba(31, 35, 41, 0.12);
}

/* 头部 */
.summary__head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
  padding-right: 34px;
}

.summary__headline {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.summary__badge {
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--gradient);
}

.summary__title {
  font-size: 19px;
  font-weight: 700;
}

.summary__duration {
  font-size: 13px;
  color: var(--text-sub);
}

.summary__export {
  display: flex;
  gap: 10px;
}

.summary__export-btn {
  padding: 7px 16px;
  font-size: 12px;
  white-space: nowrap;
}

/* 加载中 */
.summary__loading {
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 10px;
  padding: 60px 0;
  color: var(--text-sub);
  font-size: 14px;
}

.summary__spinner {
  width: 16px;
  height: 16px;
  border: 2px solid rgba(31, 35, 41, 0.3);
  border-top-color: #1f2329;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

.summary__spinner--small {
  width: 12px;
  height: 12px;
  display: inline-block;
  vertical-align: -2px;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* 已过期 */
.summary__expired {
  padding: 60px 0;
  text-align: center;
  color: var(--danger);
  font-size: 14px;
}

.summary__expired-close {
  margin-top: 16px;
  padding: 8px 28px;
  font-size: 14px;
}

/* 内容区 */
.summary__body {
  margin-top: 22px;
  display: flex;
  flex-direction: column;
  gap: 24px;
}

.summary__section-title {
  font-size: 15px;
  font-weight: 700;
  margin-bottom: 10px;
}

.summary__section-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.summary__section-head .summary__section-title {
  margin-bottom: 0;
}

.summary__overview {
  font-size: 14px;
  line-height: 1.7;
  color: var(--text-main);
  white-space: pre-line;
}

/* 章节时间线 */
.timeline {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.timeline__item {
  display: flex;
  align-items: baseline;
  gap: 12px;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  background: rgba(31, 35, 41, 0.03);
}

.timeline__time {
  flex: 0 0 auto;
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--primary);
  white-space: nowrap;
}

.timeline__title {
  font-size: 14px;
  font-weight: 600;
}

/* 思维导图 (CSS 树: 根 → 分支 → 叶子, 左侧竖线连接) */
.mindmap {
  display: flex;
  flex-direction: column;
  gap: 0;
  padding-left: 18px;
  border-left: 2px solid rgba(0, 174, 236, 0.25);
}

.mindmap__node {
  font-size: 14px;
}

.mindmap__node--root {
  align-self: flex-start;
  margin-left: -18px;
  padding: 8px 16px;
  border-radius: 999px;
  color: #fff;
  background: var(--gradient);
  font-weight: 700;
  border-left: none;
}

.mindmap__branch {
  margin-left: 4px;
  padding-top: 14px;
  position: relative;
}

.mindmap__branch::before {
  content: '';
  position: absolute;
  left: -18px;
  top: 0;
  width: 14px;
  height: 1px;
  background: rgba(0, 174, 236, 0.4);
}

.mindmap__node--branch {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  padding: 7px 14px;
  border-radius: var(--radius-sm);
  background: var(--primary-soft);
  color: var(--primary);
  font-weight: 600;
}

.mindmap__time {
  font-family: var(--font-mono);
  font-size: 12px;
  color: var(--text-dim);
}

.mindmap__leaves {
  list-style: none;
  margin: 10px 0 0 18px;
  padding-left: 16px;
  border-left: 1px dashed rgba(31, 35, 41, 0.2);
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.mindmap__leaf {
  position: relative;
  padding: 6px 12px;
  font-size: 13px;
  color: var(--text-sub);
  background: rgba(31, 35, 41, 0.03);
  border-radius: var(--radius-sm);
}

.mindmap__leaf::before {
  content: '';
  position: absolute;
  left: -16px;
  top: 50%;
  width: 12px;
  height: 1px;
  background: rgba(31, 35, 41, 0.2);
}

/* 转录全文 */
.summary__transcript-actions {
  display: flex;
  gap: 12px;
}

.summary__link-btn {
  font-size: 12px;
  color: var(--blue);
  cursor: pointer;
}

.summary__link-btn:hover {
  text-decoration: underline;
}

.summary__transcript {
  max-height: 320px;
  overflow-y: auto;
  padding: 14px;
  border-radius: var(--radius-sm);
  background: rgba(31, 35, 41, 0.03);
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
}

.summary__transcript-hint {
  font-size: 13px;
  color: var(--text-dim);
}

/* AI 问答 */
.summary__qa-hint {
  font-size: 12px;
  color: var(--text-dim);
  margin-bottom: 10px;
}

.qa {
  display: flex;
  flex-direction: column;
  gap: 8px;
  max-height: 300px;
  overflow-y: auto;
  padding: 4px 2px;
}

.qa__msg {
  max-width: 85%;
  padding: 10px 14px;
  border-radius: var(--radius-sm);
  font-size: 14px;
  line-height: 1.6;
  white-space: pre-wrap;
}

.qa__msg--user {
  align-self: flex-end;
  color: #fff;
  background: var(--gradient);
}

.qa__msg--assistant {
  align-self: flex-start;
  color: var(--text-main);
  background: rgba(31, 35, 41, 0.06);
}

.qa__empty {
  font-size: 13px;
  color: var(--text-dim);
  padding: 20px 0;
  text-align: center;
}

.qa__form {
  margin-top: 12px;
  display: flex;
  gap: 10px;
}

.qa__input {
  flex: 1;
  min-width: 0;
  height: 42px;
  padding: 0 16px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-deep);
  color: var(--text-main);
  font-size: 14px;
  outline: none;
}

.qa__input:focus {
  box-shadow: 0 0 0 2px rgba(0, 174, 236, 0.35);
}

.qa__input:disabled {
  opacity: 0.6;
}

.qa__send {
  height: 42px;
  padding: 0 24px;
  font-size: 14px;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .summary-dialog {
    padding: 26px 16px 20px;
  }

  .summary__export {
    width: 100%;
  }

  .summary__export-btn {
    flex: 1;
    text-align: center;
  }
}
</style>
