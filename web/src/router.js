import { createRouter, createWebHistory } from 'vue-router'
import { auth, fetchMe } from './store'
import LoginView from './views/LoginView.vue'
import DashboardView from './views/DashboardView.vue'
import TasksView from './views/TasksView.vue'
import ExperimentsView from './views/ExperimentsView.vue'
import ExperimentDetailView from './views/ExperimentDetailView.vue'
import SettingsView from './views/SettingsView.vue'
import AdminUsersView from './views/AdminUsersView.vue'
import AdminNodesView from './views/AdminNodesView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/login', name: 'login', component: LoginView, meta: { public: true } },
    { path: '/', name: 'dashboard', component: DashboardView },
    { path: '/tasks', name: 'tasks', component: TasksView },
    { path: '/experiments', name: 'experiments', component: ExperimentsView },
    { path: '/experiments/:id', name: 'experiment-detail', component: ExperimentDetailView },
    { path: '/settings', name: 'settings', component: SettingsView },
    { path: '/admin/users', name: 'admin-users', component: AdminUsersView, meta: { admin: true } },
    { path: '/admin/nodes', name: 'admin-nodes', component: AdminNodesView, meta: { admin: true } },
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})

let checking = null

router.beforeEach(async (to) => {
  // 登录页：已登录则回首页
  if (to.name === 'login') {
    if (auth.user) return { path: '/' }
    return true
  }

  if (!auth.checked) {
    if (!checking) checking = fetchMe().finally(() => { checking = null })
    await checking
  }

  if (!auth.user) {
    return { name: 'login', query: { next: to.fullPath } }
  }
  if (to.meta.admin && auth.user.role !== 'admin') {
    return { name: 'dashboard' }
  }
  return true
})

router.afterEach((to) => {
  const titles = {
    dashboard: '概览', tasks: '任务', experiments: '实验',
    'experiment-detail': '实验详情', settings: '设置',
    'admin-users': '用户管理', 'admin-nodes': '节点管理',
  }
  const t = titles[to.name]
  document.title = t ? `Neu Box — ${t}` : 'Neu Box'
})

export { router }
