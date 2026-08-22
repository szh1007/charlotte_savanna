<script setup lang="ts">
// 应用壳: 渐变背景 + 顶部导航 (DESIGN.md §6 页面结构 1)
import Home from '@/views/Home.vue'
</script>

<template>
  <div class="app-shell">
    <header class="nav">
      <div class="brand">
        <div class="brand-logo" aria-hidden="true">
          <svg viewBox="0 0 64 64" width="30" height="30">
            <defs>
              <linearGradient id="logoGrad" x1="0" y1="0" x2="1" y2="1">
                <stop offset="0" stop-color="#fb7299" />
                <stop offset="1" stop-color="#c9b6e4" />
              </linearGradient>
            </defs>
            <rect x="2" y="2" width="60" height="60" rx="16" fill="url(#logoGrad)" />
            <path
              d="M18 46V18l28 28V18"
              stroke="#fff"
              stroke-width="6"
              stroke-linecap="round"
              stroke-linejoin="round"
              fill="none"
            />
          </svg>
        </div>
        <span class="brand-name">CharPlot</span>
        <span class="brand-slogan">闯关学知识</span>
      </div>
    </header>

    <main class="page">
      <Home />
    </main>
  </div>
</template>

<style scoped>
.app-shell {
  min-height: 100vh;
  display: flex;
  flex-direction: column;
}

.nav {
  display: flex;
  align-items: center;
  padding: 18px 32px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(251, 114, 153, 0.1);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
}

.brand-logo {
  display: flex;
  filter: drop-shadow(0 4px 10px rgba(251, 114, 153, 0.35));
}

.brand-name {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.5px;
  color: var(--cp-ink);
}

.brand-slogan {
  font-size: 13px;
  color: var(--cp-ink-soft);
  padding: 3px 10px;
  border-radius: 999px;
  background: var(--cp-primary-soft);
}

.page {
  flex: 1;
  padding: 40px 24px 64px;
}
</style>
