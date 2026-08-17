<template>
  <el-input
    v-model="model"
    type="textarea"
    :rows="rows"
    :placeholder="placeholder"
    @paste.prevent="blockPaste"
    @keydown="onKeydown"
    @drop.prevent="blockPaste"
  />
</template>

<script setup>
// 禁粘贴三层拦截（FR-4.1，自我约束工具，IME 联想等无法拦截，非安全机制）：
// 1. @paste.prevent 主拦截
// 2. keydown 拦截 Ctrl/Cmd+V（IME 时序兜底）
// 3. @drop.prevent 挡拖拽
import { computed } from 'vue'
import { ElMessage } from 'element-plus'

const props = defineProps({
  modelValue: { type: String, default: '' },
  rows: { type: Number, default: 6 },
  placeholder: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue'])

const model = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

function blockPaste() {
  ElMessage.warning('为加深记忆，请手动输入，粘贴已被禁止')
}

function onKeydown(e) {
  if ((e.metaKey || e.ctrlKey) && (e.key === 'v' || e.key === 'V')) {
    e.preventDefault()
    blockPaste()
  }
}
</script>
