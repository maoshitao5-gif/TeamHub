<template>
  <div class="delivery-page">
    <template v-if="!auth.isLoggedIn">
      <el-alert v-if="!auth.sessionError" title="请先登录云服务" type="info" :closable="false">
        <el-button type="primary" @click="router.push({ path: '/login', query: { redirect: route.fullPath } })">前往登录</el-button>
      </el-alert>
    </template>
    <SharedLibraryEmpty v-else-if="auth.workspaceLoading || !workspace" :libraries="auth.allWorkspaces" :loading="auth.workspaceLoading" :error="auth.workspaceError" @select="selectLibrary" @retry="auth.refreshWorkspaces()" />
    <DeliveryPanel v-else :key="`${auth.serverId}:${auth.user?.id}:${workspace}:${app.libraryPath}`" :transport="deliveryTransport" :workspace-id="workspace" desktop @intaken="refreshPending" />
  </div>
</template>
<script setup>
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { useAppStore } from '@/stores/app'
import DeliveryPanel from '@/components/delivery/DeliveryPanel.vue'
import SharedLibraryEmpty from '@/components/SharedLibraryEmpty.vue'
import { deliveryTransport } from '@/api/delivery'
const auth = useAuthStore(), app = useAppStore()
const route = useRoute(), router = useRouter()
const workspace = computed(() => auth.currentWorkspace?.id || '')
function selectLibrary(id) { const library = auth.allWorkspaces.find(item => item.id === id); if (library) auth.switchWorkspace(library) }
function refreshPending() { app.fetchPendingCount?.() }
</script>
<style scoped>.delivery-page { padding: 28px; background: #fff; border-radius: 16px; min-height: 70vh; }</style>
