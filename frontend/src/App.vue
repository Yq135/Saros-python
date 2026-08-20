<template>
  <el-container class="app-shell">
    <el-header class="app-header">
      <div class="app-title">Saros</div>
      <el-menu
        mode="horizontal"
        :default-active="activePath"
        router
        :ellipsis="false"
        class="app-nav"
      >
        <el-menu-item index="/qa">联网问答</el-menu-item>
        <el-menu-item index="/webpages">网页出题</el-menu-item>
        <el-menu-item index="/videos">B站视频</el-menu-item>
        <el-menu-item index="/knowledge">知识沉淀</el-menu-item>
        <el-menu-item index="/knowledge-query">知识查询</el-menu-item>
        <el-menu-item index="/settings">设置</el-menu-item>
      </el-menu>
    </el-header>
    <el-main class="app-main">
      <router-view />
    </el-main>
  </el-container>
</template>

<script setup>
import { computed } from 'vue'
import { useRoute } from 'vue-router'

const route = useRoute()
// /videos/:id 详情页也高亮「B站视频」
const activePath = computed(() =>
  route.path.startsWith('/videos') ? '/videos' : route.path
)
</script>

<style>
html,
body,
#app {
  height: 100%;
  margin: 0;
}
.app-shell {
  height: 100%;
}
.app-header {
  display: flex;
  align-items: center;
  border-bottom: 1px solid var(--el-border-color-light);
}
.app-title {
  font-weight: 700;
  margin-right: 32px;
  color: var(--el-color-primary);
}
.app-nav {
  flex: 1;
  border-bottom: none;
}
.app-main {
  width: 100%;
  max-width: 960px;
  margin: 0 auto;
}
</style>
