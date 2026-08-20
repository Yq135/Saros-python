<template>
  <div class="knowledge-page">
    <!-- 录入区 -->
    <el-card shadow="never" class="entry-card">
      <template #header>
        <span>手打沉淀</span>
        <span class="header-tip">为加深记忆，录入框禁止粘贴</span>
      </template>
      <el-form label-position="top">
        <el-form-item label="知识点内容">
          <PasteGuardTextarea
            v-model="form.content"
            :rows="6"
            placeholder="为加深记忆，请逐字手打输入…"
          />
        </el-form-item>
        <el-form-item label="掌握程度">
          <el-rate v-model="form.mastery_level" :max="5" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="form.tags"
            multiple
            filterable
            allow-create
            default-first-option
            :remote-method="loadTagSuggestions"
            :loading="tagLoading"
            placeholder="选择已有标签，或输入新标签后回车创建"
            style="width: 100%"
          >
            <el-option v-for="t in tagSuggestions" :key="t" :label="t" :value="t" />
          </el-select>
        </el-form-item>
        <el-button type="primary" :loading="saving" @click="save">保存沉淀</el-button>
      </el-form>
    </el-card>

    <!-- 勉励话（占位文案，待定） -->
    <div class="encourage-line">
      「学而时习之，不亦说乎。」可随时前往
      <router-link to="/knowledge-query">知识查询</router-link>
      复习与检索已沉淀的知识。
    </div>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage } from 'element-plus'
import PasteGuardTextarea from '../components/PasteGuardTextarea.vue'
import { knowledgeApi } from '../api'

const tagSuggestions = ref([]) // 标签输入自动补全
const tagLoading = ref(false)
const saving = ref(false)

const form = reactive({ content: '', mastery_level: 0, tags: [] })

async function loadTagSuggestions(q) {
  tagLoading.value = true
  try {
    tagSuggestions.value = await knowledgeApi.suggestTags(q || '')
  } finally {
    tagLoading.value = false
  }
}

async function save() {
  if (!form.content.trim()) {
    ElMessage.warning('请先手打内容')
    return
  }
  saving.value = true
  try {
    await knowledgeApi.create({
      content: form.content.trim(),
      mastery_level: form.mastery_level,
      tags: form.tags,
    })
    ElMessage.success('沉淀成功')
    form.content = ''
    form.mastery_level = 0
    form.tags = []
    loadTagSuggestions('') // 若新建了标签，刷新补全候选
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

onMounted(() => {
  loadTagSuggestions('')
})
</script>

<style scoped>
.knowledge-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.entry-card {
  max-width: 720px;
}
.entry-card .header-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.encourage-line {
  color: var(--el-text-color-secondary);
  font-size: 14px;
}
</style>
