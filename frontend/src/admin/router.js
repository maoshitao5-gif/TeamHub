import { createRouter, createWebHashHistory } from 'vue-router'
import LoginPage from './pages/LoginPage.vue'
import DashboardPage from './pages/DashboardPage.vue'
import UsersPage from './pages/UsersPage.vue'
import TeamsPage from './pages/TeamsPage.vue'

const routes = [
  { path: '/login', component: LoginPage },
  {
    path: '/',
    redirect: '/dashboard',
    meta: { requiresAuth: true },
  },
  { path: '/dashboard', component: DashboardPage, meta: { requiresAuth: true } },
  { path: '/users', component: UsersPage, meta: { requiresAuth: true } },
  { path: '/teams', component: TeamsPage, meta: { requiresAuth: true } },
  { path: '/:pathMatch(.*)*', redirect: '/dashboard' },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

router.beforeEach((to) => {
  const token = localStorage.getItem('admin_token')
  if (to.meta.requiresAuth && !token) {
    return '/login'
  }
})

export default router
