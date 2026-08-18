// 统一 API 封装（Vite dev 代理 /api → 127.0.0.1:8000）
const BASE = '/api'

async function request(path, options = {}) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `请求失败（HTTP ${res.status}）`)
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

// 发送请求并按 SSE 事件流逐块回调 onEvent(event, data)
async function requestStream(path, options, onEvent) {
  const res = await fetch(BASE + path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(body?.detail || `请求失败（HTTP ${res.status}）`)
  }
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
