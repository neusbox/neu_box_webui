<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import Icon from '../components/Icon.vue'
import { login } from '../store'

const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  if (!username.value.trim() || !password.value) {
    error.value = '请输入用户名和密码'
    return
  }
  busy.value = true
  error.value = ''
  try {
    await login(username.value.trim(), password.value)
    const next = typeof route.query.next === 'string' ? route.query.next : '/'
    router.push(next)
  } catch (e) {
    error.value = e.message || '登录失败'
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

      <div v-if="error" class="login-error">{{ error }}</div>

      <form @submit.prevent="submit">
        <div class="field">
          <label class="field-label">用户名</label>
          <input v-model="username" class="input" placeholder="输入用户名"
                 autocomplete="username" autofocus>
        </div>
        <div class="field">
          <label class="field-label">密码</label>
          <input v-model="password" type="password" class="input" placeholder="输入密码"
                 autocomplete="current-password">
        </div>
        <button class="btn btn-primary" style="width:100%;padding:9px"
                :disabled="busy" type="submit">
          <Icon v-if="busy" name="refresh" :size="14" class="spin" />
          {{ busy ? '登录中…' : '登 录' }}
        </button>
      </form>
    </div>
  </div>
</template>
