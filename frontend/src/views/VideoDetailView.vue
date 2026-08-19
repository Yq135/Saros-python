<template>
  <div class="video-detail-page">
    <el-page-header @back="$router.back()" :content="detail?.title || '视频详情'" class="page-header" />

    <!-- 非成功状态：错误/进行中 -->
    <el-card v-if="detail && detail.status !== 'SUCCESS'" shadow="never">
      <el-result
        v-if="detail.status === 'FAILED'"
        icon="error"
        title="任务失败"
        :sub-title="detail.error_msg || '未知错误'"
      >
        <template #extra>
          <el-button type="primary" @click="retry">重试</el-button>
        </template>
      </el-result>
      <el-result v-else icon="info" title="任务进行中" :sub-title="detail.step_desc || '排队中'">
        <template #extra>
          <el-progress :percentage="detail.progress" style="width: 320px" />
        </template>
      </el-result>
    </el-card>

    <template v-else-if="detail">
      <!-- 播放器：内嵌 B 站官方在线播放器，时间点跳转带 ?t= 参数重载 -->
      <el-card shadow="never" class="player-card">
        <iframe
          v-if="detail"
          :key="iframeKey"
          :src="playerSrc"
          class="player"
          frameborder="0"
          allowfullscreen
          scrolling="no"
        />
        <div v-if="detail.mode === 'AUDIO'" class="audio-note">
          该视频无字幕（音频模式），大纲/字幕段基于语音转写（ASR）生成
        </div>
      </el-card>

      <!-- 元信息 -->
      <div v-if="detail.mode || detail.suggested_tags.length" class="meta-line">
        <el-tag v-if="detail.mode" type="info" size="small">{{ modeText }}</el-tag>
        <el-tag v-for="t in detail.suggested_tags" :key="t" size="small">{{ t }}</el-tag>
      </div>

      <!-- 大纲 -->
      <el-card shadow="never">
        <template #header>
          <span>学习大纲（{{ detail.outline.length }} 小节，点击时间可跳转）</span>
        </template>
        <el-empty v-if="detail.outline.length === 0" description="暂无大纲" :image-size="60" />
        <div v-for="(o, i) in detail.outline" :key="i" class="outline-item">
          <el-button link type="primary" class="time-btn" @click="jump(o.time_sec)">
            {{ fmtTs(o.time_sec) }}
          </el-button>
          <div class="outline-body">
            <div class="outline-title">{{ i + 1 }}. {{ o.title }}</div>
            <div class="outline-summary">{{ o.summary }}</div>
          </div>
        </div>
      </el-card>

      <!-- 题目 -->
      <el-card shadow="never">
        <template #header>
          <span>看完应掌握（{{ detail.questions.length }} 题）</span>
        </template>
        <el-empty v-if="detail.questions.length === 0" description="暂无题目" :image-size="60" />
        <el-collapse v-else>
          <el-collapse-item v-for="(q, i) in detail.questions" :key="q.id" :name="String(i)">
            <template #title>
              <span class="q-title">
                <el-button
                  v-if="q.ts"
                  link
                  type="primary"
                  class="time-btn"
                  @click.stop="jump(q.ts)"
                >
                  {{ fmtTs(q.ts) }}
                </el-button>
                {{ i + 1 }}. {{ q.question }}
              </span>
            </template>
            <div class="q-answer">{{ q.reference_answer }}</div>
          </el-collapse-item>
        </el-collapse>
      </el-card>

      <!-- 字幕段（默认折叠，用户一般不关注；音频模式无数据） -->
      <el-card v-if="detail.segments.length" shadow="never">
        <el-collapse>
          <el-collapse-item>
            <template #title>
              <span class="seg-head">
                字幕段（{{ detail.segments.length }} 条，点击展开，点击时间可跳转）
              </span>
            </template>
            <div v-for="s in detail.segments" :key="s.id" class="seg-item">
              <el-button link type="primary" class="time-btn" @click="jump(s.start_ts)">
                {{ fmtTs(s.start_ts) }}
              </el-button>
              <span class="seg-text">{{ s.content }}</span>
            </div>
          </el-collapse-item>
        </el-collapse>
      </el-card>
    </template>
  </div>
</template>

<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { ElMessage } from 'element-plus'
import { bilibiliApi } from '../api'

const route = useRoute()
const detail = ref(null)
const seekT = ref(0) // 播放器起始秒数（?t= 参数）
const iframeKey = ref(0) // 变更即重载 iframe（跳转到新时间点）
let timer = null

const modeText = computed(
  () => ({ CC: 'CC 字幕', AI: 'AI 字幕', AUDIO: '音频模式' }[detail.value?.mode] || '')
)

// B 站官方在线播放器地址：bvid + 分集 + 起始秒数（关闭弹幕）
const playerSrc = computed(() => {
  const d = detail.value
  if (!d) return ''
  return `https://player.bilibili.com/player.html?bvid=${d.bvid}&page=${d.p || 1}&t=${seekT.value}&autoplay=0&danmaku=0`
})

function fmtTs(sec) {
  const total = Math.floor(sec ?? 0)
  return `${String(Math.floor(total / 60)).padStart(2, '0')}:${String(total % 60).padStart(2, '0')}`
}

function jump(sec) {
  seekT.value = Math.floor(sec ?? 0)
  iframeKey.value += 1
}

async function load() {
  try {
    detail.value = await bilibiliApi.get(route.params.id)
    startPollingIfActive()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function startPollingIfActive() {
  clearInterval(timer)
  if (detail.value && !['SUCCESS', 'FAILED'].includes(detail.value.status)) {
    timer = setInterval(load, 2500) // 任务进行中，轮询刷新
  }
}

async function retry() {
  try {
    await bilibiliApi.retry(route.params.id)
    ElMessage.success('已重新提交，排队执行中')
    await load()
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(load)
onUnmounted(() => clearInterval(timer))
</script>

<style scoped>
.video-detail-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-header {
  margin-bottom: 4px;
}
.player {
  width: 100%;
  height: 480px;
  background: #000;
  border-radius: 6px;
}
.audio-note {
  margin-top: 8px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.meta-line {
  display: flex;
  align-items: center;
  gap: 8px;
}
.outline-item {
  display: flex;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.outline-item:last-child {
  border-bottom: none;
}
.time-btn {
  flex-shrink: 0;
  font-family: monospace;
}
.outline-title {
  font-weight: 500;
}
.outline-summary {
  margin-top: 2px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.q-title {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.q-answer {
  padding: 4px 12px;
  font-size: 13px;
  color: var(--el-text-color-regular);
}
.seg-head {
  font-size: 14px;
  font-weight: 500;
  color: var(--el-text-color-secondary);
}
.seg-item {
  display: flex;
  gap: 8px;
  padding: 4px 0;
  font-size: 13px;
  border-bottom: 1px dashed var(--el-border-color-lighter);
}
.seg-item:last-child {
  border-bottom: none;
}
.seg-text {
  line-height: 1.6;
}
</style>
