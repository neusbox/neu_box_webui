<script setup>
/**
 * 任务日志查看器：元数据 + 全量拉取（带进度）+ 导出/存实验。
 */
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { toast } from '../store'
import { handleCR, statusLabel, formatTime, downloadText } from '../utils'

const props = defineProps({
  task: { type: Object, default: null },  // 任务元数据
  nodeId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'save-exp'])

const state = ref('idle')  // idle | loading | done | error
const logText = ref('')
const errorMsg = ref('')
const progress = ref({ loaded: 0, total: 0 })
let reloadSeq = 0

async function load() {
  if (!props.task || !props.nodeId) {
    state.value = 'idle'
    return
  }
  const seq = ++reloadSeq
  state.value = 'loading'
  logText.value = ''
  errorMsg.value = ''
  try {
    const text = await api.getTextWithProgress(
      `/tasks/${props.task.task_id}/log?node_id=${encodeURIComponent(props.nodeId)}&raw=1`,
      (p) => { if (seq === reloadSeq) progress.value = p },
    )
    if (seq !== reloadSeq) return
    logText.value = handleCR(text)
    state.value = 'done'
  } catch (e) {
    if (seq !== reloadSeq) return
    errorMsg.value = e.message
    state.value = 'error'
  }
}

watch(() => [props.task?.task_id, props.nodeId], () => { load() }, { immediate: true })

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

const isFinished = computed(() =>
  !!props.task && (props.task.status === 'completed' || props.task.status === 'failed'))

function exportLog() {
  if (!logText.value) return
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  downloadText(`${props.task.task_id}_${ts}.log`, logText.value)
  toast('日志已导出', 'success')
}

defineExpose({ reload: load })
</script>

<template>
  <div class="log-panel card">
    <div class="card-head">
      <Icon name="terminal" :size="14" />
      <span class="card-title">任务日志</span>
      <span v-if="task" class="card-sub mono">{{ task.task_id }}</span>
      <span class="grow" />
      <button v-if="task" class="btn btn-ghost btn-icon" title="重新加载" @click="load">
        <Icon name="refresh" :size="14" />
      </button>
      <button class="btn btn-ghost btn-icon" title="关闭" @click="emit('close')">
        <Icon name="close" :size="14" />
      </button>
    </div>

    <div v-if="!task" class="empty-state">
      <div class="icon">📋</div>
      <p>点击队列中的任务查看日志</p>
      <p class="small">普通用户仅可查看自己任务的日志</p>
    </div>

    <template v-else>
      <div class="log-meta">
        <span class="k">用户</span><span class="v">{{ task.user_id }}</span>
        <span class="k">命令</span><span class="v">{{ task.command }}</span>
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

      <div v-if="state === 'loading'" class="log-progress">
        <i :style="{ width: progressPct + '%' }"></i>
        <span>{{ progressText }}</span>
      </div>

      <div v-else-if="state === 'error'" class="empty-state">
        <div class="icon text-danger">⚠</div>
        <p class="mt-8 text-danger">{{ errorMsg }}</p>
      </div>

      <div v-else class="log-body">{{ logText || '(无输出)' }}</div>

      <div v-if="state === 'done'" class="log-toolbar">
        <span class="text-3 small grow">{{ logText.length.toLocaleString() }} 字符</span>
        <button v-if="isFinished" class="btn btn-sm" @click="emit('save-exp', task)">
          <Icon name="flask" :size="13" /> 保存为实验记录
        </button>
        <button class="btn btn-sm" @click="exportLog">
          <Icon name="download" :size="13" /> 导出
        </button>
      </div>
    </template>
  </div>
</template>
