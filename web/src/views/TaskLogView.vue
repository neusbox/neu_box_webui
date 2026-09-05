<script setup>
/**
 * 任务日志独立页（全宽）：/tasks/:nodeId/:taskId/log
 *
 * 从任务页「新页面打开」进入：全宽查看长日志，支持复制/导出，
 * 任务运行中默认每 5s 自动刷新（可暂停）。
 * 权限与内嵌日志一致：仅任务属主/管理员（服务端校验）。
 */
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import Icon from '../components/Icon.vue'
import { api } from '../api'
import { toast } from '../store'
import { handleCR, statusLabel, formatTime, downloadText } from '../utils'

const route = useRoute()
const router = useRouter()
const nodeId = route.params.nodeId
const taskId = route.params.taskId

const task = ref(null)
const nodeName = ref('')
const logText = ref('')
const state = ref('loading')       // loading | done | error
const errorMsg = ref('')
const progress = ref({ loaded: 0, total: 0 })
const autoRefresh = ref(true)
let seq = 0
let timer = null

async function loadMeta() {
  try {
    const d = await api.post('/nodes/get_all_nodes', {})
    nodeName.value = (d.nodes || []).find(n => n.node_id === nodeId)?.name || nodeId
  } catch { /* 节点名拿不到不影响看日志 */ }
  try {
    task.value = await api.get(`/tasks/${taskId}?node_id=${encodeURIComponent(nodeId)}`)
  } catch {
    task.value = null   // 任务可能已被 worker 清理，日志接口仍可能可读
  }
}

async function loadLog() {
  const s = ++seq
  if (state.value !== 'done') state.value = 'loading'
  errorMsg.value = ''
  try {
    const text = await api.getTextWithProgress(
      `/tasks/${taskId}/log?node_id=${encodeURIComponent(nodeId)}&raw=1`,
      (p) => { if (s === seq) progress.value = p },
    )
    if (s !== seq) return
    logText.value = handleCR(text)
    state.value = 'done'
  } catch (e) {
    if (s !== seq) return
    errorMsg.value = e.message
    state.value = 'error'
  }
}

// 任务运行中 → 每 5s 自动刷新；完成/失败后停止
function schedule() {
  if (timer) { clearInterval(timer); timer = null }
  if (autoRefresh.value && task.value?.status === 'running') {
    timer = setInterval(() => {
      if (!document.hidden) loadLog()
    }, 5000)
  }
}
watch(() => task.value?.status, schedule)
watch(autoRefresh, schedule)

onMounted(async () => {
  await loadMeta()
  await loadLog()
  schedule()
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })

const isRunning = computed(() => task.value?.status === 'running')

const progressPct = computed(() => {
  const { loaded, total } = progress.value
  if (!total) return 6
  return Math.max(6, Math.round(loaded / total * 100))
})
const progressText = computed(() => {
  const { loaded, total } = progress.value
  if (!total) return '加载中…'
  return `${Math.round(loaded / total * 100)}%   ${(loaded / 1024).toFixed(0)} / ${(total / 1024).toFixed(0)} KB`
})

async function copyLog() {
  if (!logText.value) return
  try {
    await navigator.clipboard.writeText(logText.value)
    toast('日志已复制到剪贴板', 'success')
  } catch {
    toast('复制失败（浏览器未授权剪贴板）', 'error')
  }
}

function exportLog() {
  if (!logText.value) return
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  downloadText(`${taskId}_${ts}.log`, logText.value)
  toast('日志已导出', 'success')
}
</script>

<template>
  <div>
    <div class="page-head">
      <div style="display:flex;align-items:center;gap:12px;min-width:0">
        <button class="btn" title="返回任务页" @click="router.push('/tasks')">
          <Icon name="chevron-right" :size="14" style="transform:rotate(180deg)" /> 任务
        </button>
        <div style="min-width:0">
          <h1 class="page-title mono" style="font-size:15px">
            任务日志 <span class="text-3">/</span> {{ taskId }}
          </h1>
          <div class="page-desc mono" style="word-break:break-all">
            <template v-if="task">
              <span class="text-3">[{{ nodeName }}]</span> {{ task.command }}
            </template>
            <template v-else>命令未知（任务可能已被节点清理）</template>
          </div>
        </div>
      </div>
      <div class="page-actions">
        <span v-if="task" class="badge" :class="task.status">
          <span class="dot" />{{ statusLabel(task.status) }}
        </span>
        <label v-if="isRunning" class="text-3 small" style="cursor:pointer">
          <input type="checkbox" v-model="autoRefresh" style="vertical-align:-2px">
          自动刷新 (5s)
        </label>
        <button class="btn" @click="loadLog">
          <Icon name="refresh" :size="14" :class="{ spin: state === 'loading' }" /> 刷新
        </button>
        <button class="btn" :disabled="!logText" @click="copyLog">
          <Icon name="terminal" :size="14" /> 复制
        </button>
        <button class="btn" :disabled="!logText" @click="exportLog">
          <Icon name="download" :size="14" /> 导出
        </button>
      </div>
    </div>

    <div class="card log-page-card">
      <div v-if="task" class="log-meta">
        <span class="k">用户</span><span class="v">{{ task.user_id }}</span>
        <span class="k">节点</span><span class="v">{{ nodeName }}</span>
        <span class="k">资源</span>
        <span class="v">
          CPU={{ task.cpu || 0 }}  内存={{ task.mem || 0 }}  设备={{ task.device_num || 0 }}
          <template v-if="task.devices && task.devices.length">（{{ task.devices.join(', ') }}）</template>
        </span>
        <span class="k">创建</span><span class="v">{{ formatTime(task.created_at) }}</span>
        <span class="k">状态</span>
        <span class="v">
          {{ statusLabel(task.status) }}
          <template v-if="task.result">
            · 返回码 {{ task.result.returncode }}
            <span v-if="task.result.timed_out" class="text-danger">（超时）</span>
          </template>
        </span>
      </div>

      <div v-if="state === 'loading' && !logText" class="log-progress">
        <i :style="{ width: progressPct + '%' }"></i>
        <span>{{ progressText }}</span>
      </div>

      <div v-else-if="state === 'error'" class="empty-state">
        <div class="icon text-danger">⚠</div>
        <p class="mt-8 text-danger">{{ errorMsg }}</p>
        <p class="small text-3">仅任务本人或管理员可查看日志</p>
      </div>

      <div v-else class="log-body">
        <pre>{{ logText || '(无输出)' }}</pre>
      </div>

      <div v-if="state === 'done'" class="log-toolbar">
        <span class="text-3 small grow">{{ logText.length.toLocaleString() }} 字符
          <template v-if="isRunning && autoRefresh"> · 运行中，每 5s 自动刷新</template>
        </span>
        <button class="btn btn-sm" :disabled="!logText" @click="copyLog">
          <Icon name="terminal" :size="13" /> 复制
        </button>
        <button class="btn btn-sm" :disabled="!logText" @click="exportLog">
          <Icon name="download" :size="13" /> 导出
        </button>
      </div>
    </div>
  </div>
</template>
