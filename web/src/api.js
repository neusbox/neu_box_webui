/**
 * API 客户端 — 统一 JSON 请求 + 401 处理。
 * 会话使用 cookie（httpOnly），fetch 默认携带同站 cookie。
 */

export class ApiError extends Error {
  constructor(status, message, payload) {
    super(message)
    this.status = status
    this.payload = payload
  }
}

async function request(path, options = {}) {
  const {
    method = 'GET', body, headers = {}, raw = false, signal, timeout = 0,
  } = options
  let timeoutId = null
  let timedOut = false
  let forwardAbort = null
  let requestSignal = signal

  if (timeout > 0) {
    const controller = new AbortController()
    requestSignal = controller.signal
    if (signal) {
      forwardAbort = () => controller.abort(signal.reason)
      if (signal.aborted) forwardAbort()
      else signal.addEventListener('abort', forwardAbort, { once: true })
    }
    timeoutId = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, timeout)
  }

  const init = { method, headers: { ...headers }, signal: requestSignal }
  if (body !== undefined) {
    if (!(body instanceof FormData)) {
      // 注意：必须写到 init.headers（init 的独立拷贝），
      // 写到 headers 源对象上不会随 fetch 发出 → Flask 收不到 JSON
      init.headers['Content-Type'] = 'application/json'
      init.body = JSON.stringify(body)
    } else {
      init.body = body
    }
  }

  try {
    const resp = await fetch(path, init)

    if (resp.status === 401) {
      // 登录态失效：刷新页面触发路由守卫重新检查
      if (!location.pathname.startsWith('/login')) {
        window.dispatchEvent(new CustomEvent('neu:unauthorized'))
      }
    }

    if (raw) {
      if (!resp.ok) throw new ApiError(resp.status, `HTTP ${resp.status}`, null)
      return resp
    }

    let payload = null
    try {
      payload = await resp.json()
    } catch (error) {
      // AbortError 必须交给外层转换为超时；其他错误表示响应体为空或非 JSON。
      if (error && error.name === 'AbortError') throw error
    }
    if (!resp.ok) {
      const message = (payload && payload.error) || `HTTP ${resp.status}`
      throw new ApiError(resp.status, message, payload)
    }
    return payload
  } catch (error) {
    if (timedOut && error && error.name === 'AbortError') {
      throw new ApiError(0, '请求超时', null)
    }
    throw error
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId)
    if (signal && forwardAbort) signal.removeEventListener('abort', forwardAbort)
  }
}

async function postStream(path, body, onEvent, options = {}) {
  const { signal, timeout = 0 } = options
  const controller = new AbortController()
  let timedOut = false
  let timeoutId = null
  const requestSignal = controller.signal
  if (signal) {
    if (signal.aborted) controller.abort(signal.reason)
    else signal.addEventListener('abort', () => controller.abort(signal.reason), { once: true })
  }
  if (timeout > 0) {
    timeoutId = setTimeout(() => { timedOut = true; controller.abort() }, timeout)
  }

  try {
    const resp = await fetch(path, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
      signal: requestSignal,
    })
    if (resp.status === 401 && !location.pathname.startsWith('/login')) {
      window.dispatchEvent(new CustomEvent('neu:unauthorized'))
    }
    if (!resp.ok) {
      let message = `HTTP ${resp.status}`
      try {
        const payload = await resp.json()
        if (payload && payload.error) message = payload.error
      } catch { /* 非 JSON */ }
      throw new ApiError(resp.status, message, null)
    }
    if (!resp.body) throw new ApiError(0, '浏览器不支持流式响应', null)

    const reader = resp.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })
      buffer = buffer.replace(/\r\n/g, '\n')
      let boundary = buffer.indexOf('\n\n')
      while (boundary >= 0) {
        const chunk = buffer.slice(0, boundary)
        buffer = buffer.slice(boundary + 2)
        const data = chunk.split('\n')
          .filter(line => line.startsWith('data:'))
          .map(line => line.slice(5).trimStart())
          .join('\n')
        if (data) {
          let event = null
          try { event = JSON.parse(data) } catch { /* 忽略损坏事件 */ }
          if (event) {
            if (event.type === 'error') {
              throw new ApiError(0, event.message || 'AI 请求失败', event)
            }
            onEvent && onEvent(event)
          }
        }
        boundary = buffer.indexOf('\n\n')
      }
    }
  } catch (error) {
    if (timedOut && error && error.name === 'AbortError') {
      throw new ApiError(0, '请求超时', null)
    }
    throw error
  } finally {
    if (timeoutId !== null) clearTimeout(timeoutId)
  }
}

export const api = {
  get: (path, opts) => request(path, { ...opts, method: 'GET' }),
  post: (path, body, opts) => request(path, { ...opts, method: 'POST', body }),
  put: (path, body, opts) => request(path, { ...opts, method: 'PUT', body }),
  delete: (path, body, opts) => request(path, { ...opts, method: 'DELETE', body }),
  stream: postStream,

  /** 以纯文本全量拉取（带进度回调），用于大日志。 */
  getTextWithProgress: (path, onProgress, signal) => new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('GET', path)
    xhr.responseType = 'text'
    let total = 0
    xhr.onprogress = (e) => {
      if (!total && e.total) total = e.total
      onProgress && onProgress({
        loaded: e.loaded,
        total,
        text: xhr.responseText,
      })
    }
    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) resolve(xhr.responseText)
      else {
        let msg = `HTTP ${xhr.status}`
        try {
          const p = JSON.parse(xhr.responseText)
          if (p && p.error) msg = p.error
        } catch { /* 非 JSON */ }
        reject(new ApiError(xhr.status, msg, null))
      }
    }
    xhr.onerror = () => reject(new ApiError(0, '网络错误', null))
    if (signal) signal.addEventListener('abort', () => xhr.abort(), { once: true })
    xhr.send()
  }),
}
