<script setup>
/**
 * 节点管理（admin）：nodes.json 增删。
 */
import { onMounted, ref } from 'vue'
import Icon from '../components/Icon.vue'
import Modal from '../components/Modal.vue'
import { api } from '../api'
import { toast } from '../store'

const configNodes = ref([])
const liveNodes = ref([])
const loading = ref(false)

const addOpen = ref(false)
const form = ref({ name: '', host: '', port: '5000' })

async function load() {
  loading.value = true
  try {
    const [cfg, live] = await Promise.all([
      api.get('/nodes/config'),
      api.post('/nodes/get_all_nodes', {}).catch(() => ({ nodes: [] })),
    ])
    configNodes.value = cfg.nodes || []
    liveNodes.value = live.nodes || []
  } catch (e) {
    toast(e.message, 'error')
  } finally {
    loading.value = false
  }
}

const liveByName = (name) => liveNodes.value.find(n => n.name === name)

async function addNode() {
  const name = form.value.name.trim()
  const host = form.value.host.trim()
  const port = parseInt(form.value.port, 10)
  if (!name) { toast('请输入节点名称', 'error'); return }
  if (!host) { toast('请输入 host', 'error'); return }
  if (isNaN(port) || port < 1 || port > 65535) { toast('端口必须在 1-65535 之间', 'error'); return }
  try {
    await api.post('/nodes/config/add', { name, host, port })
    toast(`节点 "${name}" 已添加`, 'success')
    addOpen.value = false
    form.value = { name: '', host: '', port: '5000' }
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

async function removeNode(node) {
  if (!window.confirm(`确定要删除节点 "${node.name}"（${node.host}:${node.port}）吗？`)) return
  try {
    await api.post('/nodes/config/remove', { name: node.name })
    toast(`节点 "${node.name}" 已删除`, 'success')
    load()
  } catch (e) {
    toast(e.message, 'error')
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div class="page-head">
      <div>
        <h1 class="page-title">节点管理</h1>
        <div class="page-desc">维护 nodes.json 中的节点池（添加后约 15s 内心跳开始）</div>
      </div>
      <div class="page-actions">
        <button class="btn btn-ghost" @click="load">
          <Icon name="refresh" :size="14" :class="{ spin: loading }" /> 刷新
        </button>
        <button class="btn btn-primary" @click="addOpen = true">
          <Icon name="plus" :size="14" /> 添加节点
        </button>
      </div>
    </div>

    <div class="card">
      <div class="card-head">
        <Icon name="server" :size="14" />
        <span class="card-title">已配置节点</span>
        <span class="card-sub">{{ configNodes.length }}</span>
      </div>
      <div class="card-body flush">
        <div class="table-wrap">
          <table class="table">
            <thead>
              <tr>
                <th>名称</th>
                <th>地址</th>
                <th>状态</th>
                <th>资源</th>
                <th style="text-align:right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-if="!configNodes.length">
                <td colspan="5" class="queue-empty">
                  尚未配置节点。节点池由 nodes.json 定义，Master 会定期同步。
                </td>
              </tr>
              <tr v-for="n in configNodes" :key="n.name">
                <td class="cell-main">{{ n.name }}</td>
                <td class="mono text-2">{{ n.host }}:{{ n.port }}</td>
                <td>
                  <span v-if="liveByName(n.name)"
                        class="badge" :class="liveByName(n.name).status === 'online' ? 'completed' : 'offline'">
                    <span class="dot" />
                    {{ liveByName(n.name).status === 'online' ? '在线' : '离线' }}
                  </span>
                  <span v-else class="badge neutral"><span class="dot" />未探测</span>
                </td>
                <td class="text-3 small">
                  <template v-if="liveByName(n.name)?.status === 'online'">
                    {{ liveByName(n.name).idle_devices }}/{{ liveByName(n.name).total_devices }} 卡空闲
                    · {{ liveByName(n.name).active_sandboxes }} 沙盒
                  </template>
                  <template v-else>—</template>
                </td>
                <td>
                  <div class="row" style="justify-content:flex-end">
                    <button class="btn btn-danger btn-sm" @click="removeNode(n)">
                      <Icon name="trash" :size="13" /> 删除
                    </button>
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <Modal v-model="addOpen" title="添加节点">
      <div class="field">
        <label class="field-label">名称</label>
        <input v-model="form.name" class="input" placeholder="gpu-01"
               autocomplete="off" @keydown.enter="addNode">
      </div>
      <div class="input-row">
        <div class="field grow">
          <label class="field-label">Host</label>
          <input v-model="form.host" class="input mono" placeholder="192.168.1.10"
                 autocomplete="off" @keydown.enter="addNode">
        </div>
        <div class="field" style="width:110px">
          <label class="field-label">端口</label>
          <input v-model="form.port" type="number" class="input mono"
                 @keydown.enter="addNode">
        </div>
      </div>
      <p class="text-3 small">worker 需在该地址监听 HTTP API（默认 5000）。</p>
      <template #footer>
        <button class="btn" @click="addOpen = false">取消</button>
        <button class="btn btn-primary" @click="addNode">添加</button>
      </template>
    </Modal>
  </div>
</template>
