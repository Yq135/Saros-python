<template>
  <div class="knowledge-query-page">
    <!-- 语义查询（小RAG，纯检索不生成） -->
    <el-card shadow="never">
      <template #header>
        <span>语义查询</span>
        <span class="header-tip">输入自然语言，按语义匹配已沉淀的知识（纯检索，不调用 LLM）</span>
      </template>
      <div class="sem-row">
        <el-input
          v-model="semQuery"
          type="textarea"
          :rows="2"
          placeholder="例如：怎么炖红烧肉？"
          @keyup.enter.prevent="doSemSearch"
        />
        <div class="sem-actions">
          <el-select v-model="semTopK" style="width: 100px">
            <el-option v-for="n in [5, 10, 20]" :key="n" :label="`取前 ${n} 条`" :value="n" />
          </el-select>
          <el-button type="primary" :loading="semLoading" @click="doSemSearch">语义查询</el-button>
        </div>
      </div>
      <div v-if="semSearched" class="sem-results">
        <el-empty v-if="semResults.length === 0" description="没有语义匹配到的知识" />
        <KnowledgeItem
          v-for="item in semResults"
          :key="item.id"
          :item="item"
          show-similarity
          @edit="openEdit"
          @remove="remove"
          @tag-click="filterByTag"
        />
      </div>
    </el-card>

    <!-- 全部知识（筛选 + 分页） -->
    <el-card shadow="never">
      <template #header>
        <div class="list-header">
          <span>全部知识</span>
          <div class="filters">
            <el-input
              v-model="filters.q"
              placeholder="关键词搜索"
              clearable
              style="width: 200px"
              @keyup.enter="applyFilters"
              @clear="applyFilters"
            />
            <el-select
              v-model="filters.tag"
              placeholder="标签筛选"
              clearable
              filterable
              style="width: 160px"
              @change="applyFilters"
            >
              <el-option v-for="t in tagOptions" :key="t" :label="t" :value="t" />
            </el-select>
            <el-select
              v-model="filters.mastery"
              placeholder="掌握度筛选"
              clearable
              style="width: 140px"
              @change="applyFilters"
            >
              <el-option v-for="n in [0, 1, 2, 3, 4, 5]" :key="n" :label="`${n} 星`" :value="n" />
            </el-select>
          </div>
        </div>
      </template>

      <el-empty v-if="items.length === 0" description="暂无匹配的知识" />
      <KnowledgeItem
        v-for="item in items"
        :key="item.id"
        :item="item"
        @edit="openEdit"
        @remove="remove"
        @tag-click="filterByTag"
      />
      <div class="pagination-row">
        <el-pagination
          v-model:current-page="page"
          :page-size="pageSize"
          :total="total"
          :page-sizes="[10, 20, 50]"
          layout="total, sizes, prev, pager, next"
          @current-change="load"
          @size-change="applyFilters"
        />
      </div>
    </el-card>

    <!-- 编辑对话框（同样禁粘贴） -->
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
import KnowledgeItem from '../components/KnowledgeItem.vue'
import { knowledgeApi } from '../api'

// ---- 筛选 + 分页 ----
const filters = reactive({ q: '', tag: '', mastery: null })
const page = ref(1)
const pageSize = ref(20)
const total = ref(0)
const items = ref([])

const tagOptions = ref([]) // 筛选下拉选项
const tagSuggestions = ref([]) // 编辑对话框自动补全
const tagLoading = ref(false)

// ---- 语义查询 ----
const semQuery = ref('')
const semTopK = ref(10)
const semResults = ref([])
const semSearched = ref(false) // 是否执行过语义查询（区分「未查询」与「查询了没结果」）
const semLoading = ref(false)

// ---- 编辑 ----
const editVisible = ref(false)
const saving = ref(false)
const editForm = reactive({ id: null, content: '', mastery_level: 0, tags: [] })

async function load() {
  const data = await knowledgeApi.list({
    ...filters,
    page: page.value,
    page_size: pageSize.value,
  })
  items.value = data.items
  total.value = data.total
}

// 任一筛选条件变化 → 重置回第 1 页
function applyFilters() {
  page.value = 1
  load()
}

async function loadTagOptions() {
  tagOptions.value = await knowledgeApi.suggestTags('')
}

async function loadTagSuggestions(q) {
  tagLoading.value = true
  try {
    tagSuggestions.value = await knowledgeApi.suggestTags(q || '')
  } finally {
    tagLoading.value = false
  }
}

// 点击条目标签 → 设为结构化筛选条件
function filterByTag(t) {
  filters.tag = t
  applyFilters()
}

async function doSemSearch() {
  const q = semQuery.value.trim()
  if (!q) {
    ElMessage.warning('请输入查询内容')
    return
  }
  semLoading.value = true
  try {
    const data = await knowledgeApi.search({ query: q, top_k: semTopK.value })
    semResults.value = data.items
    semSearched.value = true
    if (!data.items.length) ElMessage.info('没有语义匹配到的知识')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    semLoading.value = false
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
    await Promise.all([load(), loadTagOptions(), refreshSemIfActive()])
    loadTagSuggestions('')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

// 语义结果已展示时，改删后同步刷新（查询词非空即视为活跃）
function refreshSemIfActive() {
  return semSearched.value && semQuery.value.trim()
    ? doSemSearch()
    : Promise.resolve()
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
    // 删光末页最后一条 → 回退一页，避免出现空页
    if (items.value.length === 1 && page.value > 1) page.value -= 1
    await Promise.all([load(), loadTagOptions(), refreshSemIfActive()])
  } catch (e) {
    ElMessage.error(e.message)
  }
}

onMounted(() => {
  load()
  loadTagOptions().then(() => {
    tagSuggestions.value = tagOptions.value
  })
})
</script>

<style scoped>
.knowledge-query-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.header-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.sem-row {
  display: flex;
  align-items: flex-start;
  gap: 12px;
}
.sem-row .el-textarea {
  flex: 1;
}
.sem-actions {
  display: flex;
  gap: 8px;
}
.sem-results {
  margin-top: 16px;
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
.pagination-row {
  display: flex;
  justify-content: flex-end;
  margin-top: 16px;
}
</style>
