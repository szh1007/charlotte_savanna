<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import ErrorAlert from './ErrorAlert.vue'

// 会员营销区 (T09): 免费/会员功能对比表 + 限时倒计时 + 密钥输入
// 密钥由父组件 (Home) 提交并管理全站会员状态, 本组件纯展示与输入
const props = defineProps({
  // 当前会话是否会员 (Home 统一管理)
  isMember: { type: Boolean, default: false },
  // 会员会话过期时间 (秒级时间戳, 解锁成功后展示)
  expiresAt: { type: Number, default: null },
  // 密钥提交中 (防重复点击)
  submitting: { type: Boolean, default: false },
  // 提交失败错误信息 (错误密钥等, 后端 detail 透传)
  error: { type: String, default: '' },
})

const emit = defineEmits(['unlock'])

// 功能对比表 (PRD §5 付费差异, 后端强制的能力对照)
const PLAN_ROWS = [
  { ability: '最高清晰度', free: '720p', member: '1080p / 4K / 最佳画质' },
  { ability: '并发下载', free: '1 个', member: '3 个' },
  { ability: '批量队列', free: '5 个', member: '50 个' },
  { ability: '交付直链有效期', free: '24h', member: '72h' },
]

// 付费引导文案 (PRD §10)
const FREE_SLOGAN = '免费下载 · 最高 720p · 适合快速预览'
const MEMBER_SLOGAN = '解锁 4K 高清 · 批量 50 个 · 3 倍速并发 · 72h 文件保留'

// 限时倒计时: 距今日 24:00 (营销元素, 每日重置)
const countdown = ref(0)
let timer = null

function secondsToMidnight() {
  const now = new Date()
  const end = new Date(now)
  end.setHours(24, 0, 0, 0)
  return Math.max(0, Math.floor((end - now) / 1000))
}

const countdownText = computed(() => {
  const total = countdown.value
  const h = String(Math.floor(total / 3600)).padStart(2, '0')
  const m = String(Math.floor((total % 3600) / 60)).padStart(2, '0')
  const s = String(total % 60).padStart(2, '0')
  return `${h}:${m}:${s}`
})

onMounted(() => {
  countdown.value = secondsToMidnight()
  timer = setInterval(() => (countdown.value = secondsToMidnight()), 1000)
})

onBeforeUnmount(() => clearInterval(timer))

// 密钥输入
const key = ref('')
const keyInput = ref(null)

function handleUnlock() {
  const trimmed = key.value.trim()
  if (!trimmed) return
  emit('unlock', trimmed)
}

// 导航栏会员入口滚动定位后聚焦输入框 (父组件调用)
defineExpose({
  focusInput() {
    keyInput.value?.focus()
  },
})

// 会员过期时间展示 (如 "2026-08-20 14:30")
const expiresText = computed(() =>
  props.expiresAt ? new Date(props.expiresAt * 1000).toLocaleString('zh-CN') : '',
)
</script>

<template>
  <section id="member-section" class="member fade-up" aria-label="会员专区">
    <!-- 顶部营销头: 标题 + 限时 badge + 倒计时 -->
    <div class="member__head">
      <div class="member__headline">
        <span class="member__badge">✦ 限时特惠</span>
        <h2 class="member__title">解锁全部能力, 快人一步</h2>
      </div>
      <p class="member__countdown">
        <span class="member__countdown-label">距今日结束</span>
        <span class="member__countdown-time">{{ countdownText }}</span>
      </p>
    </div>

    <!-- 功能对比表 -->
    <div class="member__table-wrap">
      <table class="member__table">
        <thead>
          <tr>
            <th class="member__th member__th--ability">能力</th>
            <th class="member__th">
              <span class="member__plan-name">免费档</span>
              <span class="member__plan-sub">{{ FREE_SLOGAN }}</span>
            </th>
            <th class="member__th member__th--member">
              <span class="member__plan-name">会员档</span>
              <span class="member__plan-sub">{{ MEMBER_SLOGAN }}</span>
            </th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in PLAN_ROWS" :key="row.ability" class="member__row">
            <td class="member__ability">{{ row.ability }}</td>
            <td class="member__cell">{{ row.free }}</td>
            <td class="member__cell member__cell--member">{{ row.member }}</td>
          </tr>
        </tbody>
      </table>
    </div>

    <!-- 密钥解锁区: 未解锁时展示输入框, 已解锁展示权益状态 -->
    <div class="member__unlock">
      <template v-if="!isMember">
        <p class="member__hint">输入会员密钥, 立即解锁全部能力</p>
        <form class="member__form" @submit.prevent="handleUnlock">
          <input
            ref="keyInput"
            v-model="key"
            class="member__input"
            type="password"
            placeholder="请输入会员密钥"
            autocomplete="off"
          />
          <button class="btn-gradient member__btn" type="submit" :disabled="submitting">
            <span v-if="submitting" class="member__spinner" aria-hidden="true"></span>
            {{ submitting ? '解锁中…' : '立即解锁' }}
          </button>
        </form>
        <ErrorAlert :message="error" class="member__error" />
      </template>

      <div v-else class="member__unlocked" role="status">
        <span class="member__unlocked-icon">✨</span>
        <div class="member__unlocked-body">
          <p class="member__unlocked-title">会员已解锁</p>
          <p class="member__unlocked-sub">全部清晰度 · 3 并发 · 队列 50 · 72h 保留</p>
          <p v-if="expiresText" class="member__unlocked-expires">
            有效期至 {{ expiresText }}
          </p>
        </div>
      </div>
    </div>
  </section>
</template>

<style scoped>
/* 深色对比块: 渐变描边容器 + 深紫黑底 */
.member {
  margin-top: 64px;
  padding: 44px 28px;
  border-radius: var(--radius);
  background:
    radial-gradient(480px 260px at 90% 0%, rgba(235, 47, 150, 0.16), transparent 65%),
    linear-gradient(var(--bg-deep), var(--bg-deep)) padding-box,
    var(--gradient) border-box;
  border: 1.5px solid transparent;
}

.member__head {
  display: flex;
  align-items: flex-end;
  justify-content: space-between;
  gap: 20px;
  flex-wrap: wrap;
}

.member__headline {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

/* 限时营销 badge */
.member__badge {
  padding: 4px 12px;
  border-radius: 999px;
  font-size: 12px;
  font-weight: 700;
  color: #fff;
  background: var(--gradient);
  animation: badge-pulse 2s ease infinite;
}

@keyframes badge-pulse {
  0%,
  100% {
    box-shadow: 0 0 0 0 rgba(235, 47, 150, 0.5);
  }
  50% {
    box-shadow: 0 0 0 8px rgba(235, 47, 150, 0);
  }
}

.member__title {
  font-size: 24px;
  font-weight: 800;
}

.member__countdown {
  display: flex;
  align-items: center;
  gap: 10px;
}

.member__countdown-label {
  font-size: 13px;
  color: var(--text-sub);
}

.member__countdown-time {
  font-family: var(--font-mono);
  font-size: 20px;
  font-weight: 700;
  color: var(--primary);
}

/* 对比表 */
.member__table-wrap {
  margin-top: 28px;
  overflow-x: auto;
}

.member__table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
}

.member__th {
  padding: 14px 18px;
  font-size: 15px;
  text-align: left;
  border-bottom: 1px solid var(--border);
}

.member__th--ability {
  width: 34%;
}

.member__th--member {
  border-radius: var(--radius-sm);
  background: rgba(235, 47, 150, 0.1);
}

.member__plan-name {
  display: block;
  font-weight: 700;
}

.member__th--member .member__plan-name {
  background: var(--gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.member__plan-sub {
  display: block;
  margin-top: 4px;
  font-size: 12px;
  font-weight: 400;
  color: var(--text-dim);
}

.member__cell {
  padding: 13px 18px;
  font-size: 14px;
  color: var(--text-sub);
  border-bottom: 1px solid var(--border);
}

.member__cell--member {
  color: var(--text-main);
  font-weight: 600;
  background: rgba(235, 47, 150, 0.06);
}

.member__ability {
  padding: 13px 18px;
  font-size: 14px;
  font-weight: 600;
}

/* 解锁区 */
.member__unlock {
  margin-top: 28px;
  text-align: center;
}

.member__hint {
  font-size: 14px;
  color: var(--text-sub);
}

.member__form {
  margin: 14px auto 0;
  max-width: 460px;
  display: flex;
  gap: 10px;
  padding: 6px;
  border-radius: 999px;
  background: var(--card);
  border: 1px solid var(--border);
}

.member__input {
  flex: 1;
  min-width: 0;
  height: 44px;
  padding: 0 18px;
  border: none;
  outline: none;
  background: transparent;
  color: var(--text-main);
  font-size: 14px;
  border-radius: 999px;
}

.member__input::placeholder {
  color: var(--text-dim);
}

.member__input:focus {
  box-shadow: 0 0 0 2px rgba(235, 47, 150, 0.35) inset;
}

.member__btn {
  height: 44px;
  padding: 0 26px;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 8px;
  white-space: nowrap;
}

.member__error {
  margin-top: 12px;
}

/* 解锁中旋转指示 */
.member__spinner {
  width: 13px;
  height: 13px;
  border: 2px solid rgba(255, 255, 255, 0.4);
  border-top-color: #fff;
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
}

@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}

/* 解锁成功动画: 发光浮现 */
.member__unlocked {
  margin: 0 auto;
  max-width: 420px;
  display: flex;
  align-items: center;
  justify-content: center;
  gap: 14px;
  padding: 22px 26px;
  border-radius: var(--radius);
  background: rgba(82, 196, 26, 0.08);
  border: 1px solid rgba(82, 196, 26, 0.4);
  animation: unlock-glow 0.8s ease both;
}

@keyframes unlock-glow {
  from {
    opacity: 0;
    transform: scale(0.94);
    filter: brightness(1.6);
  }
  to {
    opacity: 1;
    transform: scale(1);
    filter: brightness(1);
  }
}

.member__unlocked-icon {
  font-size: 30px;
}

.member__unlocked-body {
  text-align: left;
}

.member__unlocked-title {
  font-size: 17px;
  font-weight: 700;
  color: var(--success);
}

.member__unlocked-sub {
  margin-top: 4px;
  font-size: 13px;
  color: var(--text-sub);
}

.member__unlocked-expires {
  margin-top: 2px;
  font-size: 12px;
  color: var(--text-dim);
}

@media (max-width: 640px) {
  .member {
    padding: 32px 16px;
  }

  .member__countdown {
    width: 100%;
    justify-content: space-between;
  }

  .member__form {
    flex-direction: column;
    border-radius: var(--radius);
    padding: 10px;
  }

  .member__input,
  .member__btn {
    width: 100%;
  }
}
</style>
