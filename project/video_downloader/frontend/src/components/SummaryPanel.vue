<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, reactive, ref, watch } from 'vue'
import { marked } from 'marked'
import ErrorAlert from './ErrorAlert.vue'
import MindMapCanvas from './MindMapCanvas.vue'
import {
  askQuestionStream,
  exportUrl,
  fetchMindmap,
  fetchSummary,
  fetchTranscript,
  streamSummary,
} from '../api/client.js'

// AI 总结内嵌面板 (取代 SummaryDialog 弹窗): 四标签 (总结/转录/思维导图/问答)
// 由四子任务状态驱动 (task.subtasks, SSE 全量推送), 各 tab 独立进度条与
// 重试入口 (进度条显示在所属 tab 内容区顶部). 子任务独立运行: 转录先完成
// 即可先查看, 单 tab 失败可单独重试.
// 状态机 (后端 Subtask): pending 等待 / running 进行中 / done 完成 /
// failed 失败 / blocked 依赖失败 (重试依赖后自动解锁).
// 数据按子任务完成逐个拉取 (缓存只拉一次): summary / transcript / mindmap.
const props = defineProps({
  // 总结任务 (task_id / title / status / subtasks / error), Home 经 SSE 同步引用
  task: { type: Object, required: true },
})

const emit = defineEmits(['retry'])

// ---- 四标签 ----
const TABS = [
  { key: 'transcript', icon: '📝', label: '字幕' },
  { key: 'summary', icon: '📄', label: '总结' },
  { key: 'mindmap', icon: '🧠', label: '思维导图' },
  { key: 'qa', icon: '💬', label: 'AI 问答' },
]
const activeTab = ref('transcript') // 默认字幕: 转录先完成即可先查看

// 子任务状态快捷读取 (SSE 覆盖 task 引用后自动刷新)
function subStatus(name) {
  return props.task.subtasks?.[name]?.status || 'pending'
}

function subError(name) {
  return props.task.subtasks?.[name]?.error || ''
}

// 全局态: 结果已过期清理 (410 / expired) 提示重新生成
const expired = ref(false)

// 数据缓存 (按子任务完成逐个拉取, 只拉一次; 失败经「重试加载」恢复)
const summary = ref(null) // SummaryOut: {title, duration, summary:{overview, chapters, key_points, conclusion}}
const transcript = ref(null) // TranscriptOut: {text, segments}
const mindmap = ref(null) // MindMapOut: {title, duration, mindmap:{title, chapters}}
const loadErrors = reactive({ transcript: '', summary: '', mindmap: '' })

async function loadSummary() {
  loadErrors.summary = ''
  try {
    summary.value = await fetchSummary(props.task.task_id)
  } catch (e) {
    if (e.status === 410) {
      expired.value = true // 结果已过期清理 (与后端清理竞态)
    } else {
      loadErrors.summary = e.message || '总结加载失败'
    }
  }
}

async function loadTranscript() {
  loadErrors.transcript = ''
  try {
    transcript.value = await fetchTranscript(props.task.task_id)
  } catch (e) {
    if (e.status === 410) {
      expired.value = true
    } else {
      loadErrors.transcript = e.message || '转录加载失败'
    }
  }
}

async function loadMindmap() {
  loadErrors.mindmap = ''
  try {
    mindmap.value = await fetchMindmap(props.task.task_id)
  } catch (e) {
    if (e.status === 410) {
      expired.value = true
    } else {
      loadErrors.mindmap = e.message || '思维导图加载失败'
    }
  }
}

// 子任务完成 → 拉取对应数据 (done 且未缓存; blocked 重试解锁后再 done 仍触发)
watch(
  () => props.task.subtasks,
  () => {
    if (subStatus('transcript') === 'done' && !transcript.value) loadTranscript()
    if (subStatus('summary') === 'done' && !summary.value) loadSummary()
    if (subStatus('mindmap') === 'done' && !mindmap.value) loadMindmap()
  },
  { deep: true, immediate: true },
)

// ---- 总结流式渲染 (ADR-0007/0008): 订阅 /summary/stream 展示 Markdown 文档增量 ----
// 帧协议: snapshot 首帧累积全文 (重连恢复现场, 覆盖旧文本) → delta 追加 →
// done 收尾; 断线且子任务仍 running 时指数退避重连 (1s/2s/4s 封顶 10s,
// snapshot 兜底不丢文本); tab 离开 / 卸载 abort 断开 (fetch 信号取消).
// 流式期间 marked 实时渲染 (ADR-0008), 完成切换完成态 Markdown 无感.
const summaryStreamText = ref('')
const summaryStreamCtrl = ref(null) // 当前流 AbortController
const summaryRetryTimer = ref(null) // 重连定时器 (关闭/卸载时清除)
let summaryRetryMs = 0 // 退避基数 (上次延迟), 成功后归零

function closeSummaryStream() {
  clearTimeout(summaryRetryTimer.value)
  summaryRetryTimer.value = null
  summaryStreamCtrl.value?.abort()
  summaryStreamCtrl.value = null
  summaryRetryMs = 0
}

function scheduleSummaryReconnect() {
  const delay = summaryRetryMs ? Math.min(summaryRetryMs * 2, 10000) : 1000
  summaryRetryMs = delay
  summaryRetryTimer.value = setTimeout(openSummaryStream, delay)
}

function openSummaryStream({ reset = false } = {}) {
  summaryStreamCtrl.value?.abort() // 关闭上一路流 (重连时旧流已断开, abort 无副作用)
  if (reset) summaryStreamText.value = '' // 新任务清空, 重连保留旧文本等 snapshot 覆盖
  const ctrl = new AbortController()
  summaryStreamCtrl.value = ctrl
  streamSummary(props.task.task_id, {
    signal: ctrl.signal,
    onFrame: (event, data) => {
      if (event === 'snapshot' || event === 'delta') {
        summaryStreamText.value += data.text
      } else if (event === 'done') {
        // 流自然结束: 关闭标记 (无重连); 竞态兜底 — done 帧到达时若总结
        // 尚未拉取 (SSE 端点与事件总线竞态), 直接拉取不等 watch 触发
        summaryStreamCtrl.value = null
        summaryRetryMs = 0
        if (!summary.value) loadSummary()
      }
    },
  }).catch(() => {
    // AbortError (主动关闭) 不重连; 仅子任务仍 running 时退避重连
    if (ctrl.signal.aborted) return
    if (subStatus('summary') === 'running' && summaryStreamCtrl.value === ctrl) {
      scheduleSummaryReconnect()
    }
  })
}

// summary 子任务 running → 开流 (打字机); 离开 running (done/failed/blocked)
// → 关闭 (done 后由 Markdown 渲染接管); immediate 恢复刷新页面时的进行中任务
watch(
  () => subStatus('summary'),
  (s) => {
    if (s === 'running') {
      openSummaryStream({ reset: true })
    } else {
      closeSummaryStream()
    }
  },
  { immediate: true },
)

// ---- 重试: failed/blocked 子任务单独重跑 (父组件调后端, 不扣配额) ----
function retry(name) {
  emit('retry', name)
}

// 标签状态指示 (tab 栏圆点): running 转圈 / done ✓ / failed|blocked ✗
function tabStateClass(name) {
  const s = subStatus(name)
  if (s === 'running') return 'is-running'
  if (s === 'done') return 'is-done'
  if (s === 'failed' || s === 'blocked') return 'is-error'
  return ''
}

// 时长格式化: 秒 → "mm:ss" (展示与 Markdown 拼接一致)
function fmtTime(seconds) {
  const total = Math.max(0, Math.round(seconds || 0))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

// ---- Markdown 渲染 (镜像后端 _render_markdown 结构, 展示与导出一致) ----
function buildMarkdown(s) {
  const body = s?.summary || {}
  const lines = [`# 视频总结: ${s?.title || props.task.title || '未知标题'}`]
  if (s?.duration) lines.push(`> 时长: ${Math.round(s.duration)}s`)
  lines.push('', '## 视频概述', body.overview || '', '', '## 章节时间线')
  for (const ch of body.chapters || []) {
    lines.push(`### ${ch.title} (${fmtTime(ch.start)} ~ ${fmtTime(ch.end)})`)
    for (const p of ch.points || []) lines.push(`- ${p}`)
  }
  lines.push('', '## 核心要点')
  for (const k of body.key_points || []) lines.push(`- ${k}`)
  lines.push('', '## 结论', body.conclusion || '')
  return lines.join('\n')
}

// marked 渲染 (LLM 输出 + 内嵌面板, 不做 HTML 消毒; 如需严格消毒可补 DOMPurify)
const mdHtml = computed(() =>
  summary.value ? marked.parse(buildMarkdown(summary.value)) : '',
)
// 流式期间 Markdown 实时渲染 (ADR-0008): 打字机效果, 完成切换 mdHtml 无感
const streamMdHtml = computed(() =>
  summaryStreamText.value ? marked.parse(summaryStreamText.value) : '',
)
// 问答消息 Markdown 渲染 (ADR-0008): assistant 回答实时渲染, user 消息走插值
function renderMarkdown(text) {
  return text ? marked.parse(text) : ''
}

// 转录 tab: 本 tab 内滑动查看 (用户反馈: 复制全文/展开全文按钮已移除,
// 转录文本直接完整展示, 容器内滚动)
// 总结 tab: 全文直接展示 (用户反馈: 收起按钮已移除)

// 从 B 站链接提取 BV 号 (下载文件默认命名, 用户反馈); 短链/av 号无 BV 返回 ''
function bvidOf(url) {
  const m = (url || '').match(/BV[0-9A-Za-z]{10}/)
  return m ? m[0] : ''
}

// 源链接: SSE 事件把 url 键让给文件直链, 源链接走 source_url;
// 列表/本地任务则 url 即源链接 (bugfix: 任务经 SSE 同步后 url 被覆盖,
// bvidOf 取不到 BV 号, 下载文件名退化回 mindmap_标题)
function sourceUrl() {
  return props.task.source_url || props.task.url || ''
}

// 导出文件名: 有 BV 号 → "BV号.扩展名"; 无 BV → 兜底 "{kind}_{task_id}.{ext}" 原命名
function fileName(kind, ext) {
  const b = bvidOf(sourceUrl())
  return b ? `${b}.${ext}` : `${kind}_${props.task.task_id}.${ext}`
}

// ---- PDF 导出: 新窗口渲染 Markdown 后打印 (与 MindMapCanvas.exportPdf 同模式) ----
// 窗口标题默认用 BV 号 (浏览器「另存为 PDF」默认文件名取自 title, 用户反馈)
function exportPdf() {
  if (!summary.value) return
  const win = window.open('', '_blank')
  if (!win) return
  const docTitle = String(
    bvidOf(sourceUrl()) ||
      summary.value.title ||
      props.task.title ||
      '未知标题',
  ).replace(/[<>&"]/g, '')
  win.document.write(
    `<html><head><meta charset="utf-8"><title>${docTitle}</title><style>` +
      `body{font-family:-apple-system,'PingFang SC','Microsoft YaHei',sans-serif;` +
      `line-height:1.8;max-width:720px;margin:0 auto;padding:24px;color:#1f2329}` +
      `h1{font-size:22px}h2{color:#fb7299;font-size:18px;margin-top:24px}` +
      `blockquote{color:#61666d;border-left:3px solid #00aeec;padding-left:12px}` +
      `</style></head><body>${mdHtml.value}</body>` +
      `<script>window.onload=()=>window.print()<\/script></html>`,
  )
  win.document.close()
}

// ---- AI 问答 (免费档每日配额, 429 由后端拒绝并提示) ----
// qa 子任务 done = 上下文 (转录+总结) 就绪, 解锁交互问答
const qaDisabled = computed(() => subStatus('qa') !== 'done')
const messages = ref([]) // [{role: 'user'|'assistant', text}]
const question = ref('')
const asking = ref(false)
const qaError = ref('')
const qaBox = ref(null)
const qaAbort = ref(null) // 当前回答流 AbortController (卸载时 abort)

async function sendQuestion() {
  const q = question.value.trim()
  if (!q || asking.value || qaDisabled.value) return
  messages.value.push({ role: 'user', text: q })
  question.value = ''
  asking.value = true
  qaError.value = ''
  const ctrl = new AbortController()
  qaAbort.value = ctrl
  // 空文本占位消息: 流式增量 (delta 帧) 直接追加, 替代「思考中」spinner (ADR-0007).
  // 流式期间 marked 实时渲染 (ADR-0008); 必须用 reactive 包装: 普通对象
  // push 进 ref 数组后, 原变量改属性不触发响应
  const placeholder = reactive({ role: 'assistant', text: '' })
  messages.value.push(placeholder)
  try {
    await askQuestionStream(props.task.task_id, q, {
      signal: ctrl.signal,
      onFrame: (event, data) => {
        if (event === 'delta') {
          placeholder.text += data.text
        } else if (event === 'error') {
          throw new Error(data.message || '回答失败, 请重试')
        }
      },
    })
  } catch (e) {
    // 主动取消 (tab 切换/卸载) 静默; 否则 429 / LLM 失败: 移除空气泡,
    // 保留问题 (输入框未被清空前的重试入口), 提示稍后重试
    if (!ctrl.signal.aborted) {
      messages.value = messages.value.filter((m) => m !== placeholder)
      qaError.value = e.message || '提问失败, 请稍后重试'
    }
  } finally {
    asking.value = false
    qaAbort.value = null
    nextTick(() => {
      qaBox.value?.scrollTo({ top: qaBox.value.scrollHeight, behavior: 'smooth' })
    })
  }
}

// 导图数据源: mindmap 接口返回 {title, chapters} (LLM 基于总结生成, 结构与
// 总结 chapters 同构; DAG 上 mindmap 依赖 summary, 总结完成才解锁)
const chapters = computed(() => mindmap.value?.mindmap?.chapters || [])
const mindmapTitle = computed(() => mindmap.value?.title || props.task.title || '视频')

// ---- 全屏 (字幕/总结 tab: 操作条 + 内容区整体全屏, 参照思维导图顶部按钮) ----
const fsTargets = { transcript: null, summary: null } // tab key → 容器 el
const fsKey = ref('') // 当前全屏的 tab (控制按钮文案切换)

function toggleFullscreen(key) {
  const el = fsTargets[key]
  if (!el?.requestFullscreen) return
  if (document.fullscreenElement === el) {
    document.exitFullscreen()
  } else {
    el.requestFullscreen()
  }
}

function onFsChange() {
  // Esc / 浏览器退出全屏时 fullscreenElement 离开容器: 按钮文案还原
  const el = document.fullscreenElement
  fsKey.value = Object.keys(fsTargets).find((k) => fsTargets[k] === el) || ''
}

onMounted(() => document.addEventListener('fullscreenchange', onFsChange))
onBeforeUnmount(() => {
  document.removeEventListener('fullscreenchange', onFsChange)
  closeSummaryStream() // 断开总结流 + 清重连定时器
  qaAbort.value?.abort() // 中断进行中的回答流
})
</script>

<template>
  <div class="summary-panel">
    <!-- 结果已过期清理 -->
    <div v-if="expired" class="summary__expired">
      <p>总结结果已过期清理 (免费 24h / 会员 72h), 请重新生成</p>
    </div>

    <!-- 任务整体失败 (全部子任务失败, 无部分结果; 部分完成时各 tab 独立展示错误) -->
    <ErrorAlert
      v-else-if="props.task.status === 'failed' && subStatus('transcript') !== 'done'"
      :message="props.task.error || '总结任务执行失败'"
    />

    <template v-else>
      <!-- 面板头: 「✨ AI 总结」标题 + 四标签同行 (用户反馈: tab 标签放在
           标题右边, 标题由本组件渲染, 空态时由 TaskPanel 渲染) -->
      <header class="panel-header">
        <h4 class="panel-title">✨ AI 总结</h4>
        <!-- 四标签栏: 各自状态指示点 (进行中转圈 / ✓ / ✗) -->
        <nav class="panel-tabs" role="tablist">
          <button
            v-for="t in TABS"
            :key="t.key"
            class="panel-tab"
            :class="[tabStateClass(t.key), { 'is-active': activeTab === t.key }]"
            type="button"
            role="tab"
            :aria-selected="activeTab === t.key"
            @click="activeTab = t.key"
          >
            <span class="panel-tab__icon">{{ t.icon }}</span>
            {{ t.label }}
            <span
              v-if="subStatus(t.key) === 'done'"
              class="panel-tab__dot is-done"
              title="已完成"
            >✓</span>
            <span
              v-else-if="subStatus(t.key) === 'failed' || subStatus(t.key) === 'blocked'"
              class="panel-tab__dot is-error"
              :title="subStatus(t.key) === 'blocked' ? '依赖失败, 可重试' : '失败, 可重试'"
            >✗</span>
            <span
              v-else-if="subStatus(t.key) === 'running'"
              class="panel-tab__dot is-running"
              title="进行中"
            ></span>
          </button>
        </nav>
      </header>

      <div class="summary__body">
        <!-- 转录 (子任务无依赖最先运行: 滑动查看 / 导出 txt|srt|vtt) -->
        <section
          v-show="activeTab === 'transcript'"
          class="tab-pane"
          :ref="(el) => (fsTargets.transcript = el)"
        >
          <!-- 转录进行中: 进度条 + 文案显示在本 tab 内容区顶部 (用户反馈) -->
          <div v-if="subStatus('transcript') === 'running'" class="tab-pane__progress">
            <div class="bar bar--indeterminate"><span class="bar__fill"></span></div>
            <p>字幕获取中......</p>
          </div>
          <div v-else-if="subStatus('transcript') === 'pending'" class="tab-pane__wait">
            等待任务调度…
          </div>
          <div v-else-if="subStatus('transcript') === 'blocked'" class="tab-pane__error">
            <ErrorAlert :message="subError('transcript') || '依赖子任务失败, 等待重试'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('transcript')"
            >
              ↻ 重试
            </button>
          </div>
          <div v-else-if="subStatus('transcript') === 'failed'" class="tab-pane__error">
            <ErrorAlert :message="subError('transcript') || '转录失败'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('transcript')"
            >
              ↻ 重试
            </button>
          </div>
          <template v-else>
            <div v-if="transcript" class="tab-pane__content">
              <!-- 操作条: 全屏 + 下载, 与思维导图工具条同款样式 (白底描边小按钮,
                   全部右对齐; 用户反馈: 按键样式与思维导图一致, 全屏按键放右边) -->
              <div class="tab-pane__head">
                <div class="summary__export-group">
                  <button
                    class="tool-btn"
                    type="button"
                    @click="toggleFullscreen('transcript')"
                  >
                    {{ fsKey === 'transcript' ? '退出全屏' : '⛶ 全屏' }}
                  </button>
                  <a
                    v-for="f in ['txt', 'srt', 'vtt']"
                    :key="f"
                    class="tool-btn"
                    :href="exportUrl(props.task.task_id, f)"
                    :download="fileName('transcript', f)"
                  >
                    ⬇ {{ f.toUpperCase() }}
                  </a>
                </div>
              </div>
              <!-- 本 tab 内滑动查看 (需求: 字幕在本 tab 滑动查看, 直接展示全文) -->
              <div class="summary__transcript">
                {{ transcript.text }}
              </div>
            </div>
            <div v-else-if="loadErrors.transcript" class="tab-pane__error">
              <ErrorAlert :message="loadErrors.transcript" />
              <button
                class="btn-outline-gradient tab-pane__retry"
                type="button"
                @click="loadTranscript"
              >
                ↻ 重试加载
              </button>
            </div>
            <div v-else class="tab-pane__wait">加载中…</div>
          </template>
        </section>

        <!-- 总结 (Markdown 渲染 / 导出 MD + PDF) -->
        <section
          v-show="activeTab === 'summary'"
          class="tab-pane"
          :ref="(el) => (fsTargets.summary = el)"
        >
          <div v-if="subStatus('summary') === 'running'" class="tab-pane__progress">
            <div class="bar bar--indeterminate"><span class="bar__fill"></span></div>
            <p>总结生成中......</p>
            <!-- 流式渲染 (ADR-0008): Markdown 文档增量 marked 实时渲染, 完成切换 Markdown -->
            <div class="md summary__stream" v-html="streamMdHtml"></div>
          </div>
          <div v-else-if="subStatus('summary') === 'pending'" class="tab-pane__wait">
            等待转录完成…
          </div>
          <div v-else-if="subStatus('summary') === 'blocked'" class="tab-pane__error">
            <ErrorAlert :message="subError('summary') || '依赖子任务失败, 等待重试'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('summary')"
            >
              ↻ 重试
            </button>
          </div>
          <div v-else-if="subStatus('summary') === 'failed'" class="tab-pane__error">
            <ErrorAlert :message="subError('summary') || '总结生成失败'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('summary')"
            >
              ↻ 重试
            </button>
          </div>
          <template v-else>
            <div v-if="mdHtml" class="tab-pane__content">
              <div class="tab-pane__head">
                <!-- 与思维导图工具条同款样式: 全屏 + 下载按钮一组 (用户反馈) -->
                <div class="summary__export-group">
                  <button
                    class="tool-btn"
                    type="button"
                    @click="toggleFullscreen('summary')"
                  >
                    {{ fsKey === 'summary' ? '退出全屏' : '⛶ 全屏' }}
                  </button>
                  <a
                    class="tool-btn"
                    :href="exportUrl(props.task.task_id, 'md')"
                    :download="fileName('summary', 'md')"
                  >
                    ⬇ MD
                  </a>
                  <button
                    class="tool-btn"
                    type="button"
                    @click="exportPdf"
                  >
                    ⬇ PDF
                  </button>
                </div>
              </div>
              <div class="md" v-html="mdHtml"></div>
            </div>
            <div v-else-if="loadErrors.summary" class="tab-pane__error">
              <ErrorAlert :message="loadErrors.summary" />
              <button
                class="btn-outline-gradient tab-pane__retry"
                type="button"
                @click="loadSummary"
              >
                ↻ 重试加载
              </button>
            </div>
            <div v-else class="tab-pane__wait">加载中…</div>
          </template>
        </section>

        <!-- 思维导图 (基于总结生成, Canvas: 缩放/平移/全屏/导出 PDF/PNG/SVG) -->
        <section v-show="activeTab === 'mindmap'" class="tab-pane">
          <div v-if="subStatus('mindmap') === 'running'" class="tab-pane__progress">
            <div class="bar bar--indeterminate"><span class="bar__fill"></span></div>
            <p>思维导图生成中......</p>
          </div>
          <div v-else-if="subStatus('mindmap') === 'pending'" class="tab-pane__wait">
            等待总结完成…
          </div>
          <div v-else-if="subStatus('mindmap') === 'blocked'" class="tab-pane__error">
            <ErrorAlert :message="subError('mindmap') || '依赖子任务失败, 等待重试'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('mindmap')"
            >
              ↻ 重试
            </button>
          </div>
          <div v-else-if="subStatus('mindmap') === 'failed'" class="tab-pane__error">
            <ErrorAlert :message="subError('mindmap') || '思维导图生成失败'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('mindmap')"
            >
              ↻ 重试
            </button>
          </div>
          <template v-else>
            <!-- 激活本 tab 且数据就绪才挂载: 渲染 SDK 需要可见容器
                 (v-show 隐藏时初始化会量到 0 尺寸, 图空白但导出正常, 用户反馈) -->
            <MindMapCanvas
              v-if="activeTab === 'mindmap' && chapters.length"
              :title="mindmapTitle"
              :chapters="chapters"
              :filename="bvidOf(sourceUrl())"
            />
            <div v-else-if="loadErrors.mindmap" class="tab-pane__error">
              <ErrorAlert :message="loadErrors.mindmap" />
              <button
                class="btn-outline-gradient tab-pane__retry"
                type="button"
                @click="loadMindmap"
              >
                ↻ 重试加载
              </button>
            </div>
            <div v-else class="tab-pane__wait">暂无思维导图数据</div>
          </template>
        </section>

        <!-- AI 问答 (qa 子任务 done = 上下文就绪, 交互式问答) -->
        <section v-show="activeTab === 'qa'" class="tab-pane">
          <template v-if="subStatus('qa') === 'done'">
            <p class="summary__qa-hint">
              针对视频内容提问 (基于转录 + 总结上下文, 免费档每日限 10 次)
            </p>
            <div ref="qaBox" class="qa">
              <div
                v-for="(msg, i) in messages"
                :key="i"
                class="qa__msg"
                :class="
                  msg.role === 'user' ? 'qa__msg--user' : 'qa__msg--assistant'
                "
              >
                <template v-if="msg.role === 'user'">{{ msg.text }}</template>
                <!-- 回答流式期间实时渲染 Markdown (ADR-0008) -->
                <div
                  v-else
                  class="qa__msg-markdown"
                  v-html="renderMarkdown(msg.text)"
                ></div>
              </div>
              <p v-if="messages.length === 0" class="qa__empty">
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
          </template>
          <div v-else-if="subStatus('qa') === 'running'" class="tab-pane__wait">
            等待总结完成…
          </div>
          <div v-else-if="subStatus('qa') === 'blocked'" class="tab-pane__error">
            <ErrorAlert :message="subError('qa') || '依赖子任务失败, 等待重试'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('qa')"
            >
              ↻ 重试
            </button>
          </div>
          <div v-else-if="subStatus('qa') === 'failed'" class="tab-pane__error">
            <ErrorAlert :message="subError('qa') || '问答上下文生成失败'" />
            <button
              class="btn-outline-gradient tab-pane__retry"
              type="button"
              @click="retry('qa')"
            >
              ↻ 重试
            </button>
          </div>
          <div v-else class="tab-pane__wait">
            总结完成后即可提问
          </div>
        </section>
      </div>
    </template>
  </div>
</template>

<style scoped>
/* 内嵌面板 (无遮罩/弹窗载体): 仅保留面板头 + 内容区样式 */
/* 面板头: 标题与四标签同行, 底线画在 header 上 */
.panel-header {
  display: flex;
  align-items: stretch;
  gap: 16px;
  border-bottom: 1px solid var(--border);
}

/* 标题: 与空态 (TaskPanel) 同款小标题样式, 垂直居中 */
.panel-title {
  font-size: 13px;
  font-weight: 700;
  color: var(--text-sub);
  white-space: nowrap;
  align-self: center;
}

.panel-tabs {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  flex: 1;
  align-self: stretch; /* tab 撑满 header 高度, 下边框对齐 header 底线 */
  margin-bottom: -1px; /* 激活 tab 的 2px 下边框盖住 header 的 1px 线 */
  border-bottom: none;
}

.panel-tab {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 12px;
  border: none;
  border-bottom: 2px solid transparent;
  background: none;
  font-size: 13px;
  font-weight: 600;
  color: var(--text-dim);
  cursor: pointer;
  transition: color 0.2s ease;
}

.panel-tab:hover {
  color: var(--text-main);
}

.panel-tab.is-active {
  color: var(--primary);
  border-bottom-color: var(--primary);
}

.panel-tab__icon {
  font-size: 12px;
}

/* 标签状态指示点 */
.panel-tab__dot {
  width: 14px;
  height: 14px;
  border-radius: 50%;
  font-size: 9px;
  line-height: 14px;
  text-align: center;
}

.panel-tab__dot.is-done {
  color: #fff;
  background: var(--success, #52c41a);
}

.panel-tab__dot.is-error {
  color: #fff;
  background: var(--danger, #ff4d4f);
}

.panel-tab__dot.is-running {
  background: var(--gradient);
  animation: dot-pulse 1s ease-in-out infinite;
}

@keyframes dot-pulse {
  50% {
    opacity: 0.4;
  }
}

/* 内容区 */
.summary__body {
  margin-top: 14px;
}

/* 四 tab 定高一致: 切换 tab 面板上下不跳动 (用户反馈), 内容区内部滚动.
   500px 与思维导图 tab 自然高度 (工具条 + 460px 画布) 同级 */
.tab-pane {
  display: flex;
  flex-direction: column;
  gap: 12px;
  height: 500px;
}

/* 数据就绪态: 操作条 + 内容区占满容器, 内容区内部滚动 */
.tab-pane__content {
  display: flex;
  flex-direction: column;
  gap: 12px;
  flex: 1;
  min-height: 0;
}

.tab-pane__head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
}

.tab-pane__wait {
  flex: 1; /* 定高容器内占满剩余, 各 tab 等待态占位一致 */
  display: flex;
  align-items: flex-start; /* 垂直: 顶部 (用户反馈: 提示词放内容区最上面) */
  justify-content: center; /* 水平: 居中 */
  padding: 16px 0 8px;
  text-align: center;
  font-size: 13px;
  color: var(--text-dim);
}

/* 子任务失败/阻塞: 错误 + 重试按钮 (每 tab 独立) */
.tab-pane__error {
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.tab-pane__retry {
  align-self: flex-start;
  padding: 6px 16px;
  font-size: 12px;
}

/* 各 tab 内加载提示: 显示在内容区顶部 (用户反馈: 提示词放该 tab 区域
   最上面, 不垂直居中; 每个提示词留在属于自己的 tab 页).
   flex:1 + min-height:0: 定高 .tab-pane 内占满剩余, 流式文本容器
   (.summary__stream) 才有高度上限可内部滚动, 不撑出 tab 区域 (ADR-0007) */
.tab-pane__progress {
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px 0 8px;
  flex: 1;
  min-height: 0;
}

.tab-pane__progress p {
  font-size: 13px;
  color: var(--text-sub);
}

.bar {
  position: relative;
  height: 6px;
  border-radius: 999px;
  background: rgba(31, 35, 41, 0.08);
  overflow: hidden;
}

.bar__fill {
  position: absolute;
  top: 0;
  left: 0;
  height: 100%;
  border-radius: 999px;
  background: var(--gradient);
  transition: width 0.3s ease;
}

/* 不定长条: 后端 LLM 子任务 progress=30 仅作「生成中」标记, 不显示静止值 */
.bar--indeterminate .bar__fill {
  width: 30%;
  animation: bar-slide 1.2s ease-in-out infinite;
}

@keyframes bar-slide {
  0% {
    left: -30%;
  }
  100% {
    left: 100%;
  }
}

/* Markdown 渲染 (marked v-html 内容, scoped 样式需 :deep).
   白底描边与思维导图画布一致 (用户反馈: 内容展示区样式边框统一, 圆滑美观) */
.md {
  flex: 1;
  min-height: 0; /* 定高容器内占满剩余, 内部滚动 */
  overflow-y: auto;
  padding: 14px 16px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff;
  font-size: 13px;
  line-height: 1.8;
}

/* 元素排版: 总结 (md) 与问答回答 (qa__msg-markdown) 共用一套 */
.md :deep(h1),
.qa__msg-markdown :deep(h1) {
  font-size: 17px;
  font-weight: 700;
  margin-bottom: 8px;
}

.md :deep(h2),
.qa__msg-markdown :deep(h2) {
  font-size: 15px;
  font-weight: 700;
  margin: 14px 0 6px;
  color: var(--primary);
}

.md :deep(h3),
.qa__msg-markdown :deep(h3) {
  font-size: 13px;
  font-weight: 700;
  margin: 10px 0 4px;
}

.md :deep(blockquote),
.qa__msg-markdown :deep(blockquote) {
  margin: 6px 0;
  padding: 4px 10px;
  border-left: 3px solid var(--blue);
  color: var(--text-sub);
  font-size: 12px;
}

.md :deep(ul),
.qa__msg-markdown :deep(ul) {
  margin: 4px 0;
  padding-left: 18px;
  list-style: disc;
}

.md :deep(li),
.qa__msg-markdown :deep(li) {
  margin: 2px 0;
}

.md :deep(strong),
.qa__msg-markdown :deep(strong) {
  font-weight: 700;
}

.md :deep(p),
.qa__msg-markdown :deep(p) {
  margin: 6px 0;
}

/* 操作条按钮组: 全屏 + 下载, 右对齐 (与思维导图工具条同布局) */
.summary__export-group {
  display: flex;
  gap: 8px;
  margin-left: auto;
}

/* 与思维导图工具条同款按钮样式 (白底描边小按钮, 悬停粉字粉边).
   无 line-height 覆盖, 按钮尺寸与思维导图完全一致 (用户反馈) */
.tool-btn {
  display: inline-block;
  padding: 5px 12px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff;
  font-size: 12px;
  color: var(--text-sub);
  text-decoration: none;
  cursor: pointer;
  transition:
    color 0.2s ease,
    border-color 0.2s ease;
}

.tool-btn:hover {
  color: var(--primary);
  border-color: rgba(251, 114, 153, 0.45);
}

.summary__transcript {
  flex: 1;
  min-height: 0; /* 定高容器内占满剩余, 内部滚动 */
  overflow-y: auto;
  padding: 12px 14px;
  border: 1px solid var(--border);
  border-radius: var(--radius-sm);
  background: #fff; /* 与思维导图画布同款白底描边 */
  font-size: 13px;
  line-height: 1.8;
  white-space: pre-wrap;
}

/* 总结流式渲染 (ADR-0008): 复用 .md 完成态样式, 仅微调进度条下方间距 */
.summary__stream {
  margin-top: 4px;
}

/* 全屏: 操作条 + 内容区整体铺满 (参照思维导图全屏, Esc / 按钮退出) */
.tab-pane:fullscreen {
  height: 100vh;
  padding: 20px;
  background: #fff;
}

/* 已过期 */
.summary__expired {
  padding: 24px 0;
  text-align: center;
  color: var(--danger);
  font-size: 13px;
}

/* AI 问答 */
.summary__qa-hint {
  font-size: 12px;
  color: var(--text-dim);
  margin-bottom: 4px;
}

.qa {
  flex: 1; /* 定高容器内占满剩余 (消息多时内部滚动, 与各 tab 内容区一致) */
  min-height: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
  overflow-y: auto;
  padding: 4px 2px;
}

.qa__msg {
  max-width: 85%;
  padding: 8px 12px;
  border-radius: var(--radius-sm);
  font-size: 13px;
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
  white-space: normal; /* Markdown 渲染接管排版, 关闭基类 pre-wrap (防空行) */
}

.qa__empty {
  font-size: 13px;
  color: var(--text-dim);
  padding: 16px 0;
  text-align: center;
}

.qa__form {
  margin-top: 4px;
  display: flex;
  gap: 10px;
}

.qa__input {
  flex: 1;
  min-width: 0;
  height: 38px;
  padding: 0 14px;
  border-radius: 999px;
  border: 1px solid var(--border);
  background: var(--bg-deep);
  color: var(--text-main);
  font-size: 13px;
  outline: none;
}

.qa__input:focus {
  box-shadow: 0 0 0 2px rgba(0, 174, 236, 0.35);
}

.qa__input:disabled {
  opacity: 0.6;
}

.qa__send {
  height: 38px;
  padding: 0 20px;
  font-size: 13px;
  white-space: nowrap;
}

@media (max-width: 640px) {
  .panel-tab {
    padding: 8px;
    font-size: 12px;
  }
}
</style>
