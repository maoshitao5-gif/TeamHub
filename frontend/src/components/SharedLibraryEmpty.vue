<template>
  <section class="library-entry" data-testid="shared-library-empty" v-loading="loading">
    <el-alert v-if="error" :title="error" type="error" :closable="false" show-icon />
    <template v-if="!loading">
      <template v-if="libraries.length">
        <h2>选择资料库</h2><p>点击要查看的资料库即可进入。</p>
        <div class="library-entry-grid">
          <button v-for="library in libraries" :key="library.id" type="button" class="library-entry-card" :data-testid="`shared-library-entry-${library.id}`" @click="emit('select', library.id)">
            <el-icon><FolderOpened /></el-icon><span><strong>{{ library.name }}</strong><small v-if="library.team_name">{{ library.team_name }}</small></span><el-icon><ArrowRight /></el-icon>
          </button>
        </div>
      </template>
      <el-empty v-else-if="!error" description="暂无可访问的资料库，请联系管理员" />
      <el-button class="library-entry-refresh" data-testid="shared-library-retry" @click="emit('retry')">{{ error ? '重试' : '刷新资料库' }}</el-button>
    </template>
    <p v-else class="loading-copy" role="status">正在加载共享资料库…</p>
  </section>
</template>
<script setup>
import { FolderOpened, ArrowRight } from '@element-plus/icons-vue'
defineProps({ libraries: { type: Array, default: () => [] }, loading: Boolean, error: { type: String, default: '' } })
const emit = defineEmits(['select', 'retry'])
</script>
<style scoped>
.library-entry { background: #fff; padding: 32px; border: 1px solid #e7edf4; border-radius: 14px; min-height: 250px; }
h2 { margin: 0 0 8px; color: #263b50; font-size: 23px; }
p { margin: 0 0 24px; color: #758495; }
.library-entry-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 14px; }
.library-entry-card { display: flex; align-items: center; gap: 14px; padding: 22px; background: #f8fbff; border: 1px solid #dbe7f5; border-radius: 10px; text-align: left; font: inherit; cursor: pointer; color: #365c88; transition: background .15s, border-color .15s; }
.library-entry-card:hover, .library-entry-card:focus-visible { background: #edf5ff; border-color: #4096ff; outline: none; }
.library-entry-card > span { display: flex; flex: 1; flex-direction: column; gap: 7px; min-width: 0; overflow-wrap: anywhere; }
.library-entry-card small { color: #758495; font-size: 13px; }
.library-entry-card .el-icon:first-child { font-size: 25px; }
.library-entry-refresh { margin-top: 20px; }
.el-alert { margin-bottom: 20px; }
.loading-copy { text-align: center; margin-top: 70px; }
</style>
