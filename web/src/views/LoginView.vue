<script setup>
/**
 * 登录 / 注册（常规流程：标签页切换，注册成功自动登录）。
 * 注册入口是否显示由服务端 GET /auth/register 的开关决定。
 */
import { onMounted, ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { login, register } from '../store'

const router = useRouter()
const route = useRoute()

const mode = ref('login')        // 'login' | 'register'
const regAllowed = ref(true)
const error = ref('')
const busy = ref(false)

const username = ref('')
const password = ref('')
const confirmPw = ref('')

onMounted(async () => {
  try {
    const data = await api.get('/auth/register')
    regAllowed.value = !!data.allowed
  } catch {
    regAllowed.value = false
  }
})

function switchMode(m) {
  if (m === mode.value) return
  mode.value = m
  error.value = ''
}

function finishRedirect() {
  const next = typeof route.query.next === 'string' ? route.query.next : '/'
  router.push(next)
}

async function submit() {
  const name = username.value.trim()
  if (!name || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  if (mode.value === 'register') {
    if (password.value.length < 4) {
      error.value = '密码至少 4 位'
      return
    }
    if (password.value !== confirmPw.value) {
      error.value = '两次输入的密码不一致'
      return
    }
  }

  busy.value = true
  error.value = ''
  try {
    if (mode.value === 'login') {
      await login(name, password.value)
    } else {
      await register(name, password.value)
    }
    finishRedirect()
  } catch (e) {
    error.value = e.message || (mode.value === 'login' ? '登录失败' : '注册失败')
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="login-wrap">
    <div class="login-card">
      <div class="login-brand">
        <div class="brand-mark">NB</div>
        <div class="login-title">Neu Box</div>
        <div class="login-sub">命令沙盒 · 集群 Master</div>
      </div>

      <!-- 登录 / 注册 切换 -->
      <div class="login-tabs">
        <button class="login-tab" :class="{ active: mode === 'login' }"
                type="button" @click="switchMode('login')">登录</button>
        <button v-if="regAllowed" class="login-tab"
                :class="{ active: mode === 'register' }"
                type="button" @click="switchMode('register')">注册</button>
      </div>

      <div v-if="error" class="login-error">{{ error }}</div>

      <form @submit.prevent="submit">
        <div class="field">
          <label class="field-label">用户名</label>
          <input v-model="username" class="input"
                 :placeholder="mode === 'register' ? '3-32 位字母、数字、_、. 或 -' : '输入用户名'"
                 autocomplete="username" autofocus>
        </div>
        <div class="field">
          <label class="field-label">密码
            <span v-if="mode === 'register'" class="field-hint">至少 4 位</span>
          </label>
          <input v-model="password" type="password" class="input" placeholder="输入密码"
                 :autocomplete="mode === 'register' ? 'new-password' : 'current-password'">
        </div>
        <div v-if="mode === 'register'" class="field">
          <label class="field-label">确认密码</label>
          <input v-model="confirmPw" type="password" class="input" placeholder="再次输入密码"
                 autocomplete="new-password">
        </div>
        <button class="btn btn-primary" style="width:100%;padding:9px"
                :disabled="busy" type="submit">
          <Icon v-if="busy" name="refresh" :size="14" class="spin" />
          {{ busy ? '处理中…' : (mode === 'login' ? '登 录' : '注 册') }}
        </button>
      </form>

      <p v-if="mode === 'register'" class="login-footnote">
        注册账号默认为普通用户，可提交任务和查看主队列；
        管理员权限由管理员在「用户管理」中授予。
      </p>
    </div>
  </div>
</template>
