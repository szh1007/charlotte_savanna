// 路由表 + 守卫 (Issue 02): /profile 需登录, /login /register 仅游客.
import { createRouter, createWebHistory } from 'vue-router'
import { useAuth } from '@/stores/auth'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'home', component: () => import('@/views/Home.vue') },
    {
      path: '/login',
      name: 'login',
      component: () => import('@/views/Login.vue'),
      meta: { guestOnly: true },
    },
    {
      path: '/register',
      name: 'register',
      component: () => import('@/views/Register.vue'),
      meta: { guestOnly: true },
    },
    {
      path: '/profile',
      name: 'profile',
      component: () => import('@/views/Profile.vue'),
      meta: { requiresAuth: true },
    },
    {
      path: '/journeys/:id',
      name: 'journey-detail',
      component: () => import('@/views/JourneyDetail.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 闯关地图 (Issue 04): 技能树可视化 + 点亮状态 + 关卡入口
      path: '/journeys/:id/map',
      name: 'journey-map',
      component: () => import('@/views/SkillTreeMap.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 关卡入口 (Issue 05): 关卡列表 + 状态/进度
      path: '/journeys/:id/levels',
      name: 'level-list',
      component: () => import('@/views/LevelList.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 答题页 (Issue 05): 答题 → 反馈 → 结算/重开, 断点续答
      path: '/journeys/:id/levels/:levelId',
      name: 'level-quiz',
      component: () => import('@/views/QuizView.vue'),
      meta: { requiresAuth: true },
    },
    {
      // 复盘报告页 (Issue 06): 知识总结 + 答题表现 + 公开分享链接
      path: '/journeys/:id/report',
      name: 'journey-report',
      component: () => import('@/views/ReportView.vue'),
      meta: { requiresAuth: true },
    },
  ],
})

router.beforeEach(async (to) => {
  const { state, init } = useAuth()
  if (!state.initialized) {
    try {
      await init()
    } catch {
      // 后端不可达时放行, 页面自行处理请求失败
    }
  }
  if (to.meta.requiresAuth && !state.user) {
    return { name: 'login', query: { redirect: to.fullPath } }
  }
  if (to.meta.guestOnly && state.user) {
    return { name: 'home' }
  }
  return true
})

export default router
