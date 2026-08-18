<template>
  <div class="webpage-page">
    <!-- 提交区 -->
    <el-card shadow="never">
      <template #header>
        <span>网页出题</span>
        <span class="header-tip">粘贴文章链接，生成「读后掌握」问题</span>
      </template>
      <div class="entry-row">
        <el-input
          v-model="url"
          placeholder="https://…"
          clearable
          :disabled="processing"
          @keyup.enter="submit"
        />
        <el-button v-if="!processing" type="primary" @click="submit">生成题目</el-button>
        <el-button v-else type="danger" @click="stop">停止</el-button>
      </div>
      <div v-if="processing" class="step-line">
        <span class="spinner"></span>
        <span class="step-desc">{{ stepDesc }}</span>
        <span class="step-tip">约 20-40 秒，请稍候</span>
      </div>
    </el-card>

    <!-- 列表区 -->
    <el-card shadow="never">
      <template #header>
        <div class="list-header">
          <span>已收录 {{ items.length }} 篇</span>
          <el-input
            v-model="filters.q"
            placeholder="关键词搜索（标题/URL）"
            clearable
            style="width: 260px"
            @keyup.enter="load"
            @clear="load"
          />
        </div>
      </template>

      <el-empty v-if="items.length === 0" description="暂无收录，粘贴一个文章链接开始吧" />
      <div v-for="item in items" :key="item.id" class="article-item">
        <div class="item-head">
          <div class="item-title" @click="openDetail(item.id)">
            {{ item.title || item.url }}
          </div>
          <div class="item-actions">
            <el-button link type="primary" :disabled="processing" @click="regenerate(item.id)">
              重新生成
            </el-button>
            <el-button link type="danger" :disabled="processing" @click="remove(item)">删除</el-button>
          </div>
        </div>
        <div class="item-meta">
          <el-tag v-for="t in item.suggested_tags" :key="t" size="small" class="item-tag">
            {{ t }}
          </el-tag>
          <span class="item-count">{{ item.question_count }} 题</span>
          <span class="item-time">{{ formatTime(item.created_at) }}</span>
        </div>
        <div v-if="item.content_preview" class="item-preview">{{ item.content_preview }}</div>
      </div>
    </el-card>

    <!-- 详情抽屉 -->
    <el-drawer v-model="drawerVisible" size="560px" :title="detail?.title || '文章详情'">
      <div v-if="detail" class="detail-body">
        <div class="detail-meta">
          <a :href="detail.url" target="_blank" rel="noopener">{{ detail.url }}</a>
        </div>
        <div class="detail-tags">
          <el-tag v-for="t in detail.suggested_tags" :key="t" size="small">{{ t }}</el-tag>
          <span class="item-time">收录于 {{ formatTime(detail.created_at) }}</span>
        </div>

        <el-collapse v-model="openCollapse" class="detail-content-collapse">
          <el-collapse-item name="content">
            <template #title>
              <span class="collapse-title">正文（{{ contentLength }} 字，点击展开）</span>
            </template>
            <div class="detail-content">{{ detail.content }}</div>
          </el-collapse-item>
        </el-collapse>

        <div class="questions-head">
          <span>读后掌握（{{ detail.questions.length }} 题）</span>
          <el-button link type="primary" :disabled="processing" @click="regenerate(detail.id)">
            重新生成题目
          </el-button>
        </div>
        <el-empty
          v-if="detail.questions.length === 0"
          description="暂无题目，点击右上角重新生成"
          :image-size="60"
        />
        <el-collapse v-else class="questions">
          <el-collapse-item v-for="(q, i) in detail.questions" :key="q.id" :name="String(i)">
            <template #title>
              <span class="q-title">{{ i + 1 }}. {{ q.question }}</span>
            </template>
            <div class="q-answer">{{ q.reference_answer }}</div>
          </el-collapse-item>
        </el-collapse>
      </div>
    </el-drawer>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import { webpageApi } from '../api'

const url = ref('')
const items = ref([])
const filters = ref({ q: '' })
const processing = ref(false)
const stepDesc = ref('')
const currentController = ref(null) // 当前流的 AbortController（停止/离开页面时中止）

const drawerVisible = ref(false)
const detail = ref(null)
const openCollapse = ref([]) // 详情抽屉中默认折叠正文

const contentLength = computed(() => detail.value?.content?.length || 0)

async function load() {
  items.value = await webpageApi.list(filters.value)
}

async function submit() {
  const u = url.value.trim()
  if (!u || processing.value) return
  if (!/^https?:\/\//i.test(u)) {
    ElMessage.warning('请输入以 http:// 或 https:// 开头的链接')
    return
  }
  processing.value = true
  stepDesc.value = '正在准备…'
  const controller = new AbortController()
  currentController.value = controller
  try {
    await webpageApi.create(
      { url: u },
      (event, data) => {
        if (event === 'step') {
          stepDesc.value = data.desc || '处理中…'
        } else if (event === 'done') {
          if (data.questions_failed) {
            ElMessage.warning('正文已保存，但题目生成失败，可稍后「重新生成题目」')
          } else {
            ElMessage.success(`已生成 ${data.question_count} 道题`)
          }
          load().catch(() => {})
          openDetail(data.id)
        } else if (event === 'error') {
          ElMessage.error(data.detail || '出错了，请重试')
        }
      },
      controller.signal
    )
    url.value = ''
  } catch (e) {
    if (e?.name === 'AbortError') {
      ElMessage.info('已停止')
    } else if (e?.body?.existing_id) {
      ElMessage.info('该网页已收录过，为你打开已有条目')
      openDetail(e.body.existing_id)
      load().catch(() => {})
    } else {
      ElMessage.error(e.message)
    }
  } finally {
    processing.value = false
    currentController.value = null
    stepDesc.value = ''
  }
}

// 重新生成题目（列表项或详情抽屉内调用，逻辑一致）
async function regenerate(id) {
  if (processing.value) return
  processing.value = true
  stepDesc.value = '正在生成题目…'
  const controller = new AbortController()
  currentController.value = controller
  try {
    await webpageApi.regenerate(
      id,
      (event, data) => {
        if (event === 'step') {
          stepDesc.value = data.desc || '处理中…'
        } else if (event === 'done') {
          if (data.questions_failed) {
            ElMessage.warning('题目生成失败，请重试')
          } else {
            ElMessage.success(`已生成 ${data.question_count} 道题`)
          }
          load().catch(() => {})
          if (drawerVisible.value) openDetail(id)
        } else if (event === 'error') {
          ElMessage.error(data.detail || '出错了，请重试')
        }
      },
      controller.signal
    )
  } catch (e) {
    if (e?.name !== 'AbortError') ElMessage.error(e.message)
  } finally {
    processing.value = false
    currentController.value = null
    stepDesc.value = ''
  }
}

async function openDetail(id) {
  try {
    detail.value = await webpageApi.get(id)
    drawerVisible.value = true
    openCollapse.value = [] // 重新打开时正文保持折叠
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function remove(item) {
  try {
    await ElMessageBox.confirm('确定删除该文章？文章与全部题目将一并删除。', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await webpageApi.remove(item.id)
    ElMessage.success('已删除')
    if (detail.value?.id === item.id) drawerVisible.value = false
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function stop() {
  // 中止请求：后端会关掉生成器，已入库的内容会保留
  currentController.value?.abort()
  ElMessage.info('已停止')
}

function formatTime(iso) {
  if (!iso) return ''
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  load()
})

onUnmounted(() => {
  // 离开页面时中止未完成的流，避免挂起的连接影响后续请求
  currentController.value?.abort()
})
</script>

<style scoped>
.webpage-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.header-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.entry-row {
  display: flex;
  gap: 8px;
}
.step-line {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 12px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.spinner {
  width: 14px;
  height: 14px;
  border: 2px solid var(--el-color-primary-light-7);
  border-top-color: var(--el-color-primary);
  border-radius: 50%;
  animation: spin 0.8s linear infinite;
  flex-shrink: 0;
}
@keyframes spin {
  to {
    transform: rotate(360deg);
  }
}
.step-desc {
  color: var(--el-text-color-primary);
}
.step-tip {
  font-size: 12px;
}
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.article-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.article-item:last-child {
  border-bottom: none;
}
.item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 8px;
}
.item-title {
  font-weight: 600;
  cursor: pointer;
  word-break: break-all;
}
.item-title:hover {
  color: var(--el-color-primary);
}
.item-actions {
  display: flex;
  align-items: center;
  flex-shrink: 0;
}
.item-meta {
  display: flex;
  align-items: center;
  gap: 8px;
  margin: 6px 0;
}
.item-tag {
  flex-shrink: 0;
}
.item-count {
  font-size: 12px;
  color: var(--el-color-primary);
}
.item-time {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.item-preview {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.6;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.detail-body {
  padding-bottom: 24px;
}
.detail-meta a {
  color: var(--el-color-primary);
  font-size: 13px;
  word-break: break-all;
}
.detail-tags {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
  margin: 10px 0;
}
.detail-content-collapse {
  margin-bottom: 16px;
  border: none;
}
.collapse-title {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.detail-content {
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 14px;
  max-height: 50vh;
  overflow-y: auto;
}
.questions-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  font-weight: 600;
  margin-bottom: 4px;
}
.questions {
  border: none;
}
.q-title {
  font-size: 14px;
  font-weight: 500;
}
.q-answer {
  white-space: pre-wrap;
  line-height: 1.7;
  font-size: 13px;
  color: var(--el-text-color-regular);
  background: var(--el-fill-color-light);
  border-radius: 6px;
  padding: 10px 12px;
}
</style>
