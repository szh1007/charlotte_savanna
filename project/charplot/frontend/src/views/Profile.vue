<script setup lang="ts">
// 个人主页 (Issue 02, DESIGN.md §6 页面 8):
// 游戏化状态卡 + 连胜中断警告 + 统计面板 + 连胜冻结兑换.
// 签名元素: 连胜火焰徽章呼吸动画 (Issue 02 视觉 signature).
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/api/client'
import { useAuth } from '@/stores/auth'

const { state, buyStreakFreeze } = useAuth()

const buying = ref(false)

/** 冻结保护中 (freeze_until 含当日). */
const isFrozen = (): boolean => {
  const until = state.profile?.freeze_until
  if (!until) return false
  return new Date(until + 'T23:59:59').getTime() >= Date.now()
}

/** 冻结截止展示文案. */
const freezeText = (): string => {
  const until = state.profile?.freeze_until
  if (!until) return ''
  const d = new Date(until + 'T00:00:00')
  return `${d.getMonth() + 1} 月 ${d.getDate()} 日`
}

async function onBuyFreeze() {
  if (!state.profile || buying.value) return
  buying.value = true
  try {
    await buyStreakFreeze()
    ElMessage.success('已冻结 1 天连胜，放心休息')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '兑换失败，请稍后重试')
  } finally {
    buying.value = false
  }
}

interface StatTile {
  key: 'level' | 'xp' | 'streak' | 'max_streak' | 'hearts' | 'coins'
  label: string
  icon: string
  flame?: boolean
}

const stats: StatTile[] = [
  { key: 'level', label: '等级', icon: '★' },
  { key: 'xp', label: '经验值', icon: '⚡' },
  { key: 'streak', label: '连胜', icon: '🔥', flame: true },
  { key: 'max_streak', label: '最大连胜', icon: '🏆' },
  { key: 'hearts', label: '心动值', icon: '💗' },
  { key: 'coins', label: '学习币', icon: '🪙' },
]

interface PanelStat {
  key: 'login_days' | 'answered' | 'correct' | 'wrong'
  label: string
  hint: string
}

const panelStats: PanelStat[] = [
  { key: 'login_days', label: '登录天数', hint: '每天登录自动累计' },
  { key: 'answered', label: '已答题数', hint: '闯关答题上线后自动统计' },
  { key: 'correct', label: '答对数', hint: '闯关答题上线后自动统计' },
  { key: 'wrong', label: '答错数', hint: '闯关答题上线后自动统计' },
]
</script>

<template>
  <div v-if="state.profile" class="profile">
    <!-- 头部: 头像圈 + 用户名 + 徽章 -->
    <header class="profile-head">
      <div class="avatar" aria-hidden="true">
        {{ state.profile.username.slice(0, 1).toUpperCase() }}
      </div>
      <div class="head-info">
        <h1 class="username">
          {{ state.profile.username }}
          <span v-if="state.profile.is_staff" class="badge badge-staff">管理员</span>
        </h1>
        <p class="head-sub">学习闯关进行时 · 保持连胜</p>
      </div>
      <div class="level-chip" :title="`等级 ${state.profile.level}`">
        <span class="level-star" aria-hidden="true">★</span>
        <span>Lv.{{ state.profile.level }}</span>
      </div>
    </header>

    <!-- 连胜损失警告 / 冻结横幅 -->
    <Transition name="fade">
      <div
        v-if="state.profile.streak_loss_warning.warning"
        class="banner banner-warn"
        role="alert"
      >
        <span class="banner-icon" aria-hidden="true">🔥</span>
        <div class="banner-body">
          <p class="banner-title">
            已连续 {{ state.profile.streak_loss_warning.missed_days }} 天未学习，连胜即将中断
          </p>
          <p class="banner-desc">今天学习一关就能保住连胜；或兑换冻结，给连胜放个假</p>
        </div>
        <div class="banner-actions">
          <router-link to="/" class="btn btn-primary">去学习</router-link>
          <el-button
            size="small"
            round
            :loading="buying"
            :disabled="(state.profile.coins ?? 0) < 10"
            @click="onBuyFreeze"
          >
            兑换冻结
          </el-button>
        </div>
      </div>
      <div v-else-if="isFrozen()" class="banner banner-frozen" role="status">
        <span class="banner-icon" aria-hidden="true">🧊</span>
        <div class="banner-body">
          <p class="banner-title">连胜冻结中，至 {{ freezeText() }}</p>
          <p class="banner-desc">冻结期内不学习也不会中断连胜</p>
        </div>
      </div>
    </Transition>

    <!-- 游戏化状态卡: 6 格 stat tiles -->
    <section class="stat-grid" aria-label="游戏化状态">
      <article
        v-for="s in stats"
        :key="s.key"
        class="stat-tile"
        :class="{ 'stat-tile-flame': s.flame }"
      >
        <span class="stat-icon" :class="{ 'flame-breathe': s.flame }" aria-hidden="true">
          {{ s.icon }}
        </span>
        <div class="stat-num" :class="{ 'num-flame': s.flame }">
          {{ state.profile[s.key] }}
        </div>
        <div class="stat-label">{{ s.label }}</div>
      </article>
    </section>

    <!-- 统计面板 -->
    <section class="panel">
      <header class="panel-head">
        <h2 class="panel-title">学习统计</h2>
        <p class="panel-sub">所有数字来自你的真实学习记录</p>
      </header>
      <div class="panel-grid">
        <div v-for="p in panelStats" :key="p.key" class="panel-item">
          <div class="panel-num">{{ state.profile.stats[p.key] }}</div>
          <div class="panel-label">{{ p.label }}</div>
          <div class="panel-hint">{{ p.hint }}</div>
        </div>
      </div>
    </section>

    <!-- 连胜冻结兑换 -->
    <section class="panel">
      <header class="panel-head">
        <h2 class="panel-title">连胜冻结</h2>
        <p class="panel-sub">学习币兑换，轻量替代付费（PRD G-4）</p>
      </header>
      <div class="freeze-box">
        <div class="freeze-info">
          <p class="freeze-cost">
            <span class="coin-icon" aria-hidden="true">🪙</span>
            10 学习币 = 冻结 1 天连胜
          </p>
          <p class="freeze-desc">冻结期内不学习也不中断，可叠加兑换顺延</p>
        </div>
        <el-button
          type="primary"
          round
          size="large"
          :loading="buying"
          :disabled="state.profile.coins < 10"
          @click="onBuyFreeze"
        >
          兑换（剩余 {{ state.profile.coins }} 币）
        </el-button>
      </div>
      <p v-if="state.profile.coins < 10" class="freeze-guide">
        学习币不足 —— 通关关卡可获得学习币，先回地图闯一关吧
      </p>
    </section>
  </div>
  <div v-else class="profile profile-loading">
    <el-skeleton :rows="6" animated />
  </div>
</template>

<style scoped>
.profile {
  max-width: 720px;
  margin: 0 auto;
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.profile-loading {
  padding-top: 40px;
}

/* ---- 头部 ---- */
.profile-head {
  display: flex;
  align-items: center;
  gap: 16px;
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 24px 28px;
}

.avatar {
  width: 64px;
  height: 64px;
  flex-shrink: 0;
  display: grid;
  place-items: center;
  border-radius: 20px;
  font-size: 26px;
  font-weight: 800;
  color: #fff;
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  box-shadow: 0 8px 18px rgba(251, 114, 153, 0.35);
}

.head-info {
  flex: 1;
}

.username {
  margin: 0;
  font-size: 22px;
  font-weight: 800;
  color: var(--cp-ink);
  display: flex;
  align-items: center;
  gap: 10px;
}

.badge-staff {
  font-size: 11px;
  font-weight: 700;
  padding: 3px 10px;
  border-radius: 999px;
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  color: #fff;
}

.head-sub {
  margin: 5px 0 0;
  font-size: 13px;
  color: var(--cp-ink-soft);
}

.level-chip {
  display: flex;
  align-items: center;
  gap: 6px;
  padding: 8px 16px;
  border-radius: 999px;
  font-size: 14px;
  font-weight: 800;
  color: var(--cp-primary-deep);
  background: var(--cp-primary-soft);
  border: 1px solid rgba(251, 114, 153, 0.25);
}

.level-star {
  color: #ffb400;
}

/* ---- 横幅 ---- */
.banner {
  display: flex;
  align-items: center;
  gap: 14px;
  border-radius: var(--cp-radius);
  padding: 16px 20px;
}

.banner-warn {
  background: rgba(245, 166, 35, 0.12);
  border: 1px solid rgba(245, 166, 35, 0.35);
}

.banner-frozen {
  background: rgba(165, 207, 227, 0.18);
  border: 1px solid rgba(165, 207, 227, 0.45);
}

.banner-icon {
  font-size: 26px;
  flex-shrink: 0;
}

.banner-body {
  flex: 1;
}

.banner-title {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--cp-ink);
}

.banner-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.banner-actions {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-shrink: 0;
}

.btn-primary {
  text-decoration: none;
  font-size: 13px;
  font-weight: 700;
  color: #fff;
  background: linear-gradient(135deg, var(--cp-primary), var(--cp-accent-lilac));
  padding: 7px 18px;
  border-radius: 999px;
}

.btn-primary:hover {
  filter: brightness(0.96);
}

/* ---- 状态卡 ---- */
.stat-grid {
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  gap: 14px;
}

@media (max-width: 720px) {
  .stat-grid {
    grid-template-columns: repeat(3, 1fr);
  }
}

.stat-tile {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 18px 12px;
  text-align: center;
  transition:
    transform 0.25s ease,
    box-shadow 0.25s ease;
}

.stat-tile:hover {
  transform: translateY(-3px);
  box-shadow: var(--cp-shadow-hover);
}

.stat-tile-flame {
  background: linear-gradient(160deg, #fff7e8, var(--cp-card));
  border: 1px solid rgba(245, 166, 35, 0.25);
}

.stat-icon {
  display: block;
  font-size: 22px;
  line-height: 1;
  margin-bottom: 10px;
}

/* 签名元素: 连胜火焰呼吸动画 (连胜是习惯钩子, 呼吸感 = 连胜还活着) */
.flame-breathe {
  display: inline-block;
  animation: flame 2.6s ease-in-out infinite;
  transform-origin: 50% 85%;
}

@keyframes flame {
  0%,
  100% {
    transform: scale(1) rotate(-2deg);
  }
  50% {
    transform: scale(1.18) rotate(2deg);
  }
}

.stat-num {
  font-size: 26px;
  font-weight: 800;
  color: var(--cp-ink);
  line-height: 1.1;
}

.num-flame {
  background: linear-gradient(160deg, #f5a623, #fb7299);
  -webkit-background-clip: text;
  background-clip: text;
  -webkit-text-fill-color: transparent;
}

.stat-label {
  margin-top: 6px;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

/* ---- 面板 ---- */
.panel {
  background: var(--cp-card);
  border-radius: var(--cp-radius);
  box-shadow: var(--cp-shadow);
  padding: 22px 24px;
}

.panel-head {
  margin-bottom: 16px;
}

.panel-title {
  margin: 0;
  font-size: 16px;
  font-weight: 800;
  color: var(--cp-ink);
}

.panel-sub {
  margin: 3px 0 0;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.panel-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}

@media (max-width: 720px) {
  .panel-grid {
    grid-template-columns: repeat(2, 1fr);
  }
}

.panel-item {
  text-align: center;
  padding: 14px 10px;
  border-radius: var(--cp-radius-sm);
  background: var(--cp-primary-soft);
}

.panel-num {
  font-size: 24px;
  font-weight: 800;
  color: var(--cp-primary-deep);
  line-height: 1.1;
}

.panel-label {
  margin-top: 4px;
  font-size: 13px;
  font-weight: 700;
  color: var(--cp-ink);
}

.panel-hint {
  margin-top: 3px;
  font-size: 11px;
  color: var(--cp-ink-soft);
}

/* ---- 冻结兑换 ---- */
.freeze-box {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
  flex-wrap: wrap;
}

.freeze-cost {
  margin: 0;
  font-size: 15px;
  font-weight: 700;
  color: var(--cp-ink);
}

.coin-icon {
  margin-right: 4px;
}

.freeze-desc {
  margin: 4px 0 0;
  font-size: 12px;
  color: var(--cp-ink-soft);
}

.freeze-guide {
  margin: 14px 0 0;
  font-size: 13px;
  color: var(--cp-warn);
  font-weight: 600;
}

/* ---- 过渡 ---- */
.fade-enter-active {
  transition: opacity 0.3s ease, transform 0.3s ease;
}

.fade-enter-from {
  opacity: 0;
  transform: translateY(6px);
}
</style>
