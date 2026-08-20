import { createRouter, createWebHistory } from 'vue-router'

const routes = [
  { path: '/', redirect: '/qa' },
  { path: '/qa', name: 'qa', component: () => import('../views/QaView.vue'), meta: { title: '联网问答' } },
  { path: '/webpages', name: 'webpages', component: () => import('../views/WebpageView.vue'), meta: { title: '网页出题' } },
  { path: '/videos', name: 'videos', component: () => import('../views/VideoView.vue'), meta: { title: 'B站视频' } },
  { path: '/videos/:id', name: 'video-detail', component: () => import('../views/VideoDetailView.vue'), meta: { title: '视频详情' } },
  { path: '/knowledge', name: 'knowledge', component: () => import('../views/KnowledgeView.vue'), meta: { title: '知识沉淀' } },
  { path: '/knowledge-query', name: 'knowledge-query', component: () => import('../views/KnowledgeQueryView.vue'), meta: { title: '知识查询' } },
  { path: '/settings', name: 'settings', component: () => import('../views/SettingsView.vue'), meta: { title: '设置' } },
]

const router = createRouter({
  history: createWebHistory(),
  routes,
})

router.afterEach((to) => {
  document.title = to.meta.title ? `${to.meta.title} - Saros` : 'Saros'
})

export default router
