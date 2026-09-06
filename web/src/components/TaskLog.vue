<script setup>
/**
 * 任务日志查看器：元数据 + 全量拉取（带进度）+ 导出/存实验。
 * 排队中的任务无日志：只显示基本信息（位置/优先级/预计等待）。
 */
import { computed, ref, watch } from 'vue'
import Icon from './Icon.vue'
import { api } from '../api'
import { toast } from '../store'
import { handleCR, statusLabel, formatTime, downloadText, etaText } from '../utils'

const props = defineProps({
  task: { type: Object, default: null },  // 任务元数据
  nodeId: { type: String, default: '' },
})
const emit = defineEmits(['close', 'save-exp'])

const state = ref('idle')  // idle | loading | done | error
const logText = ref('')
const errorMsg = ref('')
const progress = ref({ loaded: 0, total: 0 })
const logBody = ref(null)
let reloadSeq = 0

// 日志加载完成后滚动到末尾（最新输出）
function scrollLogToEnd() {
  requestAnimationFrame(() => {
    if (logBody.value) logBody.value.scrollTop = logBody.value.scrollHeight
  })
}

async function load() {
  if (!props.task || !props.nodeId) {
    state.value = 'idle'
    return
  }
  // 排队任务还没产生日志；终端沙盒没有命令日志 → 不发起请求，面板只展示基本信息
  if (props.task.status === 'queued' || props.task.sandbox) {
    state.value = 'done'
    logText.value = ''
    errorMsg.value = ''
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
    scrollLogToEnd()
  } catch (e) {
    if (seq !== reloadSeq) return
    errorMsg.value = e.message
    state.value = 'error'
  }
}

// 状态变化（排队→运行/完成）时重新加载：排队不拉日志，开始后自动拉取
watch(() => [props.task?.task_id, props.task?.status, props.nodeId], () => { load() }, { immediate: true })

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
const isQueued = computed(() => !!props.task && props.task.status === 'queued')
const isSandbox = computed(() => !!props.task && !!props.task.sandbox)

function exportLog() {
  if (!logText.value) return
  const ts = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  downloadText(`${props.task.task_id}_${ts}.log`, logText.value)
  toast('日志已导出', 'success')
}

// 全宽独立日志页（新标签页打开，可自由缩放/搜索，不影响任务页）
function openInNewPage() {
  if (!props.task || !props.nodeId) return
  window.open(
    `/tasks/${encodeURIComponent(props.nodeId)}/${encodeURIComponent(props.task.task_id)}/log`,
    '_blank', 'noopener'
  )
}

defineExpose({ reload: load })
</script>

<template>
  <div class="log-panel card">
    <div class="card-head">
      <Icon name="terminal" :size="14" />
      <span class="card-title">{{ isSandbox ? '沙盒信息' : isQueued ? '任务信息' : '任务日志' }}</span>
      <span v-if="task" class="card-sub mono">{{ task.task_id }}</span>
      <span class="grow" />
      <button v-if="task && !isQueued && !isSandbox" class="btn btn-ghost btn-icon" title="新页面打开（全宽查看）" @click="openInNewPage">
        <Icon name="external" :size="14" />
      </button>
      <button v-if="task" class="btn btn-ghost btn-icon" title="重新加载" @click="load">
        <Icon name="refresh" :size="14" />
      </button>
      <button class="btn btn-ghost btn-icon" title="关闭" @click="emit('close')">
        <Icon name="close" :size="14" />
      </button>
    </div>

    <div v-if="!task" class="empty-state">
      <div class="icon">📋</div>
      <p>点击队列中的任务查看信息 / 日志</p>
      <p class="small">普通用户仅可查看自己任务的信息与日志</p>
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
        <template v-if="isQueued">
          <span class="k">队列位置</span><span class="v">#{{ task.position ?? '?' }}</span>
          <span class="k">优先级</span><span class="v">{{ task.priority ? '赶论文' : '普通' }}</span>
          <span class="k">预计等待</span><span class="v">{{ etaText(task.eta) || '—' }}</span>
        </template>
        <span class="k">状态</span>
        <span class="v">
          {{ statusLabel(task.status) }}
          <template v-if="isSandbox && task.pids && task.pids.length">
            · PID {{ task.pids.join(', ') }}
          </template>
          <template v-else-if="task.result">
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

      <div v-else-if="isQueued" class="empty-state">
        <div class="icon">⏳</div>
        <p>任务在排队中</p>
        <p class="small">开始后此处显示日志，可点「重新加载」刷新</p>
      </div>

      <div v-else-if="isSandbox" class="empty-state">
        <div class="icon">⬡</div>
        <p>终端沙盒（neu-sbox）</p>
        <p class="small">非命令任务，无日志；终端退出或队列中删除时释放资源</p>
      </div>

      <div v-else ref="logBody" class="log-body">{{ logText || '(无输出)' }}</div>

      <div v-if="state === 'done' && !isQueued && !isSandbox" class="log-toolbar">
        <span class="text-3 small grow">{{ logText.length.toLocaleString() }} 字符</span>
        <button v-if="task" class="btn btn-sm" title="在全宽新页面中查看日志"
                @click="openInNewPage()">
          <Icon name="external" :size="13" /> 新页面
        </button>
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
