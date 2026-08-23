<script setup lang="ts">
// 旅程详情 (Issue 03): 生成中 → SSE 五阶段进度 (失败可重试);
// 已就绪 → 图谱展示 (章节 + 知识点 + 前置依赖 chips, 技能树可视化留 Issue 04).
// 视觉遵循 /frontend-design: 进度 stepper 为签名元素 (五阶段点亮动画).
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  startPipeline,
  subscribePipeline,
  type JourneyDetail,
  type PipelineEvent,
  type KnowledgePoint,
} from '@/api/client'
import { useJourneys } from '@/stores/journeys'

const route = useRoute()
const router = useRouter()
const { state, loadJourney } = useJourneys()

const journeyId = computed(() => Number(route.params.id))
const detail = computed<JourneyDetail | null>(() => state.current)

// ---- SSE 进度状态 ----
const STAGES = [
  { key: 'parsing', label: '解析' },
  { key: 'analyzing', label: '分析' },
  { key: 'searching', label: '搜索' },
  { key: 'deconstructing', label: '解构' },
  { key: 'done', label: '完成' },
]
const currentStage = ref('')
const progress = ref(0)
const stageMessage = ref('')
const sseState = ref<'idle' | 'connecting' | 'reconnecting' | 'closed'>('idle')
const taskId = ref('')
const closeSse = ref<(() => void) | null>(null)

const stageIndex = computed(() => {
  const idx = STAGES.findIndex((s) => s.key === currentStage.value)
  return idx === -1 ? 0 : idx
})

const progressPercent = computed(() => (sseState.value === 'idle' ? 0 : progress.value))

/** 依赖边标题映射: prerequisites (DB 主键) → 知识点标题. */
const kpById = computed<Map<number, KnowledgePoint>>(() => {
  const map = new Map<number, KnowledgePoint>()
  for (const chapter of detail.value?.chapters ?? []) {
    for (const kp of chapter.knowledge_points) map.set(kp.id, kp)
  }
  return map
})

function beginSse(nextTaskId: string) {
  taskId.value = nextTaskId
  closeSse.value?.()
  sseState.value = 'connecting'
  closeSse.value = subscribePipeline(nextTaskId, {
    onEvent: (ev: PipelineEvent) => {
      currentStage.value = ev.stage
      progress.value = ev.progress
      stageMessage.value = ev.message
      if (ev.stage === 'done' || ev.stage === 'error') {
        // 任务结束 → 重载详情 (ready 图谱 / failed 错误信息)
        loadJourney(journeyId.value).catch(() => {})
      }
    },
    onStateChange: (s) => {
      sseState.value = s
    },
  })
}

/** 启动/重试管道 (首次进入 / 失败重试 / 任务丢失重新生成共用). */
async function runPipeline() {
  const d = detail.value
  if (!d) return
  try {
    const { task_id } = await startPipeline(d.id, d.input_type, d.content || undefined)
    beginSse(task_id)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '启动生成失败, 请稍后重试')
  }
}

const sseVisible = computed(() => sseState.value !== 'idle')

onMounted(async () => {
  const d = await loadJourney(journeyId.value).catch(() => null)
  if (!d) return
  if (d.status === 'generating') {
    // 创建后跳转带 task_id; 刷新后丢失 → 显示「任务已丢失」由用户重新生成
    const qTaskId = route.query.task_id
    if (typeof qTaskId === 'string' && qTaskId) {
      beginSse(qTaskId)
    }
  }
})

onUnmounted(() => {
  closeSse.value?.() // 必须断开 EventSource, 页面离开不泄漏
})
</script>

<template>
  <div class="detail">
    <router-link to="/" class="back">← 返回旅程列表</router-link>

    <template v-if="detail">
      <header class="detail-head">
        <h1 class="detail-title">{{ detail.title }}</h1>
        <p class="detail-meta">
          {{ { text: '一句话', link: '网页链接', file: '文件' }[detail.input_type] }}
          · 创建于 {{ new Date(detail.created_at).toLocaleDateString() }}
        </p>
      </header>

      <!-- 生成中: SSE 进度 -->
      <section v-if="detail.status === 'generating'" class="panel progress-panel">
        <template v-if="sseVisible">
          <ol class="stepper" aria-label="生成进度">
            <li
              v-for="(s, i) in STAGES"
              :key="s.key"
              class="step"
              :class="{
                'step-done': i < stageIndex,
                'step-active': i === stageIndex,
              }"
            >
              <span class="step-dot" aria-hidden="true">
                <span v-if="i < stageIndex" class="step-check">✓</span>
                <span v-else-if="i === stageIndex" class="step-pulse" aria-hidden="true"></span>
                {{ i + 1 }}
              </span>
              <span class="step-label">{{ s.label }}</span>
            </li>
          </ol>

          <el-progress
            :percentage="progressPercent"
            :stroke-width="10"
            class="progress-bar"
          />
          <p class="stage-message">{{ stageMessage }}</p>
          <p v-if="sseState === 'reconnecting'" class="sse-tip">连接中断，正在重连…</p>
          <p v-else-if="sseState === 'closed'" class="sse-tip">连接失败</p>
        </template>

        <template v-else>
          <p class="stage-message">生成任务已丢失（服务可能重启过）。</p>
          <el-button type="primary" round @click="runPipeline">重新生成</el-button>
        </template>
      </section>

      <!-- 生成失败: 错误 + 重试 -->
      <section v-else-if="detail.status === 'failed'" class="panel fail-panel">
        <p class="fail-emoji" aria-hidden="true">💔</p>
        <h2 class="fail-title">图谱生成失败</h2>
        <p class="fail-detail">{{ detail.error_message || '未知错误' }}</p>
        <el-button type="primary" round @click="runPipeline">重试生成</el-button>
      </section>

      <!-- 已就绪: 图谱展示 + 闯关地图入口 (Issue 04) / 复盘报告入口 (Issue 06) -->
      <section v-else class="graph">
        <div class="graph-head">
          <h2 class="graph-title">知识图谱</h2>
          <div class="graph-actions">
            <router-link v-if="detail.cleared" :to="`/journeys/${detail.id}/report`">
              <el-button type="primary" plain round>📜 复盘报告</el-button>
            </router-link>
            <router-link :to="`/journeys/${detail.id}/map`">
              <el-button type="primary" round>进入闯关地图 →</el-button>
            </router-link>
          </div>
        </div>
        <el-collapse v-if="detail.chapters.length">
          <el-collapse-item
            v-for="(chapter, ci) in detail.chapters"
            :key="chapter.id"
            :title="`${ci + 1}. ${chapter.title}`"
            class="chapter"
          >
            <p v-if="chapter.summary" class="chapter-summary">{{ chapter.summary }}</p>
            <ul class="kp-list">
              <li v-for="kp in chapter.knowledge_points" :key="kp.id" class="kp">
                <div class="kp-main">
                  <h3 class="kp-title">{{ kp.title }}</h3>
                  <p v-if="kp.summary" class="kp-summary">{{ kp.summary }}</p>
                </div>
                <div v-if="kp.prerequisites.length" class="kp-prereqs">
                  <span class="prereq-label">前置：</span>
                  <span
                    v-for="pid in kp.prerequisites"
                    :key="pid"
                    class="prereq-chip"
                  >
                    {{ kpById.get(pid)?.title ?? `#${pid}` }}
                  </span>
                </div>
              </li>
            </ul>
          </el-collapse-item>
        </el-collapse>
        <p v-else class="stage-message">图谱为空，等待后续生成。</p>
      </section>
    </template>

    <section v-else class="panel">
      <p class="stage-message">旅程不存在或已删除。</p>
      <el-button round @click="router.push('/')">返回首页</el-button>
    </section>
  </div>
</template>

<style scoped>
.detail {
  max-width: 720px;
  margin: 0 auto;
}

.back {
  display: inline-block;
  font-size: 13px;
  color: var(--cp-ink-soft);
  text-decoration: none;
  margin-bottom: 18px;
}

.back:hover {
  color: var(--cp-primary);
}

.detail-head {
  margin-bottom: 24px;
}

.detail-title {
  font-size: 28px;
  font-weight: 800;
  margin: 0 0 6px;
  color: var(--cp-ink);
}

.detail-meta {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0;
}

.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 32px;
  text-align: center;
}

/* ---- SSE 进度 (签名元素: 五阶段 stepper) ---- */
.stepper {
  list-style: none;
  margin: 0 0 24px;
  padding: 0;
  display: flex;
  justify-content: space-between;
}

.step {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 8px;
  flex: 1;
}

.step-dot {
  position: relative;
  width: 34px;
  height: 34px;
  border-radius: 50%;
  display: grid;
  place-items: center;
  font-size: 13px;
  font-weight: 700;
  background: var(--cp-primary-soft);
  color: var(--cp-ink-soft);
}

.step-done .step-dot {
  background: var(--cp-ok);
  color: #fff;
}

.step-active .step-dot {
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  color: #fff;
  box-shadow: 0 6px 16px rgba(251, 114, 153, 0.4);
}

.step-check {
  font-size: 14px;
}

.step-pulse {
  position: absolute;
  inset: 0;
  border-radius: 50%;
  animation: pulse 1.4s ease-out infinite;
}

@keyframes pulse {
  0% {
    box-shadow: 0 0 0 0 rgba(251, 114, 153, 0.45);
  }
  100% {
    box-shadow: 0 0 0 14px rgba(251, 114, 153, 0);
  }
}

.step-label {
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink-soft);
}

.step-active .step-label,
.step-done .step-label {
  color: var(--cp-ink);
}

.progress-bar {
  margin-bottom: 14px;
}

.stage-message {
  font-size: 14px;
  color: var(--cp-ink-soft);
  margin: 0 0 12px;
}

.sse-tip {
  font-size: 12px;
  color: var(--cp-warn);
  margin: 0;
}

/* ---- 失败 ---- */
.fail-emoji {
  font-size: 40px;
  margin: 0 0 8px;
}

.fail-title {
  font-size: 18px;
  font-weight: 700;
  margin: 0 0 6px;
}

.fail-detail {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 18px;
}

/* ---- 图谱 ---- */
.graph-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 14px;
}

.graph-actions {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.graph-title {
  font-size: 18px;
  font-weight: 800;
  margin: 0;
}

.chapter {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  margin-bottom: 12px;
  padding: 4px 18px;
}

.chapter-summary {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 12px;
}

.kp-list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 10px;
}

.kp {
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 12px 14px;
}

.kp-title {
  font-size: 14px;
  font-weight: 700;
  margin: 0 0 4px;
  color: var(--cp-ink);
}

.kp-summary {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 0;
}

.kp-prereqs {
  margin-top: 8px;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.prereq-chip {
  display: inline-block;
  background: var(--cp-card);
  color: var(--cp-primary);
  border: 1px solid rgba(251, 114, 153, 0.25);
  border-radius: 999px;
  padding: 1px 10px;
  margin: 2px 4px 2px 0;
}
</style>
