<script setup>
/**
 * 概览：公告、节点总览、我的活跃任务、队列统计。
 */
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { marked } from 'marked'
import { api } from '../api'
import { statusLabel, formatTime } from '../utils'

const nodes = ref([])
const groups = ref([])          // 我的任务（按节点分组）
const offlineNodes = ref([])
const notice = ref('')

async function loadNodes() {
  try {
    const data = await api.post('/nodes/get_all_nodes')
    nodes.value = data.nodes || []
  } catch { /* 保留旧值 */ }
}

async function loadMyTasks() {
  try {
    const data = await api.get('/tasks/mine')
    groups.value = data.groups || []
    offlineNodes.value = data.offline_nodes || []
  } catch { /* 保留旧值 */ }
}

async function loadNotice() {
  try {
    const r = await fetch('/static/notice.txt')
    if (r.ok) {
      const text = await r.text()
      if (text.trim()) notice.value = marked.parse(text)
    }
  } catch { /* 忽略 */ }
}

const stats = computed(() => {
  const all = groups.value.flatMap(g => g.tasks)
  return {
    nodesOnline: nodes.value.filter(n => n.status === 'online').length,
    nodesTotal: nodes.value.length,
    queued: all.filter(t => t.status === 'queued').length,
    running: all.filter(t => t.status === 'running').length,
    done: all.filter(t => t.status === 'completed').length,
    failed: all.filter(t => t.status === 'failed').length,
  }
})

const activeTasks = computed(() =>
  groups.value.flatMap(g => g.tasks
    .filter(t => t.status === 'queued' || t.status === 'running')
    .map(t => ({ ...t, node_name: g.node_name }))))

let timer = null
function loadAll() {
  loadNodes()
  loadMyTasks()
}
onMounted(() => {
  loadNotice()
  loadAll()
  timer = setInterval(loadAll, 15000)
})
onBeforeUnmount(() => { if (timer) clearInterval(timer) })
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">概览</h1>
        <div class="page-desc">集群状态与我的任务</div>
      </div>
      <div class="page-actions">
        <button class="btn" @click="loadAll">刷新</button>
        <router-link class="btn btn-primary" to="/tasks">
          提交任务 →
        </router-link>
      </div>
    </div>

    <!-- 公告 -->
    <div v-if="notice" class="card notice-card">
      <div class="card-head">
        <span class="card-title">📢 通知</span>
      </div>
      <div class="card-body nb-md" v-html="notice"></div>
    </div>

    <!-- 统计 -->
    <div class="stat-grid mt-16">
      <div class="stat">
        <div class="stat-label">节点在线</div>
        <div class="stat-value">
          {{ stats.nodesOnline }}<small> / {{ stats.nodesTotal }}</small>
        </div>
      </div>
      <div class="stat">
        <div class="stat-label">我的排队</div>
        <div class="stat-value" style="color:var(--warning)">{{ stats.queued }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">我的运行中</div>
        <div class="stat-value" style="color:var(--running)">{{ stats.running }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">我的已完成</div>
        <div class="stat-value" style="color:var(--success)">{{ stats.done }}</div>
      </div>
      <div class="stat">
        <div class="stat-label">我的失败</div>
        <div class="stat-value" :style="{ color: stats.failed ? 'var(--danger)' : undefined }">
          {{ stats.failed }}
        </div>
      </div>
    </div>

    <div class="exp-layout mt-16" style="grid-template-columns: 340px 1fr">
      <!-- 节点总览 -->
      <div class="card">
        <div class="card-head">
          <span class="card-title">节点</span>
        </div>
        <div class="card-body flush">
          <div v-if="!nodes.length" class="queue-empty">加载中…</div>
          <div v-for="n in nodes" :key="n.node_id"
               class="row" style="padding:10px 16px;border-bottom:1px solid var(--border);font-size:12.5px">
            <span class="status-dot" :class="n.status === 'online' ? 'online' : 'offline'" />
            <span class="grow ellipsis" style="font-weight:600">{{ n.name }}</span>
            <span v-if="n.status === 'online'" class="text-3">
              {{ n.idle_devices }}/{{ n.total_devices }} 卡空闲
            </span>
            <span v-else class="badge offline" style="font-size:10.5px">
              <span class="dot" />离线
            </span>
          </div>
          <div v-if="offlineNodes.length && !nodes.length" class="queue-empty">
            暂无可用节点
          </div>
        </div>
      </div>

      <!-- 我的活跃任务 -->
      <div class="card">
        <div class="card-head">
          <span class="card-title">我的活跃任务</span>
          <span class="card-sub">{{ activeTasks.length }}</span>
          <span class="grow" />
          <router-link class="btn btn-ghost btn-sm" to="/tasks">全部 →</router-link>
        </div>
        <div class="card-body flush">
          <div v-if="!activeTasks.length" class="empty-state">
            <div class="icon">✨</div>
            <p>暂无排队或运行中的任务</p>
          </div>
          <div v-for="t in activeTasks.slice(0, 10)" :key="t.node_id + t.task_id"
               class="row" style="padding:9px 16px;border-bottom:1px solid var(--border);font-size:12.5px">
            <span class="badge" :class="t.status" style="flex:none">
              <span class="dot" />{{ statusLabel(t.status) }}
            </span>
            <span class="grow ellipsis mono" :title="t.command">{{ t.command }}</span>
            <span class="text-3 small">{{ t.node_name }}</span>
            <span class="text-3 small" style="font-family:var(--mono)">
              {{ formatTime(t.created_at) }}
            </span>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
