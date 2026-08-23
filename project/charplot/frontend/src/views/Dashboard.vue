<script setup lang="ts">
// 学习分析 Dashboard (Issue 12, SPEC §10): 掌握度矩阵 + 学习活动统计 +
// 易错点清单. 数据全部来自事实表 (Attempt + 用户事件) 按需聚合, 无埋点.
// 视觉 (frontend-design): 延续 B 站粉主题令牌, 掌握度条为签名元素
// (体检报告气质: 绿 → 粉 → 琥珀按正确率分级, 薄弱点红色系胶囊高亮).
// 零图表库: 掌握度条 / 趋势柱均为纯 CSS, 与项目既有做法一致.
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import {
  ApiError,
  generateStatusSummary,
  getActivityStats,
  getMasteryMatrix,
  getWeakpoints,
  type ActivityStats,
  type MasteryJourney,
  type Weakpoint,
} from '@/api/client'
import MarkdownText from '@/components/MarkdownText.vue'
import { useAuth } from '@/stores/auth'

const { state: auth } = useAuth()

const loading = ref(true)
const journeys = ref<MasteryJourney[]>([])
const activity = ref<ActivityStats | null>(null)
const weakpoints = ref<Weakpoint[]>([])

// AI 学习总结 (Issue 13): 生成按钮 + markdown 报告, 可重复生成
const summaryText = ref('')
const generating = ref(false)

/** 是否有学习数据 (掌握度与易错清单同为空 = 从未答题) → 无数据禁用生成. */
const hasLearningData = computed(
  () => journeys.value.length > 0 || weakpoints.value.length > 0,
)

/** 生成当前状态分析报告 (FastAPI LLM 同步接口, 失败提示后可重试). */
async function generateSummary() {
  if (!auth.user || generating.value) return
  generating.value = true
  try {
    const { summary } = await generateStatusSummary(auth.user.id)
    summaryText.value = summary
  } catch (e) {
    summaryText.value = ''
    ElMessage.error(e instanceof ApiError ? e.message : '状态总结生成失败, 请稍后重试')
  } finally {
    generating.value = false
  }
}

onMounted(async () => {
  try {
    const [m, a, w] = await Promise.all([
      getMasteryMatrix(),
      getActivityStats(),
      getWeakpoints(),
    ])
    journeys.value = m.journeys
    activity.value = a
    weakpoints.value = w.weakpoints
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '学习分析加载失败, 请稍后重试')
  } finally {
    loading.value = false
  }
})

/** 时长格式化: 秒 → 分钟 → 小时 (与复盘报告同款逻辑). */
function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const min = Math.floor(seconds / 60)
  if (min < 60) return `${min} 分钟`
  return `${Math.floor(min / 60)} 小时 ${min % 60} 分`
}

/** 日期标签: 8/24 短格式. */
function shortDate(iso: string): string {
  const [, m, d] = iso.split('-')
  return `${Number(m)}/${Number(d)}`
}

/** 掌握度条配色: ≥80 主粉 / 60-80 浅紫 / <60 琥珀 (薄弱). */
function masteryColor(accuracy: number): string {
  if (accuracy >= 80) return 'var(--cp-primary)'
  if (accuracy >= 60) return 'var(--cp-accent-lilac)'
  return 'var(--cp-warn)'
}

/** 薄弱点胶囊文案 (带易错分提示, 与间隔复习同源). */
function weakLabel(p: { weak: boolean; error_score: number }): string | null {
  if (!p.weak) return null
  return p.error_score > 0 ? `薄弱 · 易错分 ${p.error_score}` : '薄弱'
}

/** 趋势柱高: 相对窗口最大值, 无活动日保留 3px 基线. */
function barHeight(day: { answers: number; cleared: number }, max: number): number {
  const total = day.answers + day.cleared
  if (total <= 0) return 3
  return Math.max(8, Math.round((total / Math.max(max, 1)) * 100))
}

/** 近 14 天窗口最大单日活动量 (柱状图比例基准). */
const maxDaily = computed(() =>
  Math.max(1, ...(activity.value?.daily ?? []).map((d) => d.answers + d.cleared)),
)

/** 今日索引 (daily 数组末位). */
const todayIndex = computed(() => (activity.value?.daily.length ?? 0) - 1)

/** 易错优先级标签映射. */
const priorityMeta: Record<Weakpoint['priority_level'], { label: string; cls: string }> = {
  high: { label: '优先复习', cls: 'priority-high' },
  medium: { label: '建议复习', cls: 'priority-medium' },
  low: { label: '顺带复习', cls: 'priority-low' },
}
</script>

<template>
  <div class="dashboard">
    <!-- 页头: 一句话状态摘要 -->
    <header class="page-head">
      <h1 class="page-title">学习分析</h1>
      <p v-if="activity" class="page-sub">
        已坚持学习 {{ activity.active_days }} 天, 闯过 {{ activity.cleared_levels }} 关,
        当前连胜 {{ activity.streak }} 天
      </p>
    </header>

    <!-- 加载中 -->
    <section v-if="loading" class="panel">
      <el-skeleton :rows="6" animated />
    </section>

    <template v-else-if="activity">
      <!-- KPI 行: 时长 / 通关数 / 活跃天数 / 连胜 -->
      <section class="kpi-row" aria-label="学习活动总览">
        <div class="kpi">
          <span class="kpi-label">学习时长</span>
          <span class="kpi-value">{{ formatDuration(activity.duration_seconds) }}</span>
        </div>
        <div class="kpi">
          <span class="kpi-label">通关数</span>
          <span class="kpi-value">{{ activity.cleared_levels }}</span>
          <span class="kpi-unit">关</span>
        </div>
        <div class="kpi">
          <span class="kpi-label">活跃天数</span>
          <span class="kpi-value">{{ activity.active_days }}</span>
          <span class="kpi-unit">天</span>
        </div>
        <div class="kpi">
          <span class="kpi-label">连胜</span>
          <span class="kpi-value kpi-flame">{{ activity.streak }}</span>
          <span class="kpi-sub">纪录 {{ activity.max_streak }}</span>
        </div>
      </section>

      <!-- AI 学习总结: 点击生成当前状态分析 (Issue 13, PRD F-4) -->
      <section class="panel summary-panel" aria-label="AI 学习总结">
        <header class="panel-head summary-head">
          <div>
            <h2 class="panel-title">AI 学习总结</h2>
            <p class="panel-desc">
              基于统计事实生成强项 / 弱项 / 学习建议, 可重复生成
            </p>
          </div>
          <el-button
            type="primary"
            round
            :loading="generating"
            :disabled="!hasLearningData"
            @click="generateSummary"
          >
            {{ generating ? '正在分析…' : summaryText ? '重新生成' : '生成当前状态分析' }}
          </el-button>
        </header>

        <div v-if="generating" class="summary-loading" aria-label="正在生成状态总结">
          <el-skeleton :rows="4" animated />
        </div>

        <!-- 报告卡片: 顶部渐变细条为签名元素 (与趋势柱渐变同族) -->
        <div v-else-if="summaryText" class="summary-card">
          <MarkdownText :text="summaryText" />
        </div>

        <div v-else class="empty summary-empty">
          <p class="empty-emoji" aria-hidden="true">💡</p>
          <p v-if="hasLearningData" class="empty-detail">
            点击右上角按钮, 生成基于当前统计的学习状态分析报告
          </p>
          <p v-else class="empty-detail">
            还没有答题数据, 先去闯一关再回来生成总结吧
          </p>
        </div>
      </section>

      <!-- 掌握度矩阵: 薄弱点高亮 (页面主区块) -->
      <section class="panel" aria-label="掌握度矩阵">
        <header class="panel-head">
          <h2 class="panel-title">掌握度矩阵</h2>
          <p class="panel-desc">按知识点正确率, 琥珀色为薄弱点 (正确率低于 60%)</p>
        </header>

        <div v-if="journeys.length === 0" class="empty">
          <p class="empty-emoji" aria-hidden="true">📊</p>
          <p class="empty-title">还没有答题数据</p>
          <p class="empty-detail">去闯一关, 这里就会出现你的知识点掌握度</p>
          <router-link to="/">
            <el-button type="primary" round>去学习</el-button>
          </router-link>
        </div>

        <div v-for="journey in journeys" :key="journey.journey_id" class="journey-block">
          <h3 class="journey-title">
            {{ journey.title }}
            <span v-if="journey.cleared" class="chip chip-cleared">已通关</span>
          </h3>

          <div v-for="chapter in journey.chapters" :key="chapter.chapter_id" class="chapter-block">
            <div class="chapter-row">
              <span class="chapter-name">{{ chapter.title }}</span>
              <span class="chapter-meta">
                {{ chapter.correct }}/{{ chapter.answered }} 对 · 正确率
                {{ chapter.accuracy }}%
              </span>
            </div>

            <ul class="point-list">
              <li
                v-for="point in chapter.knowledge_points"
                :key="point.kp_id"
                class="point-row"
                :class="{ 'point-weak': point.weak }"
              >
                <span class="point-name" :title="point.title">{{ point.title }}</span>
                <div class="point-bar-wrap" role="img" :aria-label="`${point.title} 正确率 ${point.accuracy}%`">
                  <div
                    class="point-bar"
                    :style="{
                      width: point.accuracy + '%',
                      background: masteryColor(point.accuracy),
                    }"
                  />
                </div>
                <span class="point-acc">{{ point.accuracy }}%</span>
                <span v-if="weakLabel(point)" class="chip chip-weak" role="note">
                  {{ weakLabel(point) }}
                </span>
              </li>
            </ul>
          </div>
        </div>
      </section>

      <div class="cols">
        <!-- 易错点清单: 复习优先级 -->
        <section class="panel" aria-label="易错点清单">
          <header class="panel-head">
            <h2 class="panel-title">易错点清单</h2>
            <p class="panel-desc">按易错分与时间衰减排序, 与间隔复习同源</p>
          </header>

          <div v-if="weakpoints.length === 0" class="empty empty-sm">
            <p class="empty-emoji" aria-hidden="true">🎉</p>
            <p class="empty-title">暂无易错点</p>
            <p class="empty-detail">答错的题目会累积易错分, 驱动间隔复习混入</p>
          </div>

          <ul v-else class="weak-list">
            <li v-for="(w, idx) in weakpoints" :key="w.kp_id" class="weak-row">
              <span class="weak-rank" aria-hidden="true">{{ idx + 1 }}</span>
              <div class="weak-body">
                <div class="weak-head">
                  <span class="weak-title" :title="w.title">{{ w.title }}</span>
                  <span class="chip" :class="`priority-${w.priority_level}`">
                    {{ priorityMeta[w.priority_level].label }}
                  </span>
                </div>
                <p class="weak-meta">
                  {{ w.journey_title }} · {{ w.chapter_title }} · 答错
                  {{ w.wrong_count }} 次
                  <template v-if="w.days_since_review < 30"> · {{ w.days_since_review }} 天前复习过</template>
                </p>
              </div>
              <span class="weak-score" :title="`易错分 ${w.error_score}`">
                {{ w.error_score }} 分
              </span>
            </li>
          </ul>
        </section>

        <!-- 近 14 天活跃趋势 -->
        <section class="panel" aria-label="近 14 天学习活跃">
          <header class="panel-head">
            <h2 class="panel-title">活跃趋势</h2>
            <p class="panel-desc">近 14 天每日答题 + 通关</p>
          </header>

          <div v-if="!activity.daily.some((d) => d.active)" class="empty empty-sm">
            <p class="empty-emoji" aria-hidden="true">🌱</p>
            <p class="empty-title">最近没有学习记录</p>
            <p class="empty-detail">连续学习就能保持连胜</p>
          </div>

          <div v-else class="trend" role="img" aria-label="近 14 天每日学习活跃柱状图">
            <div
              v-for="(day, i) in activity.daily"
              :key="day.date"
              class="trend-col"
              :class="{ 'trend-today': i === todayIndex }"
            >
              <div class="trend-bar-area">
                <div
                  class="trend-bar"
                  :class="{ 'trend-bar-idle': !day.active }"
                  :style="{ height: barHeight(day, maxDaily) + '%' }"
                  :title="`${shortDate(day.date)}: 答题 ${day.answers} · 通关 ${day.cleared}`"
                >
                  <span v-if="day.cleared > 0" class="trend-clear" aria-hidden="true">★</span>
                </div>
              </div>
              <span class="trend-date">{{ shortDate(day.date) }}</span>
            </div>
          </div>
        </section>
      </div>
    </template>

    <!-- 加载失败 (后端不可达等) -->
    <section v-else class="panel empty">
      <p class="empty-emoji" aria-hidden="true">🔌</p>
      <p class="empty-title">学习分析加载失败</p>
      <p class="empty-detail">请确认后端服务已启动后刷新重试</p>
    </section>
  </div>
</template>

<style scoped>
/* ---- 页头 ---- */
.page-head {
  margin-bottom: 20px;
}

.page-title {
  margin: 0;
  font-size: 26px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--cp-ink);
}

.page-sub {
  margin: 6px 0 0;
  font-size: 14px;
  color: var(--cp-ink-soft);
}

/* ---- KPI 行: 4 格, 白卡 + 粉阴影 (延续 Profile stat-grid 语言) ---- */
.kpi-row {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin-bottom: 20px;
}

.kpi {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 4px;
  padding: 16px 18px;
  background: var(--cp-card);
  border: 1px solid rgba(251, 114, 153, 0.12);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
}

.kpi-label {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.kpi-value {
  font-size: 24px;
  font-weight: 700;
  line-height: 1.1;
  color: var(--cp-ink);
}

.kpi-unit {
  position: absolute;
  top: 40px;
  right: 18px;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.kpi-sub {
  font-size: 11px;
  color: var(--cp-ink-soft);
}

/* 连胜火焰: 与导航/个人主页同款呼吸动画 (签名元素家族) */
.kpi-flame {
  color: var(--cp-primary);
  animation: dashFlame 2.6s ease-in-out infinite;
  transform-origin: 50% 85%;
  display: inline-block;
  width: fit-content;
}

@keyframes dashFlame {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.18);
  }
}

/* ---- 通用面板 ---- */
.panel {
  background: var(--cp-card);
  border: 1px solid rgba(251, 114, 153, 0.12);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 20px 22px;
  margin-bottom: 20px;
}

.panel-head {
  margin-bottom: 14px;
}

.panel-title {
  margin: 0;
  font-size: 17px;
  font-weight: 700;
  color: var(--cp-ink);
}

.panel-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

/* ---- AI 学习总结 (Issue 13) ---- */
.summary-head {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 14px;
}

/* 报告卡片签名: 顶部粉→浅紫渐变细条 (与趋势柱渐变同族), 其余保持安静 */
.summary-card {
  position: relative;
  padding: 18px 20px 6px;
  border: 1px solid rgba(251, 114, 153, 0.14);
  border-radius: var(--cp-radius-sm);
  background: var(--cp-primary-soft);
}

.summary-card::before {
  content: '';
  position: absolute;
  top: 0;
  left: 20px;
  right: 20px;
  height: 3px;
  border-radius: 0 0 3px 3px;
  background: linear-gradient(90deg, var(--cp-primary), var(--cp-accent-lilac));
}

/* 三段标题 (## 强项 / ## 弱项 / ## 学习建议): 粉左色条, 报告骨架一眼可见 */
.summary-card :deep(.md h3) {
  margin: 14px 0 8px;
  padding-left: 10px;
  border-left: 3px solid var(--cp-primary);
}

.summary-card :deep(.md h3:first-child) {
  margin-top: 0;
}

.summary-card :deep(.md ul),
.summary-card :deep(.md ol) {
  padding-left: 20px;
}

.summary-loading {
  padding: 4px 2px;
}

.summary-empty {
  padding: 18px 10px;
}

/* ---- 掌握度矩阵 ---- */
.journey-block + .journey-block {
  margin-top: 18px;
  padding-top: 18px;
  border-top: 1px dashed rgba(251, 114, 153, 0.2);
}

.journey-title {
  margin: 0 0 10px;
  font-size: 15px;
  font-weight: 700;
  color: var(--cp-ink);
  display: flex;
  align-items: center;
  gap: 8px;
}

.chapter-block + .chapter-block {
  margin-top: 12px;
}

.chapter-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 6px;
}

.chapter-name {
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink-soft);
}

.chapter-meta {
  font-size: 12px;
  color: var(--cp-ink-soft);
}

/* 知识点行: 标题 + 掌握度条 + 正确率 + 薄弱胶囊 */
.point-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.point-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 7px 8px;
  border-radius: var(--cp-radius-sm);
}

.point-row + .point-row {
  border-top: 1px solid rgba(138, 138, 153, 0.08);
}

/* 薄弱点行: 淡琥珀底 + 红系字, 一眼定位 (PRD F-1 高亮) */
.point-weak {
  background: rgba(245, 166, 35, 0.08);
}

.point-name {
  flex: 0 0 30%;
  font-size: 13px;
  color: var(--cp-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.point-bar-wrap {
  flex: 1;
  height: 8px;
  border-radius: 999px;
  background: var(--cp-primary-soft);
  overflow: hidden;
}

.point-bar {
  height: 100%;
  border-radius: 999px;
  transition: width 0.6s ease;
}

.point-acc {
  flex: 0 0 44px;
  text-align: right;
  font-size: 13px;
  font-weight: 700;
  color: var(--cp-ink);
  font-variant-numeric: tabular-nums;
}

/* ---- 胶囊徽章 ---- */
.chip {
  font-size: 11px;
  font-weight: 600;
  padding: 2px 9px;
  border-radius: 999px;
  white-space: nowrap;
}

.chip-cleared {
  background: rgba(52, 201, 142, 0.14);
  color: var(--cp-ok);
}

.chip-weak {
  background: rgba(245, 166, 35, 0.16);
  color: #c76e1b;
}

.priority-high {
  background: rgba(245, 108, 108, 0.14);
  color: #d94f4f;
}

.priority-medium {
  background: rgba(245, 166, 35, 0.16);
  color: #c76e1b;
}

.priority-low {
  background: rgba(138, 138, 153, 0.12);
  color: var(--cp-ink-soft);
}

/* ---- 两列: 易错清单 + 活跃趋势 ---- */
.cols {
  display: grid;
  grid-template-columns: 3fr 2fr;
  gap: 20px;
  align-items: start;
}

/* ---- 易错点清单 ---- */
.weak-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.weak-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 10px 4px;
}

.weak-row + .weak-row {
  border-top: 1px solid rgba(138, 138, 153, 0.08);
}

.weak-rank {
  flex: 0 0 22px;
  height: 22px;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  color: var(--cp-primary-deep);
  background: var(--cp-primary-soft);
  border-radius: 999px;
}

.weak-body {
  flex: 1;
  min-width: 0;
}

.weak-head {
  display: flex;
  align-items: center;
  gap: 8px;
}

.weak-title {
  font-size: 13px;
  font-weight: 600;
  color: var(--cp-ink);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weak-meta {
  margin: 3px 0 0;
  font-size: 11px;
  color: var(--cp-ink-soft);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.weak-score {
  flex: 0 0 auto;
  font-size: 13px;
  font-weight: 700;
  color: #d94f4f;
  font-variant-numeric: tabular-nums;
}

/* ---- 活跃趋势: 纯 CSS 柱状图 ---- */
.trend {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 150px;
  padding-top: 6px;
}

.trend-col {
  flex: 1;
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  height: 100%;
}

.trend-bar-area {
  flex: 1;
  width: 100%;
  display: flex;
  align-items: flex-end;
  justify-content: center;
}

.trend-bar {
  width: 70%;
  min-height: 3px;
  border-radius: 6px 6px 2px 2px;
  background: linear-gradient(180deg, var(--cp-primary), var(--cp-accent-lilac));
  position: relative;
}

.trend-bar-idle {
  background: rgba(251, 114, 153, 0.18);
}

/* 通关星标: 当日有通关在柱顶标记 */
.trend-clear {
  position: absolute;
  top: -16px;
  left: 50%;
  transform: translateX(-50%);
  font-size: 11px;
  color: var(--cp-warn);
}

/* 今天: 柱色加深 + 日期加粗 */
.trend-today .trend-bar {
  background: linear-gradient(180deg, var(--cp-primary-deep), var(--cp-primary));
}

.trend-today .trend-date {
  font-weight: 700;
  color: var(--cp-primary-deep);
}

.trend-date {
  font-size: 10px;
  color: var(--cp-ink-soft);
}

/* ---- 空状态 ---- */
.empty {
  text-align: center;
  padding: 26px 10px;
}

.empty-sm {
  padding: 16px 10px;
}

.empty-emoji {
  margin: 0;
  font-size: 34px;
}

.empty-title {
  margin: 10px 0 4px;
  font-size: 15px;
  font-weight: 700;
  color: var(--cp-ink);
}

.empty-detail {
  margin: 0 0 12px;
  font-size: 13px;
  color: var(--cp-ink-soft);
}

/* ---- 响应式: 窄屏单列 ---- */
@media (max-width: 900px) {
  .kpi-row {
    grid-template-columns: repeat(2, 1fr);
  }

  .cols {
    grid-template-columns: 1fr;
  }

  .point-name {
    flex-basis: 24%;
  }
}
</style>
