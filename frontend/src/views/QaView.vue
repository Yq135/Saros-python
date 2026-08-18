<template>
  <div class="qa-page">
    <!-- 左侧：会话列表 -->
    <el-card shadow="never" class="conv-panel" body-class="conv-panel-body">
      <template #header>
        <div class="conv-header">
          <el-button type="primary" size="small" :disabled="streaming" @click="newConversation">
            新对话
          </el-button>
          <el-input
            v-model="convFilter"
            placeholder="搜索会话"
            clearable
            size="small"
            @keyup.enter="loadConversations"
            @clear="loadConversations"
          />
        </div>
      </template>
      <el-empty
        v-if="conversations.length === 0"
        description="暂无会话，开始提问吧"
        :image-size="60"
      />
      <div
        v-for="c in conversations"
        :key="c.id"
        class="conv-item"
        :class="{ active: c.id === activeId }"
        @click="openConversation(c.id)"
      >
        <div class="conv-row">
          <div class="conv-title">{{ c.title }}</div>
          <el-button link type="danger" size="small" @click.stop="removeConversation(c)">
            删除
          </el-button>
        </div>
        <div class="conv-meta">{{ c.message_count }} 轮 · {{ formatTime(c.last_active) }}</div>
      </div>
    </el-card>

    <!-- 右侧：对话区 -->
    <el-card shadow="never" class="chat-panel">
      <template #header>
        <span class="chat-title">{{ currentTitle || '新对话' }}</span>
        <span v-if="activeId" class="header-tip">可继续追问，上下文会带入本轮</span>
      </template>
      <div ref="chatBody" class="chat-body">
        <el-empty v-if="messages.length === 0" description="输入问题开始对话，可连续追问" />
        <div v-for="(m, i) in messages" :key="i" class="msg">
          <div class="msg-user">{{ m.question }}</div>
          <div class="msg-assistant">
            <div v-if="m.streaming && !m.answer" class="thinking">正在思考…</div>
            <MarkdownView v-if="m.answer" :content="m.answer" />
            <div v-if="m.error" class="msg-error">⚠ {{ m.error }}</div>
            <el-collapse
              v-if="m.done && (m.sources.length || m.knowledge.length || m.suggestedTags.length)"
              class="msg-meta"
            >
              <el-collapse-item v-if="m.sources.length" :title="`来源（${m.sources.length}）`" name="sources">
                <div v-for="(s, si) in m.sources" :key="si" class="source-item">
                  <a :href="s.url" target="_blank" rel="noopener">{{ si + 1 }}. {{ s.title }}</a>
                  <div v-if="s.snippet" class="source-snippet">{{ s.snippet }}</div>
                </div>
              </el-collapse-item>
              <el-collapse-item
                v-if="m.knowledge.length"
                :title="`关联沉淀（${m.knowledge.length}）`"
                name="knowledge"
              >
                <div v-for="k in m.knowledge" :key="k.id" class="knowledge-item">
                  <div class="knowledge-content">{{ k.content }}</div>
                  <el-tag v-for="t in k.tags" :key="t" size="small" class="knowledge-tag">{{ t }}</el-tag>
                </div>
              </el-collapse-item>
              <el-collapse-item v-if="m.suggestedTags.length" title="推荐标签（转手打笔记候选）" name="tags">
                <el-tag v-for="t in m.suggestedTags" :key="t" size="small" class="knowledge-tag">
                  {{ t }}
                </el-tag>
              </el-collapse-item>
            </el-collapse>
          </div>
        </div>
      </div>
      <div class="chat-input">
        <el-input
          v-model="input"
          type="textarea"
          :rows="2"
          :disabled="streaming"
          placeholder="输入问题，Enter 发送（Shift+Enter 换行）"
          @keydown="onInputKeydown"
        />
        <el-button v-if="!streaming" type="primary" @click="send">发送</el-button>
        <el-button v-else type="danger" @click="stop">停止</el-button>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { nextTick, onMounted, onUnmounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import MarkdownView from '../components/MarkdownView.vue'
import { qaApi } from '../api'

const conversations = ref([])
const convFilter = ref('')
const activeId = ref(null)
const currentTitle = ref('')
// 消息：{ id, question, answer, streaming, error, done, sources, knowledge, suggestedTags }
const messages = ref([])
const input = ref('')
const streaming = ref(false)
const chatBody = ref(null)
const currentController = ref(null) // 当前流的 AbortController（停止/离开页面时中止）
const currentMsg = ref(null) // 当前流式中的消息

async function loadConversations() {
  conversations.value = await qaApi.listConversations({ q: convFilter.value })
}

function newConversation() {
  if (streaming.value) return
  activeId.value = null
  currentTitle.value = ''
  messages.value = []
  input.value = ''
}

async function openConversation(id) {
  if (streaming.value || id === activeId.value) return
  try {
    const detail = await qaApi.getConversation(id)
    activeId.value = id
    currentTitle.value = detail.title
    messages.value = detail.messages.map((m) => ({
      id: m.id,
      question: m.question,
      answer: m.answer,
      streaming: false,
      error: '',
      done: true,
      sources: m.search_sources || [],
      knowledge: m.referenced_knowledge || [],
      suggestedTags: m.suggested_tags || [],
    }))
    scrollToBottom()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function removeConversation(c) {
  try {
    await ElMessageBox.confirm('确定删除该会话？会话内全部问答将一并删除。', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await qaApi.removeConversation(c.id)
    ElMessage.success('已删除')
    if (activeId.value === c.id) newConversation()
    await loadConversations()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function onInputKeydown(e) {
  // 中文输入法组合中（isComposing/keyCode 229）：Enter 是确认候选词，不触发发送
  if (e.isComposing || e.keyCode === 229) return
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault()
    send()
  }
}

async function send() {
  const question = input.value.trim()
  if (!question || streaming.value) return
  streaming.value = true
  input.value = ''
  const msg = reactive({
    id: null,
    question,
    answer: '',
    streaming: true,
    error: '',
    done: false,
    sources: [],
    knowledge: [],
    suggestedTags: [],
  })
  currentMsg.value = msg
  messages.value.push(msg)
  scrollToBottom()
  const controller = new AbortController()
  currentController.value = controller
  try {
    await qaApi.ask(
      { question, conversationId: activeId.value },
      (event, data) => {
        if (event === 'start') {
          // 新会话：后端在 start 事件返回 conversation_id
          activeId.value = data.conversation_id
          msg.isNew = data.is_new
          if (data.is_new) currentTitle.value = question.slice(0, 30)
          msg.sources = data.sources || []
          msg.knowledge = data.knowledge || []
          loadConversations().catch(() => {}) // 新会话入列
        } else if (event === 'delta') {
          msg.answer += data.text
          scrollToBottom()
        } else if (event === 'done') {
          msg.id = data.id
          msg.answer = data.answer
          msg.streaming = false
          msg.done = true
          msg.suggestedTags = data.suggested_tags || []
          loadConversations().catch(() => {}) // 刷新轮次数与标题
          scrollToBottom()
        } else if (event === 'error') {
          msg.error = data.detail || '出错了，请重试'
          msg.streaming = false
          msg.done = true
          ElMessage.error(msg.error)
          loadConversations().catch(() => {})
        }
      },
      controller.signal
    )
  } catch (e) {
    msg.streaming = false
    msg.done = true
    if (e?.name === 'AbortError') {
      // 用户点了「停止」或离开页面：不弹错误
      msg.error = '已停止（本轮未保存）'
    } else {
      msg.error = e.message
      ElMessage.error(e.message)
    }
    if (msg.isNew) resetAfterStoppedNew()
    loadConversations().catch(() => {})
  } finally {
    streaming.value = false
    currentController.value = null
    currentMsg.value = null
    scrollToBottom()
  }
}

function stop() {
  // 中止请求：后端会关掉生成器，若新会话无消息则自动清理
  currentController.value?.abort()
  if (currentMsg.value?.isNew) resetAfterStoppedNew()
  ElMessage.info('已停止生成')
}

function resetAfterStoppedNew() {
  // 停止的是「新会话首问」：该会话会被后端清理，回到未选会话状态
  activeId.value = null
  currentTitle.value = ''
}

function scrollToBottom() {
  nextTick(() => {
    if (chatBody.value) chatBody.value.scrollTop = chatBody.value.scrollHeight
  })
}

function formatTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  loadConversations()
})

onUnmounted(() => {
  // 离开页面时中止未完成的流，避免挂起的连接影响后续请求
  currentController.value?.abort()
})
</script>

<style scoped>
.qa-page {
  display: flex;
  gap: 16px;
  align-items: stretch;
}
.conv-panel {
  width: 250px;
  flex-shrink: 0;
}
.conv-panel :deep(.conv-panel-body) {
  max-height: calc(100vh - 200px);
  overflow-y: auto;
  padding: 8px;
}
.chat-panel {
  flex: 1;
  min-width: 0;
}
.chat-title {
  font-weight: 600;
}
.header-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.conv-header {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.conv-item {
  padding: 8px;
  border-radius: 6px;
  cursor: pointer;
  margin-bottom: 4px;
}
.conv-item:hover {
  background: var(--el-fill-color-light);
}
.conv-item.active {
  background: var(--el-color-primary-light-9);
}
.conv-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 4px;
}
.conv-title {
  font-size: 13px;
  line-height: 1.4;
  word-break: break-all;
}
.conv-meta {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.chat-body {
  height: calc(100vh - 320px);
  min-height: 320px;
  overflow-y: auto;
  padding: 4px 4px 12px;
}
.msg {
  margin-bottom: 16px;
}
.msg-user {
  margin-bottom: 6px;
  padding: 8px 12px;
  background: var(--el-color-primary-light-9);
  border-radius: 8px;
  white-space: pre-wrap;
  line-height: 1.6;
  width: fit-content;
  max-width: 90%;
  margin-left: auto;
}
.msg-assistant {
  line-height: 1.7;
}
.thinking {
  color: var(--el-text-color-secondary);
  font-size: 13px;
}
.msg-error {
  margin-top: 6px;
  color: var(--el-color-danger);
  font-size: 13px;
}
.msg-meta {
  margin-top: 8px;
  border: none;
}
.source-item {
  padding: 4px 0;
}
.source-item a {
  color: var(--el-color-primary);
  font-size: 13px;
}
.source-snippet {
  margin-top: 2px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.knowledge-item {
  padding: 6px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.knowledge-item:last-child {
  border-bottom: none;
}
.knowledge-content {
  white-space: pre-wrap;
  font-size: 13px;
  margin-bottom: 4px;
}
.knowledge-tag {
  margin-right: 6px;
  margin-bottom: 4px;
}
.chat-input {
  display: flex;
  gap: 8px;
  align-items: flex-end;
  margin-top: 8px;
}
.chat-input :deep(.el-button) {
  height: auto;
}
</style>
