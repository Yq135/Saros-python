// 统一 API 封装（Vite dev 代理 /api → 127.0.0.1:8000）
const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const err = new Error(body?.detail || `请求失败（HTTP ${res.status}）`)
    err.body = body // 部分接口附带额外字段（如 409 的 existing_id）
    throw err
  }
  return res.status === 204 ? null : res.json()
}

export const knowledgeApi = {
  list: (params = {}) => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    if (params.tag) qs.set('tag', params.tag)
    const s = qs.toString()
    return request('/knowledge' + (s ? `?${s}` : ''))
  },
  create: (data) => request('/knowledge', { method: 'POST', body: JSON.stringify(data) }),
  update: (id, data) => request(`/knowledge/${id}`, { method: 'PUT', body: JSON.stringify(data) }),
  remove: (id) => request(`/knowledge/${id}`, { method: 'DELETE' }),
  suggestTags: (q) => request(`/tags?q=${encodeURIComponent(q || '')}`),
}

// ---------- SSE 流式（问答专用，不走 request：request 会消费 body） ----------

// 解析一个 SSE 块（event: ...\ndata: ...）为 { event, data }；无法解析返回 null
function parseSSEBlock(raw) {
  let event = 'message'
  const dataLines = []
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataLines.push(line.slice(5).trim())
  }
  if (!dataLines.length) return null
  try {
    return { event, data: JSON.parse(dataLines.join('\n')) }
  } catch {
    return null
  }
}

// 发起 SSE 请求并校验状态码（非 2xx 抛 Error，detail 取响应体）
async function openStream(path, options) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const err = new Error(body?.detail || `请求失败（HTTP ${res.status}）`)
    err.body = body
    throw err
  }
  return res
}

// 消费 SSE 响应流：按事件逐块回调 onEvent(event, data)
async function consumeStream(res, onEvent) {
  const reader = res.body.getReader()
  const decoder = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += decoder.decode(value, { stream: true })
    let idx
    while ((idx = buf.indexOf('\n\n')) !== -1) {
      const raw = buf.slice(0, idx)
      buf = buf.slice(idx + 2)
      const ev = parseSSEBlock(raw)
      if (ev) onEvent(ev.event, ev.data)
    }
  }
}

async function requestStream(path, options, onEvent) {
  consumeStream(await openStream(path, options), onEvent)
}

export const qaApi = {
  // 提问/追问（SSE 流）：onEvent(event, data)，事件为 start / delta / done / error
  // signal：AbortSignal，中止后 fetch 抛出 AbortError（用于「停止」按钮/离开页面）
  ask: (data, onEvent, signal) =>
    requestStream(
      '/qa/ask',
      {
        method: 'POST',
        body: JSON.stringify({
          question: data.question,
          conversation_id: data.conversationId ?? null,
        }),
        signal,
      },
      onEvent
    ),
  listConversations: (params = {}) => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    const s = qs.toString()
    return request('/qa/conversations' + (s ? `?${s}` : ''))
  },
  getConversation: (id) => request(`/qa/conversations/${id}`),
  removeConversation: (id) => request(`/qa/conversations/${id}`, { method: 'DELETE' }),
}

// ---------- 模块二：网页出题 ----------

export const webpageApi = {
  // 提交 URL（SSE 流）：onEvent(event, data)，事件为 step / done / error
  // URL 已收录：抛 Error 且 err.body.existing_id 为已有文章 id（前端跳转详情）
  create: async (data, onEvent, signal) => {
    const res = await fetch(BASE + '/webpages', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: data.url }),
      signal,
    })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      const err = new Error(body?.detail || `请求失败（HTTP ${res.status}）`)
      err.body = body
      throw err
    }
    await consumeStream(res, onEvent)
  },
  // 重新生成题目（SSE 流）：事件为 step / done / error
  regenerate: (id, onEvent, signal) =>
    requestStream(`/webpages/${id}/regenerate`, { method: 'POST', signal }, onEvent),
  list: (params = {}) => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    const s = qs.toString()
    return request('/webpages' + (s ? `?${s}` : ''))
  },
  get: (id) => request(`/webpages/${id}`),
  remove: (id) => request(`/webpages/${id}`, { method: 'DELETE' }),
}

// ---------- 模块三：B 站视频 ----------

export const bilibiliApi = {
  // 提交任务（后台异步执行，前端轮询列表）；重复提交抛 Error 且 err.body.existing_id 为已有任务 id
  create: (data) => request('/bilibili/tasks', { method: 'POST', body: JSON.stringify({ url: data.url }) }),
  list: () => request('/bilibili/tasks'),
  get: (id) => request(`/bilibili/tasks/${id}`),
  retry: (id) => request(`/bilibili/tasks/${id}/retry`, { method: 'POST' }),
  remove: (id) => request(`/bilibili/tasks/${id}`, { method: 'DELETE' }),
}
