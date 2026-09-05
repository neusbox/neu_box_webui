import { reactive, readonly } from 'vue'
import { api } from './api'

/**
 * 极简全局状态：当前用户 + toast。
 * 有意不用 pinia —— 状态面很小。
 */

export const auth = reactive({
  user: null,        // {id, username, role}
  checked: false,    // 是否已尝试 /auth/me
})

async function fetchMe() {
  try {
    const data = await api.get('/auth/me')
    auth.user = data.user
  } catch {
    auth.user = null
  } finally {
    auth.checked = true
  }
  return auth.user
}

async function login(username, password) {
  const data = await api.post('/auth/login', { username, password })
  auth.user = data.user
  return data.user
}

async function register(username, password) {
  const data = await api.post('/auth/register', { username, password })
  auth.user = data.user   // 注册成功即自动登录
  return data.user
}

async function logout() {
  try { await api.post('/auth/logout') } catch { /* 忽略 */ }
  auth.user = null
}

// ── Toast ────────────────────────────────────────────────────

const toasts = reactive([])
let toastSeq = 0

export function toast(message, type = 'info', duration = 3000) {
  const id = ++toastSeq
  toasts.push({ id, message, type })
  setTimeout(() => dismissToast(id), duration)
}

export function dismissToast(id) {
  const i = toasts.findIndex(t => t.id === id)
  if (i >= 0) toasts.splice(i, 1)
}

export { fetchMe, login, register, logout, toasts }
export const authRO = readonly(auth)
