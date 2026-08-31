<template>
  <el-dialog
    v-model="dialogVisible"
    :title="''"
    width="900px"
    :close-on-click-modal="false"
    :destroy-on-close="true"
    class="resource-dialog"
    align-center
  >
    <!-- ─── Custom Header ─── -->
    <template #header>
      <div class="rd-header">
        <div class="rd-header__left">
          <span class="rd-header__icon">📚</span>
          <div class="rd-header__text">
            <span class="rd-header__title">学习资料管理</span>
            <span class="rd-header__node">{{ nodeName }}</span>
          </div>
        </div>
        <div class="rd-header__stats">
          <span class="rd-stat">
            <span class="rd-stat__num">{{ totalCount }}</span>
            <span class="rd-stat__label">总计</span>
          </span>
          <span class="rd-stat rd-stat--web">
            <span class="rd-stat__num">{{ resources.web_resources.length }}</span>
            <span class="rd-stat__label">网址</span>
          </span>
          <span class="rd-stat rd-stat--upload">
            <span class="rd-stat__num">{{ resources.user_uploads.length }}</span>
            <span class="rd-stat__label">我上传</span>
          </span>
          <span class="rd-stat rd-stat--study">
            <span class="rd-stat__num">{{ resources.study_materials.length }}</span>
            <span class="rd-stat__label">AI 学习</span>
          </span>
        </div>
      </div>
    </template>

    <!-- ─── Toolbar ─── -->
    <div class="rd-toolbar">
      <el-radio-group v-model="activeTab" size="small">
        <el-radio-button value="all">全部 ({{ totalCount }})</el-radio-button>
        <el-radio-button value="web">🌐 网址 ({{ resources.web_resources.length }})</el-radio-button>
        <el-radio-button value="file">📂 文件 ({{ fileCount }})</el-radio-button>
      </el-radio-group>
      <div class="rd-toolbar__actions">
        <el-button size="small" @click="openAddUrl">
          <el-icon><Link /></el-icon>
          <span style="margin-left: 4px">添加网址</span>
        </el-button>
        <el-button size="small" @click="triggerFileUpload">
          <el-icon><UploadFilled /></el-icon>
          <span style="margin-left: 4px">上传文件</span>
        </el-button>
        <input
          ref="fileInputRef"
          type="file"
          style="display: none"
          @change="onFileSelected"
        />
      </div>
    </div>

    <!-- ─── Add/Edit URL Inline Form ─── -->
    <div v-if="urlFormVisible" class="rd-inline-form">
      <div class="rd-inline-form__header">
        <span>{{ editingUrl ? '编辑网址' : '添加网址' }}</span>
        <el-button text size="small" :icon="Close" @click="cancelUrlForm" />
      </div>
      <div class="rd-inline-form__body">
        <el-input v-model="urlFormData.title" placeholder="标题（可选）" size="small" class="rd-form-field" />
        <el-input v-model="urlFormData.url" placeholder="URL *" size="small" class="rd-form-field" />
        <el-input v-model="urlFormData.summary" placeholder="简介（可选）" size="small" class="rd-form-field" />
        <el-select v-model="urlFormData.category" size="small" class="rd-form-select" placeholder="分类">
          <el-option label="论文" value="论文" />
          <el-option label="视频" value="视频" />
          <el-option label="课程" value="课程" />
          <el-option label="代码" value="代码" />
          <el-option label="文档" value="文档" />
          <el-option label="教程" value="教程" />
          <el-option label="书籍" value="书籍" />
          <el-option label="网页" value="网页" />
          <el-option label="其他" value="其他" />
        </el-select>
        <el-button size="small" type="primary" :loading="urlSaving" @click="saveUrl">
          {{ editingUrl ? '保存' : '添加' }}
        </el-button>
      </div>
    </div>

    <!-- ─── Upload Inline Form ─── -->
    <div v-if="pendingFile" class="rd-inline-form">
      <div class="rd-inline-form__header">
        <span>上传文件</span>
        <el-button text size="small" :icon="Close" @click="cancelUpload" />
      </div>
      <div class="rd-inline-form__body">
        <span class="rd-form-filename">
          <el-icon><Document /></el-icon>
          {{ pendingFile.name }}
        </span>
        <el-select v-model="uploadCategory" size="small" class="rd-form-select" placeholder="分类">
          <el-option label="书籍" value="书籍" />
          <el-option label="论文" value="论文" />
          <el-option label="教程" value="教程" />
          <el-option label="官方文档" value="官方文档" />
          <el-option label="代码" value="代码" />
          <el-option label="视频" value="视频" />
          <el-option label="其他" value="其他" />
        </el-select>
        <el-input v-model="uploadNote" placeholder="备注（可选）" size="small" class="rd-form-field" />
        <el-button size="small" type="primary" :loading="uploading" @click="uploadFile">确认上传</el-button>
      </div>
    </div>

    <!-- ─── Edit Upload Inline Form ─── -->
    <div v-if="editingUpload" class="rd-inline-form">
      <div class="rd-inline-form__header">
        <span>编辑文件信息</span>
        <el-button text size="small" :icon="Close" @click="editingUpload = null" />
      </div>
      <div class="rd-inline-form__body">
        <span class="rd-form-filename">
          <el-icon><Document /></el-icon>
          {{ editingUpload.file }}
        </span>
        <el-select v-model="editUploadData.category" size="small" class="rd-form-select" placeholder="分类">
          <el-option label="书籍" value="书籍" />
          <el-option label="论文" value="论文" />
          <el-option label="教程" value="教程" />
          <el-option label="官方文档" value="官方文档" />
          <el-option label="代码" value="代码" />
          <el-option label="视频" value="视频" />
          <el-option label="其他" value="其他" />
        </el-select>
        <el-input v-model="editUploadData.note" placeholder="备注（可选）" size="small" class="rd-form-field" />
        <el-button size="small" type="primary" :loading="uploadSaving" @click="saveUploadEdit">保存</el-button>
      </div>
    </div>

    <!-- ─── Web Resources Section ─── -->
    <section v-if="showWebSection && webItems.length > 0" class="rd-section">
      <div v-if="showWebSectionHeader" class="rd-section__header">
        <span class="rd-section__dot rd-section__dot--web" />
        <span class="rd-section__title">🌐 网址资料</span>
        <span class="rd-section__count">{{ webItems.length }}</span>
      </div>
      <div class="rd-list">
        <div
          v-for="item in pagedWebItems"
          :key="`web-${item.url}`"
          class="rd-card rd-card--web"
        >
          <div class="rd-card__icon"><el-icon size="18"><Link /></el-icon></div>
          <div class="rd-card__main">
            <div class="rd-card__title-row">
              <a :href="item.url" target="_blank" rel="noopener" class="rd-card__link">
                {{ item.title || item.url }}
              </a>
              <el-tag size="small" class="rd-card__tag rd-card__tag--web">
                {{ categoryLabel(item.category) }}
              </el-tag>
            </div>
            <div v-if="item.summary" class="rd-card__summary">{{ item.summary }}</div>
            <div class="rd-card__meta">
              <span class="rd-card__url">{{ item.url }}</span>
              <span class="rd-card__sep">·</span>
              <span>{{ formatDate(item.added_at) }}</span>
            </div>
          </div>
          <div class="rd-card__actions">
            <el-button size="small" text :icon="Edit" @click="startEdit({ ...item, _type: 'web' })" class="rd-card__btn" />
            <el-button size="small" text :icon="Delete" @click="removeWeb(item)" class="rd-card__btn rd-card__btn--danger" />
          </div>
        </div>
      </div>
    </section>

    <!-- ─── User Uploads Section ─── -->
    <section v-if="showUploadsSection && uploadsItems.length > 0" class="rd-section">
      <div v-if="showUploadsSectionHeader" class="rd-section__header">
        <span class="rd-section__dot rd-section__dot--upload" />
        <span class="rd-section__title">📂 我上传的文件</span>
        <span class="rd-section__count">{{ uploadsItems.length }}</span>
      </div>
      <div class="rd-list">
        <div
          v-for="item in pagedUploadsItems"
          :key="`up-${item.file}`"
          class="rd-card rd-card--upload"
          @click="openPreview('upload', item)"
        >
          <div class="rd-card__icon">{{ getFileIcon(item.file) }}</div>
          <div class="rd-card__main">
            <div class="rd-card__title-row">
              <a
                :href="downloadUrl(item.file)"
                target="_blank"
                rel="noopener"
                class="rd-card__link"
                @click.stop
              >
                {{ item.file }}
              </a>
              <el-tag size="small" class="rd-card__tag rd-card__tag--upload">
                {{ item.category || '其他' }}
              </el-tag>
            </div>
            <div v-if="item.note" class="rd-card__summary">{{ item.note }}</div>
            <div class="rd-card__meta">
              <span v-if="item.size">{{ formatSize(item.size) }}</span>
              <span v-if="item.size" class="rd-card__sep">·</span>
              <span>{{ formatDate(item.moved_at) }}</span>
              <span class="rd-card__sep">·</span>
              <span class="rd-card__hint">点击预览</span>
            </div>
          </div>
          <div class="rd-card__actions" @click.stop>
            <el-button size="small" text :icon="View" @click="openPreview('upload', item)" class="rd-card__btn" title="预览" />
            <el-button size="small" text :icon="Edit" @click="startEdit({ ...item, _type: 'upload' })" class="rd-card__btn" />
            <el-button size="small" text :icon="Delete" @click="removeUpload(item)" class="rd-card__btn rd-card__btn--danger" />
          </div>
        </div>
      </div>
    </section>

    <!-- ─── Study Materials Section ─── -->
    <section v-if="showStudySection && (studyItems.length > 0 || studyPath)" class="rd-section">
      <div v-if="showStudySectionHeader" class="rd-section__header">
        <span class="rd-section__dot rd-section__dot--study" />
        <span class="rd-section__title">✨ AI 学习资料</span>
        <span class="rd-section__count">{{ studyItems.length }}</span>
      </div>
      <!-- Breadcrumb: only shown after drilling into a sub-folder. -->
      <nav v-if="studyPath" class="rd-breadcrumb">
        <el-button text size="small" class="rd-breadcrumb__root" @click="navigateStudy('')">
          <el-icon><Folder /></el-icon>
          <span style="margin-left: 4px">study_materials</span>
        </el-button>
        <template v-for="(seg, i) in studyBreadcrumb" :key="seg">
          <span class="rd-breadcrumb__sep">/</span>
          <el-button
            v-if="i < studyBreadcrumb.length - 1"
            text
            size="small"
            class="rd-breadcrumb__seg"
            @click="navigateStudy(studyBreadcrumb.slice(0, i + 1).join('/'))"
          >
            {{ seg }}
          </el-button>
          <span v-else class="rd-breadcrumb__current">{{ seg }}</span>
        </template>
      </nav>
      <div v-if="studyLoading" class="rd-list">
        <div class="rd-empty rd-empty--inline">
          <span class="rd-empty__icon">⏳</span>
          <span class="rd-empty__text">加载中…</span>
        </div>
      </div>
      <div v-else-if="studyItems.length === 0" class="rd-list">
        <div class="rd-empty rd-empty--inline">
          <span class="rd-empty__icon">📂</span>
          <span class="rd-empty__text">该文件夹暂无内容</span>
        </div>
      </div>
      <div v-else class="rd-list">
        <div
          v-for="item in pagedStudyItems"
          :key="`sm-${item.file}`"
          :class="['rd-card', item.type === 'folder' ? 'rd-card--folder' : 'rd-card--study']"
          @click="onStudyItemClick(item)"
        >
          <div class="rd-card__icon">
            <el-icon v-if="item.type === 'folder'" size="20"><Folder /></el-icon>
            <span v-else>{{ getFileIcon(item.file) }}</span>
          </div>
          <div class="rd-card__main">
            <div class="rd-card__title-row">
              <!-- Files: title click bubbles to the card's onStudyItemClick
                   which opens the in-app preview.  The explicit ⬇ button
                   on the right is the only download entry point. -->
              <span
                v-if="item.type !== 'folder'"
                class="rd-card__link rd-card__link--preview"
              >
                {{ displayName(item.file) }}
              </span>
              <span v-else class="rd-card__link rd-card__link--folder">
                {{ displayName(item.file) }}
              </span>
              <el-tag
                v-if="item.type === 'folder'"
                size="small"
                class="rd-card__tag rd-card__tag--folder"
              >
                {{ item.children_count ?? 0 }} 篇
              </el-tag>
              <el-tag
                v-else
                size="small"
                class="rd-card__tag rd-card__tag--study"
              >
                {{ item.category || 'AI 学习' }}
              </el-tag>
            </div>
            <div v-if="item.type !== 'folder'" class="rd-card__meta">
              <span v-if="item.size">{{ formatSize(item.size) }}</span>
              <span v-if="item.size" class="rd-card__sep">·</span>
              <span>{{ formatDate(item.moved_at) }}</span>
              <span class="rd-card__sep">·</span>
              <span class="rd-card__hint">点击预览</span>
            </div>
          </div>
          <div v-if="item.type !== 'folder'" class="rd-card__actions" @click.stop>
            <el-button size="small" text :icon="View" @click="openPreview('study', item)" class="rd-card__btn" title="预览" />
            <a
              :href="studyDownloadUrl(item.file)"
              :download="displayName(item.file)"
              class="rd-card__btn rd-card__btn--link"
              title="下载到本地"
            >⬇</a>
          </div>
        </div>
      </div>
    </section>

    <!-- ─── Empty state ─── -->
    <div v-if="totalCount === 0" class="rd-empty">
      <span class="rd-empty__icon">📋</span>
      <span class="rd-empty__text">暂无学习资料</span>
      <span class="rd-empty__hint">点击上方「添加网址」或「上传文件」开始管理</span>
    </div>

    <!-- ─── File Preview Drawer ─── -->
    <FilePreviewDialog
      v-model="previewVisible"
      :file-name="previewingFile ? displayName(previewingFile.file) : ''"
      :download-path="previewingPath"
      :file-size="previewingFile?.size"
    />
  </el-dialog>
</template>

<script setup lang="ts">
import { ref, computed, watch } from 'vue'
import { ElMessage, ElMessageBox } from 'element-plus'
import {
  Close,
  Delete,
  Edit,
  Document,
  Folder,
  Link,
  UploadFilled,
  View,
} from '@element-plus/icons-vue'
import * as api from '@/api'
import type { NodeResources, WebResource, UploadResource, StudyMaterialItem } from '@/types/graph'
import { getFileIcon } from './file-preview/filePreviewUtils'
import FilePreviewDialog from './file-preview/FilePreviewDialog.vue'

const props = defineProps<{
  modelValue: boolean
  domain: string
  nodeName: string
  resources: NodeResources
}>()

const emit = defineEmits<{
  'update:modelValue': [val: boolean]
  'update:resources': [val: NodeResources]
}>()

const dialogVisible = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// ── State ──
const activeTab = ref<'all' | 'web' | 'file'>('all')

// Add/Edit URL
const urlFormVisible = ref(false)
const editingUrl = ref<WebResource | null>(null)
const urlFormData = ref({ title: '', url: '', summary: '', category: '网页' })
const urlSaving = ref(false)

// Upload
const fileInputRef = ref<HTMLInputElement | null>(null)
const pendingFile = ref<File | null>(null)
const uploadCategory = ref('其他')
const uploadNote = ref('')
const uploading = ref(false)

// Edit Upload
const editingUpload = ref<UploadResource | null>(null)
const editUploadData = ref({ category: '其他', note: '' })
const uploadSaving = ref(false)

// ── Preview ──
const previewVisible = ref(false)
const previewingFile = ref<UploadResource | StudyMaterialItem | null>(null)
const previewingKind = ref<'upload' | 'study'>('upload')

// ── Computed ──
const totalCount = computed(
  () =>
    props.resources.web_resources.length +
    props.resources.user_uploads.length +
    props.resources.study_materials.length,
)

const fileCount = computed(
  () => props.resources.user_uploads.length + props.resources.study_materials.length,
)

const webItems = computed(() => props.resources.web_resources)
const uploadsItems = computed(() => props.resources.user_uploads)
// study_materials supports drill-down: at the root it mirrors
// props.resources.study_materials; when navigated into a folder, it
// holds the result of a follow-up listStudyMaterials() call.
const studyPath = ref<string>('')
const studyItems = ref<StudyMaterialItem[]>([])
const studyLoading = ref(false)
function resetStudyNav() {
  studyPath.value = ''
  studyItems.value = props.resources.study_materials ?? []
}
resetStudyNav()

/** Enter a sub-directory of study_materials/.  Empty string goes back
 *  to the root and reuses the props snapshot to avoid a refetch. */
async function navigateStudy(path: string) {
  if (path === studyPath.value) return
  if (path === '') {
    resetStudyNav()
    return
  }
  studyLoading.value = true
  try {
    studyItems.value = await api.listStudyMaterials(props.domain, props.nodeName, path)
    studyPath.value = path
  } catch (e: any) {
    ElMessage.error(e.message || '加载文件夹失败')
  } finally {
    studyLoading.value = false
  }
}

function onStudyItemClick(item: StudyMaterialItem) {
  if (item.type === 'folder') {
    navigateStudy(item.file)
  } else {
    openPreview('study', item)
  }
}

/** Tail segment of a relative path (for display inside a folder). */
function displayName(file: string): string {
  return file.split('/').pop() || file
}

/** Top-level path segments for the breadcrumb. */
const studyBreadcrumb = computed(() => {
  if (!studyPath.value) return []
  return studyPath.value.split('/').filter(Boolean)
})

// 各 section 是否可见（按 tab 控制 + 总开关）
const showWebSection = computed(
  () => activeTab.value === 'all' || activeTab.value === 'web',
)
const showUploadsSection = computed(
  () => activeTab.value === 'all' || activeTab.value === 'file',
)
const showStudySection = computed(
  () => activeTab.value === 'all' || activeTab.value === 'file',
)

const showWebSectionHeader = computed(() => activeTab.value === 'all')
const showUploadsSectionHeader = computed(
  () => activeTab.value === 'all' && uploadsItems.value.length > 0,
)
const showStudySectionHeader = computed(
  () => activeTab.value === 'all' && studyItems.value.length > 0,
)

// "文件" tab 时合并显示（仅文件区，不再单独分页）
const pagedUploadsItems = computed(() => uploadsItems.value)
const pagedStudyItems = computed(() => studyItems.value)
const pagedWebItems = computed(() => webItems.value)

const previewingPath = computed(() => {
  if (!previewingFile.value) return ''
  return previewingKind.value === 'study'
    ? studyDownloadUrl(previewingFile.value.file)
    : downloadUrl(previewingFile.value.file)
})

// ── Local resource sync ──
function updateResources(newRes: NodeResources) {
  emit('update:resources', newRes)
}

// ── URL Add/Edit ──
function openAddUrl() {
  editingUrl.value = null
  urlFormData.value = { title: '', url: '', summary: '', category: '网页' }
  urlFormVisible.value = true
}

function startEdit(item: WebResource & { _type: 'web' } | (UploadResource & { _type: 'upload' })) {
  if (item._type === 'web') {
    editingUrl.value = item
    urlFormData.value = {
      title: item.title,
      url: item.url,
      summary: item.summary,
      category: item.category,
    }
    urlFormVisible.value = true
    editingUpload.value = null
  } else {
    editingUpload.value = item
    editUploadData.value = {
      category: item.category || '其他',
      note: item.note || '',
    }
    urlFormVisible.value = false
  }
}

function cancelUrlForm() {
  urlFormVisible.value = false
  editingUrl.value = null
}

async function saveUrl() {
  if (!urlFormData.value.url.trim()) {
    ElMessage.error('URL 不能为空')
    return
  }
  urlSaving.value = true
  try {
    if (editingUrl.value) {
      await api.editWebResource(props.domain, props.nodeName, editingUrl.value.url, { ...urlFormData.value })
      const newWeb = props.resources.web_resources.map((r) =>
        r.url === editingUrl.value!.url ? { ...urlFormData.value, added_at: r.added_at } : r,
      )
      updateResources({ ...props.resources, web_resources: newWeb })
      ElMessage.success('已更新')
    } else {
      const res = await api.addWebResource(props.domain, props.nodeName, { ...urlFormData.value })
      updateResources({
        ...props.resources,
        web_resources: [...props.resources.web_resources, res.item],
      })
      ElMessage.success('已添加')
    }
    cancelUrlForm()
  } catch (e: any) {
    ElMessage.error(e.message || '操作失败')
  } finally {
    urlSaving.value = false
  }
}

// ── File Upload ──
function triggerFileUpload() {
  fileInputRef.value?.click()
}

function onFileSelected(e: Event) {
  const input = e.target as HTMLInputElement
  if (input.files && input.files.length > 0) {
    pendingFile.value = input.files[0]
    uploadCategory.value = '其他'
    uploadNote.value = ''
  }
}

function cancelUpload() {
  pendingFile.value = null
  uploadCategory.value = '其他'
  uploadNote.value = ''
  if (fileInputRef.value) fileInputRef.value.value = ''
}

async function uploadFile() {
  if (!pendingFile.value) return
  uploading.value = true
  try {
    const res = await api.uploadFile(props.domain, props.nodeName, pendingFile.value, uploadCategory.value, uploadNote.value)
    updateResources({
      ...props.resources,
      user_uploads: [...props.resources.user_uploads, res.item],
    })
    cancelUpload()
    ElMessage.success('上传成功')
  } catch (e: any) {
    ElMessage.error(e.message || '上传失败')
  } finally {
    uploading.value = false
  }
}

async function saveUploadEdit() {
  if (!editingUpload.value) return
  uploadSaving.value = true
  try {
    await api.editUpload(props.domain, props.nodeName, editingUpload.value.file, { ...editUploadData.value })
    const newUploads = props.resources.user_uploads.map((r) =>
      r.file === editingUpload.value!.file
        ? { ...r, category: editUploadData.value.category, note: editUploadData.value.note }
        : r,
    )
    updateResources({ ...props.resources, user_uploads: newUploads })
    editingUpload.value = null
    ElMessage.success('已更新')
  } catch (e: any) {
    ElMessage.error(e.message || '更新失败')
  } finally {
    uploadSaving.value = false
  }
}

// ── Delete ──
async function removeWeb(item: WebResource) {
  try {
    await ElMessageBox.confirm('确定删除这条资料吗？', '提示', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api.deleteWebResource(props.domain, props.nodeName, item.url)
    updateResources({
      ...props.resources,
      web_resources: props.resources.web_resources.filter((r) => r.url !== item.url),
    })
    ElMessage.success('已删除')
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

async function removeUpload(item: UploadResource) {
  try {
    await ElMessageBox.confirm(`确定删除「${item.file}」吗？`, '提示', {
      confirmButtonText: '删除',
      cancelButtonText: '取消',
      type: 'warning',
    })
    await api.deleteUpload(props.domain, props.nodeName, item.file)
    updateResources({
      ...props.resources,
      user_uploads: props.resources.user_uploads.filter((r) => r.file !== item.file),
    })
    ElMessage.success('已删除')
  } catch (e: any) {
    if (e !== 'cancel') ElMessage.error(e.message || '删除失败')
  }
}

// ── Preview ──
function openPreview(kind: 'upload' | 'study', file: UploadResource | StudyMaterialItem) {
  previewingKind.value = kind
  previewingFile.value = file
  previewVisible.value = true
}

// ── Helpers ──
function downloadUrl(filename: string): string {
  return api.getDownloadUrl(props.domain, props.nodeName, filename)
}

function studyDownloadUrl(filename: string): string {
  return api.getStudyMaterialUrl(props.domain, props.nodeName, filename)
}

function categoryLabel(cat: string): string {
  const map: Record<string, string> = {
    doc: '文档',
    tutorial: '教程',
    video: '视频',
    book: '书籍',
    web: '网页',
    论文: '论文',
    视频: '视频',
    课程: '课程',
    代码: '代码',
    文档: '文档',
    教程: '教程',
    书籍: '书籍',
    网页: '网页',
    其他: '其他',
  }
  return map[cat] || cat || '网页'
}

function formatDate(dateStr: string): string {
  if (!dateStr) return ''
  try {
    const d = new Date(dateStr)
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
  } catch {
    return dateStr
  }
}

function formatSize(bytes: number | undefined): string {
  if (!bytes || bytes <= 0) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

// 当对话框关闭时，重置 preview 状态；重开时回到 study_materials 根
watch(dialogVisible, (open) => {
  if (!open) {
    previewVisible.value = false
    previewingFile.value = null
  } else {
    resetStudyNav()
  }
})
</script>

<style scoped>
/* ════════════════════════════════════════════════════════════
   Resource Dialog
   ════════════════════════════════════════════════════════════ */

/* ── Header ── */
.rd-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  width: 100%;
}

.rd-header__left {
  display: flex;
  align-items: center;
  gap: 12px;
}

.rd-header__icon {
  font-size: 22px;
}

.rd-header__text {
  display: flex;
  flex-direction: column;
  gap: 2px;
}

.rd-header__title {
  font-size: 17px;
  font-weight: 700;
  color: var(--text-primary);
}

.rd-header__node {
  font-size: 12px;
  color: var(--text-muted);
}

.rd-header__stats {
  display: flex;
  gap: 16px;
}

.rd-stat {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 1px;
}

.rd-stat__num {
  font-size: 20px;
  font-weight: 800;
  color: var(--text-primary);
  font-family: 'JetBrains Mono', monospace;
  line-height: 1;
}

.rd-stat__label {
  font-size: 10.5px;
  color: var(--text-muted);
  text-transform: uppercase;
  letter-spacing: 0.06em;
}

.rd-stat--web .rd-stat__num { color: var(--accent-cyan); }
.rd-stat--upload .rd-stat__num { color: var(--accent-amber); }
.rd-stat--study .rd-stat__num { color: var(--accent-green); }

/* ── Toolbar ── */
.rd-toolbar {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
  gap: 12px;
  flex-wrap: wrap;
}

.rd-toolbar__actions {
  display: flex;
  gap: 8px;
}

/* ── Inline form ── */
.rd-inline-form {
  margin-bottom: 16px;
  padding: 14px 16px;
  background: var(--bg-tertiary);
  border: 1px solid var(--accent-blue);
  border-radius: 10px;
  box-shadow: 0 0 0 3px rgba(76, 125, 255, 0.08);
}

.rd-inline-form__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 10px;
  font-size: 13px;
  font-weight: 700;
  color: var(--text-primary);
}

.rd-inline-form__body {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}

.rd-form-field {
  flex: 1;
  min-width: 140px;
}

.rd-form-select {
  width: 110px;
  flex-shrink: 0;
}

.rd-form-filename {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 13px;
  color: var(--text-primary);
  font-weight: 600;
  flex: 1;
  min-width: 140px;
}

/* ── Section ── */
.rd-section {
  margin-bottom: 18px;
}

.rd-section__header {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 8px;
  padding: 0 2px;
}

.rd-section__dot {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  display: inline-block;
}
.rd-section__dot--web    { background: var(--accent-cyan); }
.rd-section__dot--upload  { background: var(--accent-amber); }
.rd-section__dot--study   { background: var(--accent-green); }

.rd-section__title {
  font-size: 12.5px;
  font-weight: 700;
  color: var(--text-secondary);
  letter-spacing: 0.04em;
}

.rd-section__count {
  font-size: 11px;
  font-family: 'JetBrains Mono', monospace;
  color: var(--text-muted);
  padding: 1px 6px;
  background: var(--bg-tertiary);
  border-radius: 4px;
}

/* Breadcrumb showing the current path inside study_materials/.  Hover
  states use the accent purple to signal the link is navigable. */
.rd-breadcrumb {
  display: flex;
  align-items: center;
  gap: 4px;
  margin-bottom: 10px;
  padding: 6px 10px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 8px;
  font-size: 12.5px;
  font-family: 'JetBrains Mono', monospace;
  flex-wrap: wrap;
}

.rd-breadcrumb__root,
.rd-breadcrumb__seg {
  color: var(--accent-blue) !important;
  font-family: inherit !important;
  padding: 2px 6px !important;
}

.rd-breadcrumb__root:hover,
.rd-breadcrumb__seg:hover {
  background: rgba(76, 125, 255, 0.1) !important;
  color: var(--accent-cyan) !important;
}

.rd-breadcrumb__sep {
  color: var(--text-muted);
  opacity: 0.6;
  user-select: none;
}

.rd-breadcrumb__current {
  color: var(--text-primary);
  font-weight: 700;
  padding: 2px 4px;
}

/* ── List ── */
.rd-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* ── Card ── */
.rd-card {
  display: flex;
  align-items: flex-start;
  gap: 12px;
  padding: 12px 14px;
  background: var(--bg-tertiary);
  border: 1px solid var(--border-color);
  border-radius: 10px;
  transition: border-color 0.15s, transform 0.15s, box-shadow 0.15s;
}

.rd-card:hover {
  border-color: var(--border-light);
  transform: translateY(-1px);
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.2);
}

.rd-card--web {
  border-left: 3px solid var(--accent-cyan);
}

.rd-card--upload {
  border-left: 3px solid var(--accent-amber);
  cursor: pointer;
}

.rd-card--study {
  border-left: 3px solid var(--accent-green);
  cursor: pointer;
}

/* Folder card: distinguished by purple left border + folder icon.
 *  Same hover treatment so users immediately understand it's clickable. */
.rd-card--folder {
  border-left: 3px solid var(--accent-purple);
  cursor: pointer;
  background: linear-gradient(
    135deg,
    var(--bg-tertiary) 0%,
    rgba(124, 92, 255, 0.05) 100%
  );
}

.rd-card--folder .rd-card__icon {
  background: rgba(124, 92, 255, 0.12);
  color: var(--accent-purple);
}

.rd-card__icon {
  width: 32px;
  height: 32px;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 8px;
  flex-shrink: 0;
  margin-top: 1px;
  font-size: 18px;
}

.rd-card--web .rd-card__icon {
  background: rgba(6, 182, 212, 0.12);
  color: var(--accent-cyan);
}

.rd-card--upload .rd-card__icon {
  background: rgba(245, 158, 11, 0.12);
  color: var(--accent-amber);
}

.rd-card--study .rd-card__icon {
  background: rgba(34, 211, 165, 0.12);
  color: var(--accent-green);
}

.rd-card__main {
  flex: 1;
  min-width: 0;
}

.rd-card__title-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.rd-card__link {
  color: var(--accent-blue);
  text-decoration: none;
  font-size: 14px;
  font-weight: 600;
  word-break: break-all;
  transition: color 0.15s;
  border-bottom: 1px solid transparent;
}

.rd-card__link:hover {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
}

/* Preview trigger — looks like a link but isn't an <a>, so clicking
 *  bubbles to the card's onStudyItemClick handler instead of
 *  navigating to a download URL.  Inherits color/weight from
 *  .rd-card__link. */
.rd-card__link--preview {
  cursor: pointer;
  border-bottom-color: transparent;
}
.rd-card__link--preview:hover {
  color: var(--accent-cyan);
  border-bottom-color: var(--accent-cyan);
}

.rd-card__tag {
  flex-shrink: 0;
  font-size: 10.5px !important;
  height: 18px !important;
  padding: 0 6px !important;
}

.rd-card__tag--web {
  background: rgba(6, 182, 212, 0.15) !important;
  border-color: rgba(6, 182, 212, 0.35) !important;
  color: var(--accent-cyan) !important;
}

.rd-card__tag--upload {
  background: rgba(245, 158, 11, 0.15) !important;
  border-color: rgba(245, 158, 11, 0.35) !important;
  color: var(--accent-amber) !important;
}

.rd-card__tag--study {
  background: rgba(34, 211, 165, 0.15) !important;
  border-color: rgba(34, 211, 165, 0.35) !important;
  color: var(--accent-green) !important;
}

.rd-card__tag--folder {
  background: rgba(124, 92, 255, 0.15) !important;
  border-color: rgba(124, 92, 255, 0.35) !important;
  color: var(--accent-purple) !important;
}

/* Folder link has no anchor underline / hover colour change — it
 *  represents a navigable item, not a download. */
.rd-card__link--folder {
  color: var(--text-primary);
  border-bottom: none;
}
.rd-card__link--folder:hover {
  color: var(--accent-purple);
}

.rd-card__summary {
  font-size: 13px;
  color: var(--text-secondary);
  margin-bottom: 4px;
  line-height: 1.55;
}

.rd-card__meta {
  display: flex;
  align-items: center;
  gap: 6px;
  font-size: 11.5px;
  color: var(--text-muted);
  flex-wrap: wrap;
  font-family: 'JetBrains Mono', monospace;
}

.rd-card__url {
  word-break: break-all;
}

.rd-card__sep {
  opacity: 0.5;
}

.rd-card__hint {
  color: var(--accent-blue);
  opacity: 0.7;
}

/* ── Card actions ── */
.rd-card__actions {
  display: flex;
  align-items: center;
  gap: 2px;
  flex-shrink: 0;
}

.rd-card__btn {
  opacity: 0;
  transition: opacity 0.15s;
}

.rd-card:hover .rd-card__btn {
  opacity: 1;
}

.rd-card__btn--danger:hover {
  color: var(--el-color-danger) !important;
}

.rd-card__btn--link {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 28px;
  height: 28px;
  font-size: 14px;
  text-decoration: none;
  color: var(--text-muted);
  border-radius: 4px;
  transition: color 0.15s, background 0.15s;
}

.rd-card__btn--link:hover {
  color: var(--accent-blue);
  background: var(--bg-hover);
}

/* ── Empty ── */
.rd-empty {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  padding: 60px 20px;
  text-align: center;
}

/* Inline empty (loading / empty folder) sits inside the list area
 *  instead of the dialog body, so the padding is tighter. */
.rd-empty--inline {
  padding: 28px 16px;
  background: var(--bg-tertiary);
  border: 1px dashed var(--border-color);
  border-radius: 10px;
}

.rd-empty__icon {
  font-size: 40px;
  opacity: 0.5;
}

.rd-empty--inline .rd-empty__icon {
  font-size: 24px;
}

.rd-empty__text {
  font-size: 15px;
  font-weight: 600;
  color: var(--text-secondary);
}

.rd-empty--inline .rd-empty__text {
  font-size: 13px;
}

.rd-empty__hint {
  font-size: 12px;
  color: var(--text-muted);
}

/* ── Dialog overrides ── */
.resource-dialog :deep(.el-dialog__body) {
  padding: 0 20px 20px;
}

.resource-dialog :deep(.el-dialog__header) {
  padding: 20px 20px 0;
  margin-right: 0;
}
</style>