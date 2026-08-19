<script setup>
// 顶部导航栏: Logo + 会员入口 (渐变描边按钮, T09 接通会员功能)
// 未解锁: 点击打开会员弹窗输入密钥; 已解锁: 展示会员身份徽章
const props = defineProps({
  // 当前会话是否会员 (由 Home 统一管理, 刷新后经状态接口恢复)
  isMember: { type: Boolean, default: false },
})

const emit = defineEmits(['go-member'])

function handleClick() {
  // 已解锁时点击仍可打开弹窗查看权益详情
  emit('go-member')
}
</script>

<template>
  <header class="nav">
    <div class="container nav__inner">
      <a href="#" class="nav__logo">
        <span class="nav__logo-icon">🅱️</span>
        <span class="nav__logo-text">BilibiliDownloader</span>
      </a>
      <button
        class="btn-outline-gradient nav__member"
        :class="{ 'nav__member--active': isMember }"
        type="button"
        @click="handleClick"
      >
        <span>{{ isMember ? '✓' : '✦' }}</span>
        {{ isMember ? '会员已解锁' : '会员解锁' }}
      </button>
    </div>
  </header>
</template>

<style scoped>
.nav {
  position: sticky;
  top: 0;
  z-index: 100;
  height: var(--nav-height);
  background: rgba(255, 255, 255, 0.85);
  backdrop-filter: blur(12px);
  -webkit-backdrop-filter: blur(12px);
  border-bottom: 1px solid var(--border);
}

.nav__inner {
  height: 100%;
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.nav__logo {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  font-size: 20px;
  font-weight: 700;
}

.nav__logo-icon {
  font-size: 22px;
}

.nav__logo-text {
  background: var(--gradient);
  -webkit-background-clip: text;
  background-clip: text;
  color: transparent;
}

.nav__member {
  padding: 8px 20px;
  font-size: 14px;
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.nav__member span {
  color: var(--primary);
}

/* 已解锁态: 实心渐变徽章, 与「会员解锁」引导按钮区分 */
.nav__member--active {
  background: var(--gradient);
  color: #fff;
}

.nav__member--active span {
  color: #fff;
}

@media (max-width: 640px) {
  .nav__member {
    padding: 7px 16px;
    font-size: 13px;
  }
}
</style>
