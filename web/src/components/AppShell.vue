<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { authRO } from '../store'
import Icon from './Icon.vue'

const props = defineProps({
  dark: { type: Boolean, default: false },
})
defineEmits(['toggle-theme', 'logout'])

const route = useRoute()
const user = computed(() => authRO.user)
const isAdmin = computed(() => user.value?.role === 'admin')

const nav = [
  { name: 'dashboard', label: '概览', icon: 'grid' },
  { name: 'tasks', label: '任务', icon: 'terminal' },
  { name: 'experiments', label: '实验', icon: 'flask' },
]
const adminNav = [
  { name: 'admin-users', label: '用户管理', icon: 'users' },
  { name: 'admin-nodes', label: '节点管理', icon: 'server' },
]

function isActive(name) {
  if (route.name === name) return true
  // 实验详情也高亮“实验”
  if (name === 'experiments' && route.name === 'experiment-detail') return true
  return false
}
</script>

<template>
  <div class="shell">
    <aside class="sidebar">
      <div class="brand">
        <div class="brand-mark">NB</div>
        <div>
          <div class="brand-name">Neu Box</div>
          <div class="brand-sub">命令沙盒 · Master</div>
        </div>
      </div>

      <nav>
        <div v-for="item in nav" :key="item.name"
             class="nav-item" :class="{ active: isActive(item.name) }"
             @click="$router.push({ name: item.name })">
          <Icon :name="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </div>

        <div v-if="isAdmin" class="nav-section">管理</div>
        <div v-for="item in (isAdmin ? adminNav : [])" :key="item.name"
             class="nav-item" :class="{ active: isActive(item.name) }"
             @click="$router.push({ name: item.name })">
          <Icon :name="item.icon" :size="16" />
          <span>{{ item.label }}</span>
        </div>

        <div v-if="user" class="nav-item" @click="$router.push({ name: 'settings' })">
          <Icon name="settings" :size="16" />
          <span>设置</span>
        </div>
      </nav>

      <div class="sidebar-footer">
        <div v-if="user" class="row" style="margin-bottom:6px">
          <button class="btn btn-ghost btn-sm grow" @click="$emit('toggle-theme')">
            <Icon name="settings" :size="14" />
            <span>{{ dark ? '浅色模式' : '深色模式' }}</span>
          </button>
        </div>
        <div v-if="user" class="user-chip">
          <div class="user-avatar">{{ (user.username || '?').slice(0, 1) }}</div>
          <div class="user-meta">
            <div class="user-name ellipsis">{{ user.username }}</div>
            <div class="user-role">{{ user.role === 'admin' ? '管理员' : '用户' }}</div>
          </div>
          <button class="btn btn-ghost btn-icon" title="退出登录" @click="$emit('logout')">
            <Icon name="logout" :size="15" />
          </button>
        </div>
      </div>
    </aside>

    <main class="main">
      <slot />
    </main>
  </div>
</template>
