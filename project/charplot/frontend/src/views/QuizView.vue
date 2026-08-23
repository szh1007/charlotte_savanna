<script setup lang="ts">
// 闯关答题页 (Issue 05, PRD D-2~D-5): 答题 → 即时反馈 (讲解/来源) →
// 答错扣心 (温和鼓励动画) → 通关结算 (XP/币/连胜/彩花) / 5 心扣完重开.
// 断点续答: 每次进入/下一题都从后端拉取当前题 (current_index 定位),
// 中途退出再进自动续答; 进度/剩余心持久化在后端 (charplot_level).
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  answerQuestion,
  getLevel,
  restartLevel,
  type AnswerResult,
  type LevelDetail,
} from '@/api/client'
import Confetti from '@/components/Confetti.vue'
import HeartsBar from '@/components/HeartsBar.vue'
import QuestionCard from '@/components/QuestionCard.vue'
import { useAuth } from '@/stores/auth'

const route = useRoute()
const router = useRouter()
const { refreshProfile } = useAuth()
const journeyId = computed(() => Number(route.params.id))
const levelId = computed(() => Number(route.params.levelId))

type Phase = 'loading' | 'answering' | 'feedback' | 'cleared' | 'failed'

const detail = ref<LevelDetail | null>(null)
const phase = ref<Phase>('loading')
const result = ref<AnswerResult | null>(null)
const submitting = ref(false)
const confettiBurst = ref(0)

// ---- 作答计时 (秒, 宽松参考; 提交后落库) ----
let startedAt = Date.now()
watch(
  () => detail.value?.question?.id,
  () => {
    startedAt = Date.now()
  },
)
function elapsedSeconds() {
  return Math.round((Date.now() - startedAt) / 1000)
}

const hearts = computed(() => result.value?.hearts ?? detail.value?.hearts ?? 0)
const questionCount = computed(() => detail.value?.question_count ?? 0)
const currentIndex = computed(() => detail.value?.current_index ?? 0)

/** 题目进度文案: 已答完 N 题 / 共 M 题 (前端展示为「第 N+1 题」). */
const questionLabel = computed(() => {
  const idx = Math.min(currentIndex.value + 1, questionCount.value)
  return `第 ${idx} 题 / 共 ${questionCount.value} 题`
})

async function loadLevel() {
  phase.value = 'loading'
  try {
    detail.value = await getLevel(levelId.value)
    // 已通关 / 心扣完 → 直接进入对应视图, 不重复答题
    if (detail.value.status === 'cleared') phase.value = 'cleared'
    else if (detail.value.status === 'failed') phase.value = 'failed'
    else phase.value = 'answering'
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '关卡加载失败, 请稍后重试')
    phase.value = 'loading'
  }
}

async function onSubmit(answer: number[] | string[]) {
  const question = detail.value?.question
  if (!question || submitting.value) return
  submitting.value = true
  try {
    result.value = await answerQuestion(levelId.value, {
      question_id: question.id,
      answer,
      duration: elapsedSeconds(),
    })
    detail.value = { ...detail.value!, hearts: result.value.hearts }
    // 答对彩花小庆祝; 答错不触发 (温和鼓励, 非错误反馈)
    if (result.value.correct) confettiBurst.value += 1
    phase.value = 'feedback'
    // 答题即改动游戏化状态 (XP/心/连胜), 同步导航徽章 (PRD A-2 实时同步)
    refreshProfile().catch(() => {})
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '提交失败, 请稍后重试')
    // 题目不匹配等 400: 重新拉取当前题 (进度可能已被推进)
    if (e instanceof ApiError) await loadLevel()
  } finally {
    submitting.value = false
  }
}

/** 下一题 / 结算跳转: 后端已推进进度, 重新拉取当前题. */
async function nextStep() {
  if (result.value?.cleared) {
    phase.value = 'cleared'
    confettiBurst.value += 1
    return
  }
  if (result.value?.level_status === 'failed') {
    phase.value = 'failed'
    return
  }
  await loadLevel()
}

async function onRestart() {
  try {
    detail.value = await restartLevel(levelId.value)
    result.value = null
    phase.value = 'answering'
    // 重开重置 profile.hearts, 同步导航徽章
    refreshProfile().catch(() => {})
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '重开失败, 请稍后重试')
  }
}

const sources = computed(() => result.value?.sources ?? [])

onMounted(loadLevel)
</script>

<template>
  <div class="quiz-page">
    <Confetti :burst="confettiBurst" />

    <!-- 顶部: 返回 + 章节/知识点 + 进度 + 心动值 -->
    <div class="quiz-head">
      <router-link :to="`/journeys/${journeyId}/levels`" class="back">
        ← 返回关卡列表
      </router-link>
      <div v-if="detail" class="quiz-meta">
        <div class="meta-text">
          <span class="chapter-tag">{{ detail.chapter_title }}</span>
          <h1 class="kp-title">{{ detail.kp_title }}</h1>
        </div>
        <div v-if="phase === 'answering' || phase === 'feedback'" class="meta-side">
          <span class="q-progress">{{ questionLabel }}</span>
          <HeartsBar :hearts="hearts" />
        </div>
      </div>
    </div>

    <!-- 加载中 -->
    <section v-if="phase === 'loading'" class="panel">
      <el-skeleton :rows="4" animated />
    </section>

    <!-- 答题 / 反馈 -->
    <section v-else-if="detail && (phase === 'answering' || phase === 'feedback')" class="quiz-main">
      <QuestionCard
        v-if="detail.question"
        :question="detail.question"
        :submitted="phase === 'feedback'"
        :is-correct="result?.correct ?? true"
        @submit="onSubmit"
      />

      <!-- 即时反馈区 (答对答错均展示讲解 + 来源引用, PRD D-4) -->
      <div v-if="phase === 'feedback' && result" class="feedback" :class="result.correct ? 'is-right' : 'is-wrong'">
        <div class="feedback-emoji" aria-hidden="true">
          {{ result.correct ? '🎉' : '🌱' }}
        </div>
        <h3 class="feedback-title">
          {{ result.correct ? '答对了, 继续加油!' : '没关系, 记住这个点就好' }}
        </h3>
        <p class="feedback-explanation">{{ result.explanation || '暂无讲解' }}</p>

        <!-- 来源引用位: 真实知识源 (Issue 08) 填充前显示占位 -->
        <div v-if="sources.length" class="sources">
          <span class="sources-label">来源:</span>
          <a
            v-for="(src, i) in sources"
            :key="i"
            class="source-link"
            :href="src"
            target="_blank"
            rel="noopener noreferrer"
          >
            {{ src }}
          </a>
        </div>
        <p v-else class="sources-placeholder">来源引用将在接入真实知识源后显示</p>

        <!-- 通关: 查看结算; 进行中: 下一题; 心扣完: 由 failed-card 提供重开 -->
        <el-button
          v-if="result.level_status !== 'failed'"
          type="primary"
          size="large"
          round
          class="next-btn"
          @click="nextStep"
        >
          {{ result.cleared ? '查看结算 →' : '下一题 →' }}
        </el-button>
      </div>

      <!-- 扣完心: 温和鼓励 + 重开 -->
      <div v-if="result?.level_status === 'failed'" class="failed-card">
        <p class="failed-emoji" aria-hidden="true">💗</p>
        <h3 class="failed-title">心都飞走啦</h3>
        <p class="failed-detail">这一关先休息一下, 重新开始会满血满心, 历史答题记录会保留。</p>
        <el-button type="primary" size="large" round @click="onRestart">
          重新开始本关
        </el-button>
      </div>
    </section>

    <!-- 心扣完 (进入关卡时 status=failed): 本关失败需重开 -->
    <section v-else-if="detail && phase === 'failed'" class="panel failed-card">
      <p class="failed-emoji" aria-hidden="true">💗</p>
      <h3 class="failed-title">心都飞走啦</h3>
      <p class="failed-detail">这一关先休息一下, 重新开始会满血满心, 历史答题记录会保留。</p>
      <el-button type="primary" size="large" round @click="onRestart">
        重新开始本关
      </el-button>
    </section>

    <!-- 通关结算 (PRD D-5): 彩花 + XP/币/连胜 -->
    <section v-else-if="detail && phase === 'cleared'" class="panel settle-card">
      <p class="settle-emoji" aria-hidden="true">🎊</p>
      <h2 class="settle-title">关卡通关!</h2>
      <p class="settle-sub">{{ detail.kp_title }} · 已点亮技能树节点</p>

      <template v-if="result?.reward">
        <div class="reward-grid">
          <div class="reward-item">
            <span class="reward-num">+{{ result.reward.xp }}</span>
            <span class="reward-label">经验</span>
          </div>
          <div class="reward-item">
            <span class="reward-num">+{{ result.reward.coins }}</span>
            <span class="reward-label">学习币</span>
          </div>
          <div class="reward-item">
            <span class="reward-num">{{ result.reward.streak }}</span>
            <span class="reward-label">连胜天数</span>
          </div>
        </div>
        <p v-if="result.reward.journey_cleared" class="journey-cleared">
          🏆 旅程全部关卡通关!
        </p>
      </template>
      <p v-else class="settle-sub">本关已完成, 可以去地图查看点亮效果。</p>

      <div class="settle-actions">
        <el-button size="large" round @click="router.push(`/journeys/${journeyId}/levels`)">
          关卡列表
        </el-button>
        <el-button
          type="primary"
          size="large"
          round
          @click="router.push(`/journeys/${journeyId}/map`)"
        >
          返回闯关地图
        </el-button>
      </div>
    </section>

    <!-- 异常: 关卡不存在 -->
    <section v-else class="panel">
      <p class="empty-emoji" aria-hidden="true">😿</p>
      <h2 class="settle-title">关卡不存在或已删除</h2>
      <el-button round @click="router.push(`/journeys/${journeyId}/levels`)">
        返回关卡列表
      </el-button>
    </section>
  </div>
</template>

<style scoped>
.quiz-page {
  max-width: 680px;
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

/* ---- 头部 ---- */
.quiz-head {
  margin-bottom: 20px;
}

.quiz-meta {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.chapter-tag {
  display: inline-block;
  font-size: 12px;
  font-weight: 600;
  color: var(--cp-primary);
  background: var(--cp-primary-soft);
  border-radius: 999px;
  padding: 2px 12px;
  margin-bottom: 8px;
}

.kp-title {
  font-size: 24px;
  font-weight: 800;
  margin: 0;
  color: var(--cp-ink);
}

.meta-side {
  display: flex;
  align-items: center;
  gap: 16px;
}

.q-progress {
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink-soft);
}

/* ---- 反馈区 ---- */
.feedback {
  margin-top: 16px;
  border-radius: var(--cp-radius);
  padding: 22px 26px;
  border: 1.5px solid transparent;
}

.feedback.is-right {
  background: #f0fbf6;
  border-color: rgba(52, 201, 142, 0.35);
}

.feedback.is-wrong {
  background: #fffaf0;
  border-color: rgba(245, 166, 35, 0.35);
}

.feedback-emoji {
  font-size: 34px;
  margin-bottom: 4px;
}

.feedback-title {
  font-size: 17px;
  font-weight: 800;
  margin: 0 0 8px;
  color: var(--cp-ink);
}

.feedback-explanation {
  font-size: 14px;
  line-height: 1.7;
  color: var(--cp-ink);
  margin: 0 0 12px;
  white-space: pre-wrap;
}

.sources-label {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin-right: 8px;
}

.source-link {
  display: block;
  font-size: 12px;
  color: var(--cp-accent-sky);
  text-decoration: none;
  word-break: break-all;
  margin: 2px 0;
}

.source-link:hover {
  color: var(--cp-primary);
}

.sources-placeholder {
  font-size: 12px;
  color: var(--cp-ink-soft);
  margin: 0;
}

.next-btn {
  margin-top: 14px;
  width: 100%;
}

/* ---- 心扣完 ---- */
.failed-card {
  margin-top: 16px;
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 30px 26px;
  text-align: center;
}

.failed-emoji {
  font-size: 38px;
  margin: 0 0 6px;
}

.failed-title {
  font-size: 18px;
  font-weight: 800;
  margin: 0 0 8px;
  color: var(--cp-ink);
}

.failed-detail {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 18px;
}

/* ---- 结算 ---- */
.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 36px 28px;
  text-align: center;
}

.settle-emoji {
  font-size: 52px;
  margin: 0 0 6px;
  animation: settlePop 0.6s ease-out;
}

@keyframes settlePop {
  0% {
    transform: scale(0.4);
    opacity: 0;
  }
  70% {
    transform: scale(1.15);
  }
  100% {
    transform: scale(1);
    opacity: 1;
  }
}

.settle-title {
  font-size: 26px;
  font-weight: 800;
  margin: 0 0 6px;
  color: var(--cp-ink);
}

.settle-sub {
  font-size: 13px;
  color: var(--cp-ink-soft);
  margin: 0 0 22px;
}

.reward-grid {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 18px;
}

.reward-item {
  background: var(--cp-primary-soft);
  border-radius: var(--cp-radius-sm);
  padding: 14px 8px;
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.reward-num {
  font-size: 24px;
  font-weight: 900;
  color: var(--cp-primary);
}

.reward-label {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.journey-cleared {
  font-size: 14px;
  font-weight: 700;
  color: var(--cp-warn);
  margin: 0 0 18px;
}

.settle-actions {
  display: flex;
  justify-content: center;
  gap: 10px;
  flex-wrap: wrap;
}

.empty-emoji {
  font-size: 40px;
  margin: 0 0 8px;
}
</style>
