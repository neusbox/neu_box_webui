/**
 * api.js 冒烟测试（node 直接跑，无依赖）：
 *   node web/test/api.test.mjs
 *
 * 关键回归：POST/PUT/DELETE 的 JSON body 必须随 fetch 发出
 * Content-Type: application/json（曾因写到源对象而非 init.headers
 * 导致 Flask 收不到请求体，登录/注册全部失败）。
 */
import assert from 'node:assert'
import { api } from '../src/api.js'

const calls = []
globalThis.fetch = async (path, init) => {
  calls.push({ path, init })
  return new Response(JSON.stringify({ ok: true }), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

// ── POST JSON ────────────────────────────────────────────────
await api.post('/auth/login', { username: 'u', password: 'p' })
let c = calls.at(-1)
assert.equal(c.init.headers['Content-Type'], 'application/json')
assert.equal(c.init.body, JSON.stringify({ username: 'u', password: 'p' }))

// ── PUT JSON ─────────────────────────────────────────────────
await api.put('/auth/password', { old_password: 'a', new_password: 'b' })
c = calls.at(-1)
assert.equal(c.init.headers['Content-Type'], 'application/json')

// ── DELETE 带 body ───────────────────────────────────────────
await api.delete('/tasks', { node_id: 'n1', task_ids: ['t1'] })
c = calls.at(-1)
assert.equal(c.init.method, 'DELETE')
assert.equal(c.init.headers['Content-Type'], 'application/json')

// ── GET 无 body：不加 Content-Type ───────────────────────────
await api.get('/auth/me')
c = calls.at(-1)
assert.equal(c.init.headers['Content-Type'], undefined)

// ── FormData：不覆盖浏览器自带的 multipart Content-Type ──────
const fd = new FormData()
fd.append('file', new Blob(['x']), 'a.txt')
await api.post('/experiments/upload-image', fd)
c = calls.at(-1)
assert.equal(c.init.body, fd)
assert.equal(c.init.headers['Content-Type'], undefined)

console.log('api.js smoke: all assertions passed')
