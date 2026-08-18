<template>
  <div class="markdown-view" v-html="html"></div>
</template>

<script setup>
// Markdown 安全渲染：marked 转 HTML + DOMPurify 防 XSS（FR-5.2）
import { computed } from 'vue'
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const props = defineProps({
  content: { type: String, default: '' },
})

const html = computed(() => DOMPurify.sanitize(marked.parse(props.content || '')))
</script>

<style scoped>
.markdown-view :deep(p) {
  margin: 0.4em 0;
}
.markdown-view :deep(h1),
.markdown-view :deep(h2),
.markdown-view :deep(h3),
.markdown-view :deep(h4) {
  margin: 0.7em 0 0.3em;
  font-size: 1.1em;
}
.markdown-view :deep(ul),
.markdown-view :deep(ol) {
  padding-left: 1.5em;
  margin: 0.4em 0;
}
.markdown-view :deep(li) {
  margin: 0.2em 0;
}
.markdown-view :deep(code) {
  background: var(--el-fill-color-light);
  padding: 1px 5px;
  border-radius: 4px;
  font-family: ui-monospace, monospace;
  font-size: 0.9em;
}
.markdown-view :deep(pre) {
  background: var(--el-fill-color-light);
  padding: 10px 12px;
  border-radius: 6px;
  overflow-x: auto;
  margin: 0.5em 0;
}
.markdown-view :deep(pre code) {
  background: none;
  padding: 0;
}
.markdown-view :deep(a) {
  color: var(--el-color-primary);
}
.markdown-view :deep(blockquote) {
  margin: 0.4em 0;
  padding: 2px 12px;
  border-left: 3px solid var(--el-border-color);
  color: var(--el-text-color-secondary);
}
.markdown-view :deep(table) {
  border-collapse: collapse;
  margin: 0.5em 0;
}
.markdown-view :deep(th),
.markdown-view :deep(td) {
  border: 1px solid var(--el-border-color);
  padding: 4px 10px;
}
</style>
