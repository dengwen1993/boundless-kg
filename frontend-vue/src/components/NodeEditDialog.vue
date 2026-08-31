<template>
  <el-dialog
    :model-value="visible"
    :title="dialogTitle"
    width="480px"
    @update:model-value="$emit('update:visible', $event)"
    @open="onOpen"
  >
    <el-form :model="form" label-width="100px" @submit.prevent="onSave">
      <!-- Parent chip (create-child mode only) -->
      <el-form-item v-if="mode === 'create-child'" label="父节点">
        <el-tag type="warning" size="large" disable-transitions>
          {{ parentName }}
        </el-tag>
        <span class="parent-hint">将作为子节点链接自动附加</span>
      </el-form-item>

      <el-form-item label="节点名" required>
        <el-input
          ref="nameInputRef"
          v-model="form.name"
          placeholder="给节点起个名字…"
          @keyup.enter="onSave"
        />
      </el-form-item>

      <!-- Edit-only fields (hidden in create-child mode) -->
      <template v-if="mode === 'update'">
        <el-form-item label="层级标签">
          <el-input v-model="form.tag" placeholder="L1 / L2 / concept / skill …" />
        </el-form-item>
        <el-form-item label="关联节点">
          <el-input
            v-model="form.linksStr"
            placeholder="例: 认知能力, 语言发展"
          />
          <div class="field-hint">多个用逗号分隔，links 为父→子方向</div>
        </el-form-item>
      </template>

      <el-form-item :label="mode === 'create-child' ? '备注' : '备注'">
        <el-input
          v-model="form.note"
          type="textarea"
          :rows="3"
          :placeholder="mode === 'create-child' ? '备注（可选）' : '备注（可选，写入 note.md）'"
        />
      </el-form-item>
    </el-form>

    <template #footer>
      <el-button @click="$emit('update:visible', false)">取消</el-button>
      <el-button type="primary" :loading="graphStore.saving" @click="onSave">
        {{ mode === 'create-child' ? '新增' : '保存' }}
      </el-button>
    </template>
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, reactive, computed, nextTick, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { useGraphStore } from '@/stores/graph'
import type { GraphNode } from '@/types/graph'

const props = defineProps<{
  visible: boolean
  mode: 'update' | 'create-child'
  node: GraphNode | null
  parentName: string
}>()

const emit = defineEmits<{
  'update:visible': [val: boolean]
  saved: []
}>()

const graphStore = useGraphStore()

const form = reactive({
  name: '',
  tag: '',
  linksStr: '',
  note: '',
})

const nameInputRef = ref<any>(null)

const dialogTitle = computed(() => {
  if (props.mode === 'create-child') {
    return `为「${props.parentName}」新增子节点`
  }
  return `编辑节点：${props.node?.name ?? ''}`
})

function onOpen() {
  // reset form
  if (props.mode === 'create-child') {
    form.name = ''
    form.tag = ''
    form.linksStr = ''
    form.note = ''
  } else {
    form.name = props.node?.name ?? ''
    form.tag = ''
    form.linksStr = (props.node?.links ?? []).join(', ')
    form.note = ''
  }
  // focus name input
  nextTick(() => {
    nameInputRef.value?.focus?.()
    if (props.mode === 'update') {
      nameInputRef.value?.select?.()
    }
  })
}

// Also watch for visibility changes (for when dialog opens)
watch(
  () => props.visible,
  (val) => {
    if (val) onOpen()
  },
)

async function onSave() {
  const name = form.name.trim()
  if (!name) {
    ElMessage.error('节点名不能为空')
    return
  }

  try {
    if (props.mode === 'create-child') {
      // Add child node — parent is auto-linked by backend
      const msg = await graphStore.addNode(name, props.parentName)
      ElMessage.success(`已在「${props.parentName}」下新增「${name}」`)
    } else {
      // Update existing node
      const oldName = props.node?.name ?? ''
      const links = form.linksStr
        .split(/[,，]/)
        .map((s) => s.trim())
        .filter(Boolean)
      const msg = await graphStore.updateNode(oldName, {
        newName: name,
        newLinks: links,
      })
      ElMessage.success(`已更新「${oldName}」→「${name}」`)
    }
    emit('update:visible', false)
    emit('saved')
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  }
}
</script>

<style scoped>
.parent-hint {
  margin-left: 8px;
  font-size: 12px;
  color: var(--text-muted);
}

.field-hint {
  font-size: 11px;
  color: var(--text-muted);
  margin-top: 4px;
}
</style>
