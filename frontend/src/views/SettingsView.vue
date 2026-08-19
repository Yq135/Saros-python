<template>
  <div class="settings-page">
    <!-- LLM -->
    <el-card shadow="never">
      <template #header>
        <span>LLM 配置</span>
        <span class="header-tip">问答 / 网页出题 / 视频大纲与出题的主模型（OpenAI 兼容协议）</span>
      </template>
      <el-form label-width="130px" label-position="left">
        <el-form-item label="Base URL">
          <el-input v-model="form.llm_base_url" placeholder="https://api.deepseek.com/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.llm_api_key" type="password" show-password placeholder="sk-…" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.llm_model" placeholder="deepseek-chat" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- ASR -->
    <el-card shadow="never">
      <template #header>
        <span>ASR 配置（音频模式语音转写）</span>
        <span class="header-tip">自建 mlx-qwen3-asr 服务（OpenAI 兼容接口）</span>
      </template>
      <el-form label-width="130px" label-position="left">
        <el-form-item label="Base URL">
          <el-input v-model="form.asr_base_url" placeholder="http://100.100.61.45:9001/v1" />
        </el-form-item>
        <el-form-item label="API Key">
          <el-input v-model="form.asr_api_key" type="password" show-password placeholder="key" />
        </el-form-item>
        <el-form-item label="模型">
          <el-input v-model="form.asr_model" placeholder="mlx-community/Qwen3-ASR-1.7B-bf16" />
        </el-form-item>
      </el-form>
    </el-card>

    <!-- B 站视频 -->
    <el-card shadow="never">
      <template #header>
        <span>B 站视频</span>
      </template>
      <el-form label-width="130px" label-position="left">
        <el-form-item label="跳过字幕下载">
          <el-switch v-model="form.skip_subtitle" />
          <span class="switch-tip">
            开启后所有视频直接走音频模式：ASR 转写带断句标点，大纲/出题质量更高，
            但更耗时（每 10 分钟视频约多 1-2 分钟）
          </span>
        </el-form-item>
        <el-form-item label="cookie 文件">
          <span class="cookie-path">{{ form.cookie_path }}</span>
          <el-button link type="primary" :loading="checkingCookie" @click="checkCookie">
            校验 cookie
          </el-button>
          <el-tag v-if="cookieResult" :type="cookieResult.valid ? 'success' : 'danger'" size="small">
            {{ cookieResult.valid ? `有效（${cookieResult.uname}）` : cookieResult.detail || '无效' }}
          </el-tag>
        </el-form-item>
        <el-form-item label="cookie 内容">
          <div class="cookie-editor">
            <el-input
              v-model="cookieContent"
              type="textarea"
              :rows="6"
              placeholder="Netscape 格式 cookie 原文（浏览器插件导出后粘贴到这里）"
            />
            <el-button class="cookie-save" :loading="savingCookie" @click="saveCookie">
              保存 cookie
            </el-button>
          </div>
        </el-form-item>
      </el-form>
    </el-card>

    <div class="save-row">
      <el-button type="primary" :loading="saving" @click="save">保存配置</el-button>
      <span class="save-tip">保存后即时生效，无需重启</span>
    </div>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { settingsApi } from '../api'

const form = ref({
  llm_base_url: '',
  llm_api_key: '',
  llm_model: '',
  asr_base_url: '',
  asr_api_key: '',
  asr_model: '',
  cookie_path: '',
  skip_subtitle: false,
})
const cookieContent = ref('')
const cookieResult = ref(null)
const saving = ref(false)
const savingCookie = ref(false)
const checkingCookie = ref(false)

async function load() {
  try {
    const s = await settingsApi.get()
    form.value = { ...s }
    const c = await settingsApi.getCookie()
    cookieContent.value = c.content
  } catch (e) {
    ElMessage.error(e.message)
  }
}

async function save() {
  saving.value = true
  try {
    await settingsApi.save(form.value)
    ElMessage.success('已保存，即时生效')
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    saving.value = false
  }
}

async function saveCookie() {
  savingCookie.value = true
  try {
    cookieResult.value = await settingsApi.saveCookie(cookieContent.value)
    ElMessage[cookieResult.value.valid ? 'success' : 'error'](
      cookieResult.value.valid ? 'cookie 已保存且有效' : `cookie 校验失败：${cookieResult.value.detail}`
    )
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    savingCookie.value = false
  }
}

async function checkCookie() {
  checkingCookie.value = true
  try {
    cookieResult.value = await settingsApi.checkCookie()
  } catch (e) {
    ElMessage.error(e.message)
  } finally {
    checkingCookie.value = false
  }
}

onMounted(load)
</script>

<style scoped>
.settings-page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.header-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.switch-tip {
  margin-left: 12px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
  max-width: 520px;
}
.cookie-path {
  font-family: monospace;
  font-size: 13px;
  margin-right: 8px;
}
.cookie-editor {
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.cookie-save {
  align-self: flex-start;
}
.save-row {
  display: flex;
  align-items: center;
  gap: 12px;
}
.save-tip {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
</style>
