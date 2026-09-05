<script setup>
/**
 * 任务队列列表。
 * - 行: 位置/状态 · 用户 · 命令 · ETA · 操作（标注/重跑）
 * - 勾选 → 批量删除
 * - 点击任意行 → 右侧显示基本信息（运行/已完成/失败另可看日志）
 * - markedTasks: 本地 ★ 标注（localStorage）
 */
import { computed } from 'vue'
import Icon from './Icon.vue'
import { statusLabel, etaText } from '../utils'

const props = defineProps({
  tasks: { type: Array, default: () => [] },
  myUsername: { type: String, default: '' },
  checked: { type: Array, default: () => [] },
  marked: { type: Array, default: () => [] },
  // 聚合视图时显示节点列
  showNode: { type: Boolean, default: false },
  deleting: { type: Boolean, default: false },
})
const emit = defineEmits(['select-task', 'toggle-check', 'check-all', 'toggle-mark', 'rerun'])

// 所有行均可点击 → 右侧面板显示基本信息（排队任务无日志，只显示信息）
function onRowClick(task) {
  emit('select-task', task)
}

const checkedCount = computed(() => props.checked.length)
</script>

<template>
  <div>
    <div v-if="!tasks.length" class="queue-empty">
      <Icon name="inbox" :size="22" />
      <p class="mt-8">队列为空</p>
    </div>

    <div v-for="task in tasks" :key="(task.node_id || 'n') + '/' + task.task_id"
         class="queue-row clickable"
         :class="{
           selected: checked.includes(task.task_id),
         }"
         @click="onRowClick(task)">
      <input type="checkbox" class="checkbox"
             :checked="checked.includes(task.task_id)"
             @click.stop
             @change="emit('toggle-check', task.task_id)">
      <span class="pos">
        <template v-if="task.status === 'running'">▶</template>
        <template v-else>{{ task.position ?? '?' }}</template>
      </span>
      <span v-if="showNode" class="text-3 small ellipsis" style="width:84px;flex:none"
            :title="task.node_name">{{ task.node_name }}</span>
      <span class="user" :class="{ me: task.user_id === myUsername }"
            :title="task.user_id">{{ task.user_id || '?' }}</span>
      <span class="cmd" :title="task.command">{{ task.command }}</span>
      <span v-if="task.status === 'queued' && task.eta != null" class="eta">
        {{ etaText(task.eta) }}
      </span>
      <span class="badge" :class="task.status"><span class="dot" />{{ statusLabel(task.status) }}</span>
      <span class="actions">
        <button v-if="task.status === 'completed' || task.status === 'failed'"
                class="btn btn-ghost btn-icon"
                :class="{ always: marked.includes(task.task_id) }"
                :style="marked.includes(task.task_id) ? 'color:var(--warning)' : ''"
                :title="marked.includes(task.task_id) ? '取消标注' : '标注此任务'"
                @click.stop="emit('toggle-mark', task.task_id)">
          <Icon name="star" :size="13" />
        </button>
        <button v-if="task.status === 'completed' || task.status === 'failed'"
                class="btn btn-ghost btn-icon" title="重新执行此命令"
                @click.stop="emit('rerun', task)">
          <Icon name="play" :size="13" />
        </button>
      </span>
    </div>
  </div>
</template>
