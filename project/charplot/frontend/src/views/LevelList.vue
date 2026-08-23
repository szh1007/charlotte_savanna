<script setup lang="ts">
// 关卡入口页 (Issue 05/08): 旅程关卡列表 (首次访问后端懒生成关卡骨架).
// Issue 08 渐进生成: 进入页面时对 frontier (第一个未通关关) 及其下一关触发
// 出题生成 (预生成), 生成中订阅 SSE 进度, 失败可重试; Boss 关与解锁状态
// 在卡片上标记. 地图页点击知识点节点时带 query.kp 过滤到该知识点关卡.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  getJourney,
  getLevels,
  startLevelGeneration,
  subscribePipeline,
  type JourneyDetail,
  type LevelSummary,
  type PipelineEvent,
} from '@/api/client'

const route = useRoute()
const router = useRouter()
const journeyId = computed(() => Number(route.params.id))
const focusKp = computed(() => Number(route.query.kp) || 0)

const detail = ref<JourneyDetail | null>(null)
const levels = ref<LevelSummary[]>([])
const loading = ref(true)
// level.id → SSE 关闭函数 (订阅出题生成进度, 卸载时统一清理)
const genSubs = ref<Record<number, () => void>>({})
// level.id → 生成进度 (前端展示)
const genProgress = ref<Record<number, number>>({})

/** query.kp 过滤: 地图页点节点进入只显示该知识点关卡, 无 kp 显示全部. */
const shownLevels = computed(() =>
  focusKp.value
    ? levels.value.filter((l) => l.kp_id === focusKp.value)
    : levels.value,
)

/** 状态标签文案与色系. */
const STATUS_META: Record<string, { label: string; cls: string }> = {
  pending: { label: '待挑战', cls: 'is-pending' },
  in_progress: { label: '进行中', cls: 'is-progress' },
  failed: { label: '心扣完', cls: 'is-failed' },
  cleared: { label: '已通关', cls: 'is-cleared' },
}

/** 每关主按钮文案: 按状态给出动作 (Duolingo 式: 明确下一步). */
function actionLabel(level: LevelSummary) {
  if (level.cleared) return '已通关'
  if (level.status === 'failed') return '重新挑战'
  if (level.status === 'in_progress') return '继续闯关'
  return '开始闯关'
}

function openLevel(level: LevelSummary) {
  if (level.locked || level.questions_status === 'generating') return
  router.push(`/journeys/${journeyId.value}/levels/${level.id}`)
}

async function loadLevels() {
  const res = await getLevels(journeyId.value)
  levels.value = res.levels
}

/** 订阅单关生成进度; done/error → 刷新列表 (失败态在卡片上可重试). */
function subscribeLevelGen(levelId: number, taskId: string) {
  genSubs.value[levelId]?.()
  genProgress.value[levelId] = 0
  genSubs.value[levelId] = subscribePipeline(taskId, {
    onEvent: (ev: PipelineEvent) => {
      if (ev.stage === 'done' || ev.stage === 'error') {
        genSubs.value[levelId]?.()
        delete genSubs.value[levelId]
        delete genProgress.value[levelId]
        loadLevels().catch(() => {})
      } else {
        genProgress.value[levelId] = ev.progress
      }
    },
  })
}

/** 触发生成 (渐进 + 预生成): frontier 及其下一关; 生成中任务续订阅. */
async function ensureGeneration() {
  const sorted = [...levels.value].sort((a, b) => a.seq - b.seq)
  const frontier = sorted.find((l) => l.status !== 'cleared')
  if (!frontier) return
  const targets = sorted.filter(
    (l) => l.seq === frontier.seq || l.seq === frontier.seq + 1,
  )
  for (const level of targets) {
    if (level.questions_status === 'pending' || level.questions_status === 'failed') {
      try {
        const { task_id } = await startLevelGeneration(journeyId.value, level.seq)
        subscribeLevelGen(level.id, task_id)
      } catch {
        /* 生成启动失败静默: 进入关卡时按渐进生成再触发 */
      }
    } else if (level.questions_status === 'generating' && level.latest_task_id) {
      subscribeLevelGen(level.id, level.latest_task_id)
    }
  }
}

/** 生成失败重试 (卡片按钮). */
async function retryGeneration(level: LevelSummary) {
  try {
    const { task_id } = await startLevelGeneration(journeyId.value, level.seq)
    subscribeLevelGen(level.id, task_id)
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '生成启动失败, 请重试')
  }
}

onMounted(async () => {
  try {
    detail.value = await getJourney(journeyId.value)
    if (detail.value.status === 'ready') {
      await loadLevels()
      await ensureGeneration()
    }
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '关卡加载失败, 请稍后重试')
  } finally {
    loading.value = false
  }
})

onUnmounted(() => {
  Object.values(genSubs.value).forEach((close) => close())
})
</script>

<template>
  <div class="levels-page">
    <router-link :to="`/journeys/${journeyId}/map`" class="back">← 返回闯关地图</router-link>

    <header v-if="detail" class="levels-head">
      <h1 class="levels-title">{{ detail.title }} · 关卡</h1>
      <p class="levels-sub">
        {{ focusKp ? '本知识点的闯关' : '全部关卡' }} · 每关 5-8 题, 答错扣心,
        通关点亮技能树
      </p>
    </header>

    <!-- 未就绪 -->
    <section
      v-if="!loading && detail && detail.status !== 'ready'"
      class="empty panel"
    >
      <p class="empty-emoji" aria-hidden="true">🌱</p>
      <h2 class="empty-title">图谱还没就绪</h2>
      <p class="empty-detail">等图谱生成完成后, 这里会出现闯关入口。</p>
      <el-button type="primary" round @click="router.push(`/journeys/${journeyId}`)">
        返回旅程详情
      </el-button>
    </section>

    <!-- 关卡列表 -->
    <section v-else-if="!loading && shownLevels.length" class="level-list">
      <article
        v-for="level in shownLevels"
        :key="level.id"
        class="level-card"
        :class="{
          'is-cleared': level.cleared,
          'is-boss': level.level_type === 'boss',
          'is-locked': level.locked,
        }"
      >
        <div class="level-main">
          <div class="level-tags">
            <span class="chapter-tag">{{ level.chapter_title }}</span>
            <span v-if="level.level_type === 'boss'" class="boss-tag">🏆 Boss</span>
          </div>
          <h2 class="level-kp">{{ level.kp_title }}</h2>
          <div class="level-meta">
            <template v-if="level.questions_status === 'generating'">
              <span>✨ 题目生成中 {{ genProgress[level.id] ?? 0 }}%</span>
            </template>
            <template v-else-if="level.questions_status === 'failed'">
              <span class="gen-failed-text">题目生成失败</span>
            </template>
            <template v-else>
              <span>{{ level.question_count }} 题</span>
              <template v-if="level.cleared">
                <span class="meta-dot" aria-hidden="true">·</span>
                <span>已通关</span>
              </template>
              <template v-else-if="level.current_index > 0">
                <span class="meta-dot" aria-hidden="true">·</span>
                <span>进度 {{ level.current_index }}/{{ level.question_count }}</span>
              </template>
            </template>
            <span class="meta-dot" aria-hidden="true">·</span>
            <span class="meta-hearts" aria-hidden="true">💗 {{ level.hearts }}</span>
            <template v-if="level.locked">
              <span class="meta-dot" aria-hidden="true">·</span>
              <span class="locked-text">🔒 未解锁</span>
            </template>
          </div>
        </div>

        <div class="level-side">
          <span class="status-tag" :class="STATUS_META[level.status]?.cls">
            {{ STATUS_META[level.status]?.label }}
          </span>
          <template v-if="level.questions_status === 'failed' && !level.cleared">
            <el-button type="warning" round @click="retryGeneration(level)">
              生成失败 · 重试
            </el-button>
          </template>
          <el-button
            v-else
            type="primary"
            round
            :disabled="level.cleared || level.locked || level.questions_status === 'generating'"
            @click="openLevel(level)"
          >
            {{ actionLabel(level) }}
          </el-button>
        </div>
      </article>
    </section>

    <!-- 空关卡 -->
    <section v-else-if="!loading" class="empty panel">
      <p class="empty-emoji" aria-hidden="true">🎮</p>
      <h2 class="empty-title">还没有关卡</h2>
      <p class="empty-detail">图谱没有产出知识点, 稍后重试或重新生成。</p>
      <el-button type="primary" round @click="router.push(`/journeys/${journeyId}/map`)">
        返回闯关地图
      </el-button>
    </section>

    <section v-else-if="loading" class="empty panel">
      <el-skeleton :rows="3" animated />
    </section>

    <section v-else class="empty panel">
      <p class="empty-emoji" aria-hidden="true">😿</p>
      <h2 class="empty-title">旅程不存在或已删除</h2>
      <el-button round @click="router.push('/')">返回首页</el-button>
    </section>
  </div>
</template>

<style scoped>
.levels-page {
  max-width: 760px;
  margin: 0 auto;
}

.back {
  display: inline-block;
  font-size: 13px;
  color: var(--cp-ink-soft);
  text-decoration: none;
  margin-bottom: 16px;
}

.back:hover {
  color: var(--cp-primary);
}

.levels-head {
  margin-bottom: 20px;
}

.levels-title {
  font-size: 26px;
  font-weight: 800;
  margin: 0 0 4px;
  color: var(--cp-ink);
}

.levels-sub {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0;
}

/* ---- 关卡卡片 ---- */
.level-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
}

.level-card {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 18px 20px;
  transition:
    transform 0.15s ease,
    box-shadow 0.15s ease;
}

.level-card:hover {
  transform: translateY(-2px);
  box-shadow: var(--cp-shadow-hover);
}

/* 已通关: 柔和降级, 不抢进行中的主角 */
.level-card.is-cleared {
  box-shadow: 0 8px 20px rgba(52, 201, 142, 0.08);
}

/* Boss 关: 高难度标记 (G-5), 弱强调边框 */
.level-card.is-boss {
  border: 1.5px solid rgba(251, 114, 153, 0.28);
}

/* 未解锁: 灰化 */
.level-card.is-locked {
  opacity: 0.55;
}

.level-tags {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-bottom: 6px;
}

.chapter-tag {
  display: inline-block;
  font-size: 11px;
  font-weight: 600;
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
  border-radius: 999px;
  padding: 2px 10px;
}

.boss-tag {
  display: inline-block;
  font-size: 11px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, #fb7299, #f0607e);
  border-radius: 999px;
  padding: 2px 10px;
}

.gen-failed-text,
.locked-text {
  color: var(--cp-warn);
  font-weight: 600;
}

.level-kp {
  font-size: 17px;
  font-weight: 700;
  margin: 0 0 6px;
  color: var(--cp-ink);
}

.level-meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.meta-dot {
  color: #d8d8e0;
}

.meta-hearts {
  font-size: 13px;
}

.level-side {
  display: flex;
  align-items: center;
  gap: 12px;
  flex: none;
}

.status-tag {
  font-size: 12px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 999px;
}

.status-tag.is-pending {
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
}

.status-tag.is-progress {
  color: var(--cp-accent-sky);
  background: #eef7fc;
}

.status-tag.is-failed {
  color: var(--cp-warn);
  background: #fff7e8;
}

.status-tag.is-cleared {
  color: var(--cp-ok);
  background: #eefaf4;
}

/* ---- 空态 ---- */
.empty {
  margin-top: 24px;
}

.empty-emoji {
  font-size: 40px;
  margin: 0 0 8px;
}

.empty-title {
  font-size: 18px;
  font-weight: 700;
  margin: 0 0 6px;
}

.empty-detail {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 18px;
}

.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 32px;
  text-align: center;
}
</style>
