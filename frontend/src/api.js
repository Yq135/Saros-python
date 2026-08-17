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
