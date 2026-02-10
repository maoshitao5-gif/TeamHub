/**
 * 路由配置
 */
import { createRouter, createWebHistory } from 'vue-router'
import { ElMessage } from 'element-plus'
import UploadPage from '../pages/UploadPage.vue'
import FileListPage from '../pages/FileListPage.vue'
import TagManagePage from '../pages/TagManagePage.vue'
import LoginPage from '../pages/LoginPage.vue'
import AdminPage from '../pages/AdminPage.vue'
import { isLoggedIn, isAdmin } from '../api/auth'

const routes = [
  {
    path: '/',
    redirect: '/files' // 默认跳转到文件列表页
  },
  {
    path: '/login',
    name: 'Login',
    component: LoginPage,
    meta: {
      title: '登录',
      requiresAuth: false  // 登录页不需要认证
    }
  },
  {
    path: '/upload',
    name: 'Upload',
    component: UploadPage,
    meta: {
      title: '文件上传',
      requiresAuth: true  // 需要登录
    }
  },
  {
    path: '/files',
    name: 'FileList',
    component: FileListPage,
    meta: {
      title: '文件列表',
      requiresAuth: true  // 需要登录
    }
  },
  {
    path: '/tags',
    name: 'TagManage',
    component: TagManagePage,
    meta: {
      title: '标签管理',
      requiresAuth: true  // 需要登录
    }
  },
  {
    path: '/admin',
    name: 'Admin',
    component: AdminPage,
    meta: {
      title: '管理后台',
      requiresAuth: true,
      requiresAdmin: true  // 需要管理员权限
    }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫：认证检查和设置页面标题
router.beforeEach((to, from, next) => {
  // 设置页面标题
  document.title = to.meta.title ? `${to.meta.title} - 团队文件管理系统` : '团队文件管理系统'
  
  // 检查是否需要认证
  if (to.meta.requiresAuth !== false) {
    // 需要登录的页面
    if (!isLoggedIn()) {
      ElMessage.warning('请先登录')
      next({
        path: '/login',
        query: { redirect: to.fullPath }  // 保存原始路径，登录后可以跳转回来
      })
      return
    }
    
    // 检查是否需要管理员权限
    if (to.meta.requiresAdmin && !isAdmin()) {
      ElMessage.error('权限不足，需要管理员权限')
      next('/files')
      return
    }
  } else {
    // 登录页，如果已登录则跳转到文件列表
    if (to.path === '/login' && isLoggedIn()) {
      next('/files')
      return
    }
  }
  
  next()
})

export default router
