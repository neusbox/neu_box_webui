<script setup>
/**
 * 节点列表（左栏）：在线/离线、CPU/内存/设备占用。
 * 点击选中节点；设备选择与活跃沙盒在下方展示。
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import {
  formatBytes, formatCpu, formatTime,
  cpuUsedPercent, memUsedPercent,
} from '../utils'

const props = defineProps({
  nodes: { type: Array, default: () => [] },
  selectedId: { type: String, default: null },
  deviceIds: { type: Array, default: () => [] },
  sandboxes: { type: Array, default: () => [] },
  myUsername: { type: String, default: '' },
  isAdmin: { type: Boolean, default: false },
})
const emit = defineEmits(['select', 'refresh', 'manage', 'update:deviceIds', 'release'])

const selected = computed(() =>
  props.nodes.find(n => n.node_id === props.selectedId) || null)

function barClass(pct) {
  return pct > 90 ? 'crit' : pct > 75 ? 'high' : ''
}

function toggleDevice(id) {
  const node = selected.value
  if (!node || node.dev_status?.[id]) return
  const set = new Set(props.deviceIds)
  if (set.has(id)) set.delete(id)
  else set.add(id)
  emit('update:deviceIds', [...set].sort((a, b) => a - b))
}

function sandboxKind(s) {
  const rest = (s.name || '').replace(/^sbx_[^_]+_/, '').replace(/\.slice$/, '')
  return rest.startsWith('task_') ? '任务' : '终端'
}

function sandboxDevs(s) {
  const devs = (s.devices || []).map(d => String(d).split(':')[1])
  return devs.length ? `卡 ${devs.join(', ')}` : '无设备'
}

function canRelease(s) {
  return props.isAdmin || s.owner === props.myUsername
}

function askRelease(s) {
  if (!selected.value) return
  if (!window.confirm(`确定销毁沙盒 ${s.name} 吗？\n将终止其中所有进程并释放 CPU/内存/卡。`)) return
  emit('release', { nodeId: selected.value.node_id, name: s.name })
}
</script>

<template>
  <div>
    <div class="row mb-8">
      <span class="card-title">节点</span>
      <span v-if="nodes.length" class="card-sub">
        {{ nodes.filter(n => n.status === 'online').length }}/{{ nodes.length }} 在线
      </span>
      <span class="grow" />
      <button class="btn btn-ghost btn-icon" title="刷新节点状态" @click="emit('refresh')">
        刷新
      </button>
      <button class="btn btn-ghost btn-icon" title="管理节点" @click="emit('manage')">
        管理
      </button>
    </div>

    <div v-if="!nodes.length" class="queue-empty">暂无节点，请联系管理员配置</div>

    <div v-for="node in nodes" :key="node.node_id"
         class="node-card mb-8"
         :class="{
           selected: node.node_id === selectedId,
           offline: node.status !== 'online',
         }"
         @click="emit('select', node.node_id)">
      <div class="node-head">
        <span class="status-dot" :class="node.status === 'online' ? 'online' : 'offline'" />
        <span class="node-name">{{ node.name }}</span>
        <span class="node-addr">{{ node.ip }}:{{ node.port }}</span>
      </div>

      <template v-if="node.status === 'online'">
        <div class="resource-row">
          <span class="r-label">CPU</span>
          <div class="progress">
            <i :class="barClass(cpuUsedPercent(node.idle_cpu, node.total_cpu))"
               :style="{ width: cpuUsedPercent(node.idle_cpu, node.total_cpu) + '%' }" />
          </div>
          <span class="r-val">{{ formatCpu(node.idle_cpu, node.total_cpu) }}</span>
        </div>
        <div class="resource-row">
          <span class="r-label">内存</span>
          <div class="progress">
            <i :class="barClass(memUsedPercent(node.idle_mem, node.total_mem))"
               :style="{ width: memUsedPercent(node.idle_mem, node.total_mem) + '%' }" />
          </div>
          <span class="r-val">{{ formatBytes(node.total_mem - node.idle_mem) }} / {{ formatBytes(node.total_mem) }}</span>
        </div>
        <div class="resource-row">
          <span class="r-label">设备</span>
          <div class="device-chips grow">
            <span v-for="i in node.idle_devices" :key="'i' + i" class="device-chip" />
            <span v-for="i in (node.total_devices - node.idle_devices)" :key="'b' + i" class="device-chip busy" />
          </div>
          <span class="r-val">{{ node.idle_devices }} / {{ node.total_devices }} 空闲</span>
        </div>
      </template>
      <div v-else class="node-error ellipsis" :title="node.status_error">
        离线：{{ node.status_error || '无法连接' }}
      </div>
    </div>

    <!-- 选中节点：设备选择 -->
    <div v-if="selected && Object.keys(selected.dev_status || {}).length" class="card mt-12">
      <div class="card-head">
        <span class="card-title">指定卡号</span>
        <span class="card-sub">勾选后忽略数量</span>
      </div>
      <div class="card-body">
        <div class="device-picker">
          <label v-for="id in Object.keys(selected.dev_status).map(Number).sort((a, b) => a - b)"
                 :key="id"
                 class="device-check"
                 :class="{
                   free: !selected.dev_status[id],
                   busy: !!selected.dev_status[id],
                   checked: deviceIds.includes(id),
                 }">
            <input type="checkbox"
                   :checked="deviceIds.includes(id)"
                   :disabled="!!selected.dev_status[id]"
                   @change="toggleDevice(id)">
            <span>卡{{ id }}{{ selected.dev_status[id] ? '（占用）' : '' }}</span>
          </label>
        </div>
      </div>
    </div>

    <!-- 选中节点：活跃沙盒 -->
    <div v-if="selected && sandboxes.length" class="card mt-12">
      <div class="card-head">
        <span class="card-title">活跃沙盒</span>
        <span class="card-sub">{{ sandboxes.length }}</span>
      </div>
      <div class="card-body flush">
        <div v-for="s in sandboxes" :key="s.name" class="row" style="padding:7px 14px;font-size:12px">
          <span class="badge neutral" style="font-size:10.5px;padding:0 7px">
            {{ sandboxKind(s) }}
          </span>
          <span class="grow ellipsis text-2">{{ s.owner || '?' }}</span>
          <span class="text-3">{{ sandboxDevs(s) }}</span>
          <span class="text-3" style="font-family:var(--mono);font-size:11px">
            {{ formatTime(s.created_at) }}
          </span>
          <button v-if="canRelease(s)" class="btn btn-ghost btn-icon"
                  style="width:22px;height:22px;flex:none" :title="`销毁沙盒（终止其中进程）`"
                  @click="askRelease(s)">
            <Icon name="trash" :size="12" />
          </button>
        </div>
      </div>
    </div>
  </div>
</template>
