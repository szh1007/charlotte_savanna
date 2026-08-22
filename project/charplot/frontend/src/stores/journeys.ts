// 旅程状态单例 (无 pinia, 同 auth store 模式): 列表 + 当前详情.
// SSE 生命周期 (连接/关闭/重试) 由 JourneyDetail.vue 组件管理,
// 连接对象不放进响应式单例, 避免事件循环泄漏.
import { reactive } from 'vue'
import * as api from '@/api/client'

interface JourneysState {
  list: api.JourneySummary[] | null
  current: api.JourneyDetail | null
}

const state = reactive<JourneysState>({
  list: null,
  current: null,
})

export function useJourneys() {
  async function refreshList(): Promise<void> {
    const res = await api.getJourneys()
    state.list = res.journeys
  }

  async function loadJourney(id: number): Promise<api.JourneyDetail> {
    state.current = await api.getJourney(id)
    return state.current
  }

  return { state, refreshList, loadJourney }
}
