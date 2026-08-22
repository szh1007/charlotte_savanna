// 认证状态单例 (无 pinia): 仅 auth + profile 两个状态, reactive 单例足够.
// 状态规模变大 (Issue 03+ 旅程/答题) 再平滑迁移 pinia.
import { reactive } from 'vue'
import * as api from '@/api/client'

interface AuthState {
  initialized: boolean
  user: api.SessionUser | null
  profile: api.Profile | null
}

const state = reactive<AuthState>({
  initialized: false,
  user: null,
  profile: null,
})

export function useAuth() {
  /** SPA 启动: 探测会话 (同时建立 csrftoken cookie) + 拉取个人主页. */
  async function init(): Promise<void> {
    const session = await api.getSession()
    state.user = session.authenticated ? session.user : null
    if (state.user) {
      await refreshProfile()
    }
    state.initialized = true
  }

  async function refreshProfile(): Promise<void> {
    state.profile = await api.getProfile()
  }

  async function login(username: string, password: string): Promise<void> {
    state.user = await api.login({ username, password })
    await refreshProfile()
  }

  async function register(data: {
    username: string
    email: string
    password: string
  }): Promise<void> {
    await api.register(data)
  }

  async function logout(): Promise<void> {
    await api.logout()
    state.user = null
    state.profile = null
  }

  /** 兑换连胜冻结, 重新拉取完整 profile (streak_loss_warning 随之刷新). */
  async function buyStreakFreeze(): Promise<{ coins: number; frozen: string }> {
    const res = await api.buyStreakFreeze()
    await refreshProfile()
    return res
  }

  return { state, init, refreshProfile, login, register, logout, buyStreakFreeze }
}
