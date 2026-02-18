/**
 * 路由配置
 * 四个主页面：文档库、待整理、标签管理、设置
 */
import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  {
    path: '/',
    redirect: '/library'
  },
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
  next()
})

export default router
