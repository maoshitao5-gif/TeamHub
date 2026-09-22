/**
 * 路由配置
 * 包含本地功能页和云服务认证页
 */
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/library'
  },
  // 云服务认证路由
  { path: '/login', name: 'Login', component: () => import('../pages/LoginPage.vue'), meta: { title: '登录', requiresCloudAuth: false } },
  { path: '/register', name: 'Register', component: () => import('../pages/RegisterPage.vue'), meta: { title: '注册', requiresCloudAuth: false } },
  {
    path: '/library',
    name: 'Library',
    component: () => import('../pages/LibraryPage.vue'),
    meta: { title: '文档库' }
  },
  {
    path: '/pending',
    name: 'Pending',
    component: () => import('../pages/PendingPage.vue'),
    meta: { title: '待整理' }
  },
  {
    path: '/tags',
    name: 'TagManage',
    component: () => import('../pages/TagManagePage.vue'),
    meta: { title: '标签管理' }
  },
  {
    path: '/trash',
    name: 'Trash',
    component: () => import('../pages/TrashPage.vue'),
    meta: { title: '回收站' }
  },
  {
    path: '/settings',
    name: 'Settings',
    component: () => import('../pages/SettingsPage.vue'),
    meta: { title: '设置' }
  },
  {
    path: '/shared',
    name: 'SharedInbox',
    component: () => import('../pages/SharedInboxPage.vue'),
    meta: { title: '云仓库', requiresCloudAuth: true }
  },
  { path: '/team', name: 'Team', component: () => import('../pages/TeamPage.vue'), meta: { title: '团队管理' } },
  { path: '/onboarding', name: 'Onboarding', component: () => import('../pages/OnboardingPage.vue'), meta: { title: '入职引导', requiresCloudAuth: true } },
  {
    path: '/setup',
    name: 'Setup',
    component: () => import('../pages/SetupPage.vue'),
    meta: { title: '初始化' }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

router.beforeEach((to, from, next) => {
  document.title = to.meta.title ? `${to.meta.title} - TeamHub` : 'TeamHub'

  if (to.meta?.requiresCloudAuth) {
    // 动态 import 避免循环依赖
    import('../stores/auth').then(({ useAuthStore }) => {
      const authStore = useAuthStore()
      if (!authStore.isLoggedIn) {
        next({ path: '/login', query: { redirect: to.fullPath } })
      } else if (authStore.needsOnboarding && to.name !== 'Onboarding') {
        // 已登录但未加入团队 → 引导入职
        next({ path: '/onboarding' })
      } else {
        next()
      }
    })
  } else {
    next()
  }
})

router.onError((error) => {
  console.error('Router error:', error)
})

export default router
