<template>
  <div class="knowledge-item">
    <div class="item-head">
      <div class="item-tags">
        <el-tag
          v-if="showSimilarity"
          type="success"
          size="small"
          class="sim-tag"
        >相似度 {{ simPercent }}</el-tag>
        <el-tag
          v-for="t in item.tags"
          :key="t"
          size="small"
          class="tag-click"
          @click="$emit('tag-click', t)"
        >{{ t }}</el-tag>
      </div>
      <div class="item-actions">
        <el-rate :model-value="item.mastery_level" :max="5" disabled size="small" />
        <el-button link type="primary" @click="$emit('edit', item)">编辑</el-button>
        <el-button link type="danger" @click="$emit('remove', item)">删除</el-button>
      </div>
    </div>
    <div class="item-content">{{ item.content }}</div>
    <div class="item-time">更新于 {{ formatTime(item.updated_at) }}</div>
  </div>
</template>

<script setup>
import { computed } from 'vue'

// 知识条目（查询页的筛选列表与语义查询结果共用）
const props = defineProps({
  item: { type: Object, required: true },
  showSimilarity: { type: Boolean, default: false },
})
defineEmits(['edit', 'remove', 'tag-click'])

// 余弦相似度可能为负（不相关内容），展示时 clamp 到 0
const simPercent = computed(() =>
  `${(Math.max(0, props.item.similarity ?? 0) * 100).toFixed(1)}%`
)

function formatTime(iso) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}
</script>

<style scoped>
.knowledge-item {
  padding: 12px 0;
  border-bottom: 1px solid var(--el-border-color-lighter);
}
.knowledge-item:last-child {
  border-bottom: none;
}
.item-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 6px;
}
.item-tags {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
}
.sim-tag {
  cursor: default;
}
.tag-click {
  cursor: pointer;
}
.item-actions {
  display: flex;
  align-items: center;
  gap: 4px;
  flex-shrink: 0;
}
.item-content {
  white-space: pre-wrap;
  line-height: 1.6;
}
.item-time {
  margin-top: 6px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
