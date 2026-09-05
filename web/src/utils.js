/** 间隔轮询：标签页隐藏时自动暂停，重新可见时立即拉一次。
 *  返回清理函数（替代裸 setInterval，多标签页时减少无效请求堆积）。 */
export function setPolling(fn, ms) {
  const tick = () => { if (!document.hidden) fn() }
  const id = setInterval(tick, ms)
  const onVisible = () => { if (!document.hidden) fn() }
  document.addEventListener('visibilitychange', onVisible)
  return () => {
    clearInterval(id)
    document.removeEventListener('visibilitychange', onVisible)
  }
}

/** 通用格式化工具（从旧版前端移植） */

export function formatBytes(bytes) {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB', 'TB']
  let i = 0, val = bytes
  while (val >= 1024 && i < units.length - 1) { val /= 1024; i++ }
  return val >= 100 ? `${Math.round(val)} ${units[i]}` : `${val.toFixed(1)} ${units[i]}`
}

/** Worker 的 idle_cpu 上报百分比(0-100)；≤100 视为百分比 */
export function isIdlePercent(cpuIdle) {
  return typeof cpuIdle === 'number' && cpuIdle <= 100
}

export function formatCpu(cpuIdle, cpuTotal) {
  if (!cpuTotal) return '? / ? 核'
  if (isIdlePercent(cpuIdle)) {
    const used = ((100 - cpuIdle) / 100) * cpuTotal
    return `${used.toFixed(1)} / ${cpuTotal} 核`
  }
  return `${cpuTotal - cpuIdle} / ${cpuTotal} 核`
}

export function cpuUsedPercent(cpuIdle, cpuTotal) {
  if (!cpuTotal) return 0
  if (isIdlePercent(cpuIdle)) return 100 - cpuIdle
  return ((cpuTotal - cpuIdle) / cpuTotal) * 100
}

export function memUsedPercent(idle, total) {
  if (!total) return 0
  return ((total - idle) / total) * 100
}

export function formatTime(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ` +
    `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

export function formatTimeShort(ts) {
  if (!ts) return '—'
  const d = new Date(ts * 1000)
  const pad = n => String(n).padStart(2, '0')
  return `${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

/** 终端 \r 处理：每行只保留最后一个 \r 之后的内容 */
export function handleCR(text) {
  if (!text || text.indexOf('\r') < 0) return text
  return text.split('\n').map(line => {
    const idx = line.lastIndexOf('\r')
    return idx >= 0 ? line.substring(idx + 1) : line
  }).join('\n')
}

export function statusLabel(s) {
  const map = { queued: '排队中', running: '执行中', completed: '已完成', failed: '失败' }
  return map[s] || s
}

/** 多行命令（支持 \ 续行）→ && 拼接后的单条命令 */
export function joinCommandLines(raw) {
  const lines = String(raw || '').trim()
    .split('\n').map(s => s.trim()).filter(s => s !== '')
  const commands = []
  let buf = ''
  for (const line of lines) {
    if (line.endsWith('\\')) {
      buf += (buf ? ' ' : '') + line.slice(0, -1).trim()
    } else if (buf) {
      buf += ' ' + line
      commands.push(buf)
      buf = ''
    } else {
      commands.push(line)
    }
  }
  if (buf) commands.push(buf)
  return commands.join(' && ')
}

/** "4G" / "512M" → GB 数值 */
export function memToGB(raw) {
  const m = String(raw || '0').match(/^(\d+(?:\.\d+)?)([MGmg])?$/)
  if (!m) return 0
  const n = parseFloat(m[1])
  if ((m[2] || '').toLowerCase() === 'm') return Math.round(n / 1024 * 10) / 10
  return n
}

export function etaText(eta) {
  if (eta == null) return ''
  if (eta >= 60) {
    const h = Math.floor(eta / 60)
    const m = eta % 60
    return `⏳ ~${h}h${m > 0 ? m + 'm' : ''}`
  }
  return eta > 0 ? `⏳ ~${eta}min` : '⏳ 即将执行'
}

export function downloadText(filename, text, mime = 'text/plain') {
  const blob = new Blob([text], { type: `${mime};charset=utf-8` })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}

export function safeFilename(name) {
  return (name || 'file').replace(/[\\/:*?"<>|]/g, '-').substring(0, 60)
}
