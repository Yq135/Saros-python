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

    <!-- 列表区 -->
    <el-card shadow="never">
      <template #header>
        <div class="list-header">
          <span>已沉淀 {{ items.length }} 条</span>
          <div class="filters">
            <el-input
              v-model="filters.q"
              placeholder="关键词搜索"
              clearable
              style="width: 200px"
              @keyup.enter="load"
              @clear="load"
            />
            <el-select
              v-model="filters.tag"
              placeholder="标签筛选"
              clearable
              style="width: 160px"
              @change="load"
            >
              <el-option v-for="t in allTags" :key="t" :label="t" :value="t" />
            </el-select>
          </div>
        </div>
      </template>

      <el-empty v-if="items.length === 0" description="暂无沉淀，先手打一条吧" />
      <div v-for="item in items" :key="item.id" class="knowledge-item">
        <div class="item-head">
          <div class="item-tags">
            <el-tag
              v-for="t in item.tags"
              :key="t"
              size="small"
              class="tag-click"
              @click="filters.tag = t; load()"
            >{{ t }}</el-tag>
          </div>
          <div class="item-actions">
            <el-rate :model-value="item.mastery_level" :max="5" disabled size="small" />
            <el-button link type="primary" @click="openEdit(item)">编辑</el-button>
            <el-button link type="danger" @click="remove(item)">删除</el-button>
          </div>
        </div>
        <div class="item-content">{{ item.content }}</div>
        <div class="item-time">更新于 {{ formatTime(item.updated_at) }}</div>
      </div>
    </el-card>

    <!-- 编辑对话框 -->
    <el-dialog v-model="editVisible" title="编辑知识点（同样禁粘贴）" width="560px">
      <el-form label-position="top">
        <el-form-item label="知识点内容">
          <PasteGuardTextarea v-model="editForm.content" :rows="6" />
        </el-form-item>
        <el-form-item label="掌握程度">
          <el-rate v-model="editForm.mastery_level" :max="5" />
        </el-form-item>
        <el-form-item label="标签">
          <el-select
            v-model="editForm.tags"
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
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">取消</el-button>
        <el-button type="primary" :loading="saving" @click="saveEdit">保存</el-button>
      </template>
    </el-dialog>
  </div>
</template>

<script setup>
import { onMounted, reactive, ref } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import PasteGuardTextarea from '../components/PasteGuardTextarea.vue'
import { knowledgeApi } from '../api'

const items = ref([])
const allTags = ref([]) // 标签筛选下拉
const tagSuggestions = ref([]) // 标签输入自动补全
const tagLoading = ref(false)
const saving = ref(false)

const filters = reactive({ q: '', tag: '' })
const form = reactive({ content: '', mastery_level: 0, tags: [] })
const editVisible = ref(false)
const editForm = reactive({ id: null, content: '', mastery_level: 0, tags: [] })

async function load() {
  items.value = await knowledgeApi.list(filters)
}

async function loadAllTags() {
  allTags.value = await knowledgeApi.suggestTags('')
}

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
    await Promise.all([load(), loadAllTags()])
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

function openEdit(item) {
  editForm.id = item.id
  editForm.content = item.content
  editForm.mastery_level = item.mastery_level
  editForm.tags = [...item.tags]
  editVisible.value = true
}

async function saveEdit() {
  if (!editForm.content.trim()) {
    ElMessage.warning('内容不能为空')
    return
  }
  saving.value = true
  try {
    await knowledgeApi.update(editForm.id, {
      content: editForm.content,
      mastery_level: editForm.mastery_level,
      tags: editForm.tags,
    })
    ElMessage.success('已更新')
    editVisible.value = false
    await Promise.all([load(), loadAllTags()])
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function remove(item) {
  try {
    await ElMessageBox.confirm('确定删除这条沉淀？删除后不可恢复。', '删除确认', {
      type: 'warning',
      confirmButtonText: '删除',
      cancelButtonText: '取消',
    })
  } catch {
    return // 用户取消
  }
  try {
    await knowledgeApi.remove(item.id)
    ElMessage.success('已删除')
    await Promise.all([load(), loadAllTags()])
  } catch (e) {
    ElMessage.error(e.message)
  }
}

function formatTime(iso) {
  return new Date(iso).toLocaleString('zh-CN', { hour12: false })
}

onMounted(() => {
  load()
  loadAllTags().then(() => {
    tagSuggestions.value = allTags.value
  })
})
</script>

<style scoped>
.knowledge-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.entry-card .header-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.list-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}
.filters {
  display: flex;
  gap: 8px;
}
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
