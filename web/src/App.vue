<script setup>
import { onBeforeUnmount, onMounted, ref } from 'vue'
import AppShell from './components/AppShell.vue'
import Toasts from './components/Toasts.vue'
import { useRouter, useRoute } from 'vue-router'
import { logout } from './store'

const router = useRouter()
const route = useRoute()

// ── 主题（浅色默认，记忆到 localStorage） ──────────────────
const dark = ref(localStorage.getItem('neu_theme') === 'dark')
function applyTheme() {
  if (dark.value) document.documentElement.setAttribute('data-theme', 'dark')
  else document.documentElement.removeAttribute('data-theme')
}
function toggleTheme() {
  dark.value = !dark.value
  localStorage.setItem('neu_theme', dark.value ? 'dark' : 'light')
  applyTheme()
}
applyTheme()

// ── 会话失效 → 回登录页 ────────────────────────────────────
function onUnauthorized() {
  if (route.name === 'login') return
  router.push({ name: 'login', query: { next: route.fullPath } })
}
onMounted(() => window.addEventListener('neu:unauthorized', onUnauthorized))
onBeforeUnmount(() => window.removeEventListener('neu:unauthorized', onUnauthorized))

async function onLogout() {
  await logout()
  router.push({ name: 'login' })
}
</script>

<template>
  <router-view v-if="route.name === 'login' || route.name === undefined" />
  <AppShell v-else :dark="dark" @toggle-theme="toggleTheme" @logout="onLogout">
    <router-view />
  </AppShell>
  <Toasts />
</template>
