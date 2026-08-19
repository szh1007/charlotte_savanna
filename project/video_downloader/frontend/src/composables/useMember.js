import { ref } from 'vue'
import {
  fetchMemberStatus,
  getMemberToken,
  setMemberToken,
  submitMemberKey,
} from '../api/client.js'

// 会员状态组合式 (T09): 解锁 / 恢复 / 清除全站会员身份.
// 与解析 / 下载 / SSE 逻辑解耦 (Home 仅接线), 组件树内单一数据源
export function useMember({ onUnlocked } = {}) {
  const isMember = ref(false)
  const memberExpires = ref(null)
  const memberSubmitting = ref(false)
  const memberError = ref('')

  // 解锁: 提交密钥 → 持久化 token → 全站状态生效;
  // onUnlocked 回调由调用方决定解锁后的联动 (如自动重新解析)
  async function handleUnlock(key) {
    memberSubmitting.value = true
    memberError.value = ''
    try {
      const { token, expires_at } = await submitMemberKey(key)
      setMemberToken(token) // 先持久化, 后续请求自动携带 X-Member-Token
      isMember.value = true
      memberExpires.value = expires_at
      onUnlocked?.()
    } catch (e) {
      memberError.value = e.message
    } finally {
      memberSubmitting.value = false
    }
  }

  // 恢复会员状态: 有本地 token 时经状态接口确认 (刷新页面后仍保持会员)
  async function restoreMember() {
    if (!getMemberToken()) return
    try {
      const { is_member, expires_at } = await fetchMemberStatus()
      isMember.value = is_member
      memberExpires.value = expires_at
      if (!is_member) {
        setMemberToken(null) // token 已过期/失效: 清除本地残留
      }
    } catch {
      // 后端不可用时保留现有状态, 不误清 token
    }
  }

  return {
    isMember,
    memberExpires,
    memberSubmitting,
    memberError,
    handleUnlock,
    restoreMember,
  }
}
