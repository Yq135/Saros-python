<template>
  <div class="video-page">
    <!-- 提交区 -->
    <el-card shadow="never">
      <template #header>
        <span>B站视频</span>
        <span class="header-tip">粘贴视频链接，自动归档字幕/音频/视频，生成带时间戳大纲与题目</span>
      </template>
      <div class="entry-row">
        <el-input
          v-model="url"
          placeholder="https://www.bilibili.com/video/BV…（支持 b23.tv 短链与 ?p=N 分集）"
          clearable
          @keyup.enter="submit"
        />
        <el-button type="primary" :loading="submitting" @click="submit">提交任务</el-button>
      </div>
      <div class="queue-tip">任务排队串行执行：下载三件套 → 解析字幕 → LLM 大纲 → 出题，约 1-3 分钟</div>
    </el-card>

    <!-- 任务列表 -->
    <el-card shadow="never">
      <template #header>
        <div class="list-header">
          <span>任务（{{ items.length }}）</span>
          <span v-if="anyActive" class="running-hint">有任务进行中，自动刷新中…</span>
        </div>
      </template>

      <el-empty v-if="items.length === 0" description="暂无任务，粘贴一个 B 站链接开始吧" />
      <div v-for="item in items" :key="item.id" class="task-item">
        <div class="item-head">
          <div class="item-title" :class="{ clickable: item.status === 'SUCCESS' }" @click="openDetail(item)">
            {{ item.title || item.bvid }}
          </div>
          <div class="item-actions">
            <el-button v-if="item.status === 'FAILED'" link type="primary" @click="retry(item)">
              重试
            </el-button>
            <el-button link type="danger" :disabled="item.status === 'PROCESSING'" @click="remove(item)">
              删除
            </el-button>
          </div>
        </div>
        <div class="item-status">
          <el-tag :type="statusType(item.status)" size="small">{{ statusText(item.status) }}</el-tag>
          <el-tag v-if="item.mode" size="small" type="info" class="mode-tag">{{ modeText(item.mode) }}</el-tag>
          <span v-if="item.status === 'PROCESSING'" class="step-desc">{{ item.step_desc || '处理中…' }}</span>
          <span v-else-if="item.status === 'PENDING'" class="step-desc">排队等待中…</span>
          <span class="item-time">{{ formatTime(item.created_at) }}</span>
        </div>
        <el-progress
          v-if="item.status === 'PROCESSING'"
          :percentage="item.progress"
          :stroke-width="8"
          class="task-progress"
        />
        <div v-if="item.status === 'FAILED' && item.error_msg" class="item-error">{{ item.error_msg }}</div>
      </div>
    </el-card>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRouter } from 'vue-router'
import { ElMessage, ElMessageBox } from 'element-plus'
import { bilibiliApi } from '../api'

const router = useRouter()
const url = ref('')
const items = ref([])
const submitting = ref(false)
let timer = null

const anyActive = computed(() =>
  items.value.some((i) => ['PENDING', 'PROCESSING'].includes(i.status))
)

const statusText = (s) =>
  ({ PENDING: '排队中', PROCESSING: '处理中', SUCCESS: '完成', FAILED: '失败' }[s] || s)
const statusType = (s) =>
  ({ PENDING: 'info', PROCESSING: 'warning', SUCCESS: 'success', FAILED: 'danger' }[s] || 'info')
const modeText = (m) => ({ CC: 'CC 字幕', AI: 'AI 字幕', AUDIO: '音频模式' }[m] || m)

async function load() {
  try {
    items.value = await bilibiliApi.list()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function submit() {
  if (!url.value.trim()) return
  submitting.value = true
  try {
    await bilibiliApi.create({ url: url.value.trim() })
    url.value = ''
    ElMessage.success('任务已提交，排队执行中')
    await load()
  } catch (e) {
    if (e.body?.existing_id) {
      ElMessage.warning('该视频已提交过任务')
      router.push(`/videos/${e.body.existing_id}`)
    } else {
      ElMessage.error(e.message)
    }
  } finally {
    submitting.value = false
  }
}

async function retry(item) {
  try {
    await bilibiliApi.retry(item.id)
    ElMessage.success('已重新提交，排队执行中')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function remove(item) {
  try {
    await ElMessageBox.confirm(
      '删除任务将同时清理本地媒体文件与大纲/题目，确定删除？',
      '删除确认',
      { type: 'warning' }
    )
  } catch {
    return
  }
  try {
    await bilibiliApi.remove(item.id)
    ElMessage.success('已删除')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function openDetail(item) {
  if (item.status === 'SUCCESS') router.push(`/videos/${item.id}`)
}

function formatTime(s) {
  if (!s) return ''
  const d = new Date(s)
  const pad = (n) => String(n).padStart(2, '0')
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

onMounted(() => {
  load()
  timer = setInterval(load, 2500) // 本地单用户低频轮询，无压力
})
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.video-page {
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
  gap: 12px;
}
.queue-tip {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.list-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.running-hint {
  font-size: 12px;
  color: var(--el-color-warning);
}
.task-item {
  padding: 12px 4px;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.task-item:last-child {
  border-bottom: none;
}
.item-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.item-title {
  font-weight: 500;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.item-title.clickable {
  cursor: pointer;
  color: var(--el-color-primary);
}
.item-actions {
  flex-shrink: 0;
}
.item-status {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  font-size: 12px;
}
.step-desc {
  color: var(--el-text-color-secondary);
}
.item-time {
  margin-left: auto;
  color: var(--el-text-color-placeholder);
}
.task-progress {
  margin-top: 6px;
}
.item-error {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-color-danger);
}
</style>
