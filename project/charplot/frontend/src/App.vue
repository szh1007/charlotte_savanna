<script setup lang="ts">
// 应用壳 (DESIGN.md §6 页面结构 1): 渐变背景 + 顶部导航.
// 导航 = Logo + 登录态(徽章组 + 个人主页入口 + 登出) / 游客态(登录/注册).
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage } from 'element-plus'
import { ApiError } from '@/api/client'
import { useAuth } from '@/stores/auth'

const router = useRouter()
const { state, init, logout } = useAuth()

onMounted(() => {
  init().catch(() => {
    // 后端不可达: 保持游客态, 页面请求自行报错
  })
})

async function onLogout() {
  try {
    await logout()
    ElMessage.success('已退出登录')
    router.push('/')
  } catch (e) {
    ElMessage.error(e instanceof ApiError ? e.message : '退出失败，请稍后重试')
  }
}
</script>

<template>
  <div class="app-shell">
    <header class="nav">
      <router-link to="/" class="brand">
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
      </router-link>

      <nav class="nav-actions" aria-label="用户操作">
        <!-- 已登录: 游戏化状态徽章组 + 个人主页 + 登出 -->
        <template v-if="state.user">
          <div class="badges" aria-label="游戏化状态">
            <span class="nav-badge" title="等级">
              <span class="badge-star" aria-hidden="true">★</span>{{ state.profile?.level ?? 1 }}
            </span>
            <span class="nav-badge badge-flame" title="连胜">
              <span class="flame-dot" aria-hidden="true">🔥</span>{{ state.profile?.streak ?? 0 }}
            </span>
            <span class="nav-badge" title="心动值">
              <span aria-hidden="true">💗</span>{{ state.profile?.hearts ?? 5 }}
            </span>
            <span class="nav-badge" title="学习币">
              <span aria-hidden="true">🪙</span>{{ state.profile?.coins ?? 0 }}
            </span>
          </div>
          <el-button size="small" round @click="router.push('/profile')">
            {{ state.user.username }}
          </el-button>
          <el-button size="small" round plain @click="onLogout">登出</el-button>
        </template>

        <!-- 游客: 登录 / 注册 -->
        <template v-else>
          <el-button size="small" round @click="router.push('/login')">登录</el-button>
          <el-button size="small" round type="primary" @click="router.push('/register')">
            注册
          </el-button>
        </template>
      </nav>
    </header>

    <main class="page">
      <router-view />
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
  justify-content: space-between;
  padding: 18px 32px;
  background: rgba(255, 255, 255, 0.72);
  backdrop-filter: blur(12px);
  border-bottom: 1px solid rgba(251, 114, 153, 0.1);
}

.brand {
  display: flex;
  align-items: center;
  gap: 12px;
  text-decoration: none;
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

.nav-actions {
  display: flex;
  align-items: center;
  gap: 10px;
}

.badges {
  display: flex;
  align-items: center;
  gap: 6px;
  margin-right: 4px;
}

.nav-badge {
  display: inline-flex;
  align-items: center;
  gap: 3px;
  font-size: 12px;
  font-weight: 700;
  color: var(--cp-ink);
  background: var(--cp-card);
  border: 1px solid rgba(251, 114, 153, 0.18);
  padding: 4px 10px;
  border-radius: 999px;
  box-shadow: 0 2px 8px rgba(251, 114, 153, 0.08);
}

.badge-star {
  color: #ffb400;
}

/* 连胜火焰: 呼吸动画, 与个人主页签名元素呼应 */
.flame-dot {
  display: inline-block;
  animation: navFlame 2.6s ease-in-out infinite;
  transform-origin: 50% 85%;
}

.badge-flame {
  background: linear-gradient(160deg, #fff7e8, var(--cp-card));
  border-color: rgba(245, 166, 35, 0.3);
}

@keyframes navFlame {
  0%,
  100% {
    transform: scale(1);
  }
  50% {
    transform: scale(1.2);
  }
}

.page {
  flex: 1;
  padding: 40px 24px 64px;
}
</style>
