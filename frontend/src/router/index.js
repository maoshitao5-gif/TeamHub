/**
 * 路由配置
 */
import { createRouter, createWebHistory } from 'vue-router'
import UploadPage from '../pages/UploadPage.vue'
import FileListPage from '../pages/FileListPage.vue'
import TagManagePage from '../pages/TagManagePage.vue'

const routes = [
  {
    path: '/',
    redirect: '/files' // 默认跳转到文件列表页
  },
  {
    path: '/upload',
    name: 'Upload',
    component: UploadPage,
    meta: {
      title: '文件上传'
    }
  },
  {
    path: '/files',
    name: 'FileList',
    component: FileListPage,
    meta: {
      title: '文件列表'
    }
  },
  {
    path: '/tags',
    name: 'TagManage',
    component: TagManagePage,
    meta: {
      title: '标签管理'
    }
  }
]

const router = createRouter({
  history: createWebHistory(),
  routes
})

// 路由守卫：设置页面标题
router.beforeEach((to, from, next) => {
  // 设置页面标题
  document.title = to.meta.title ? `${to.meta.title} - 团队文件管理系统` : '团队文件管理系统'
  next()
})

export default router
