/**
 * 文件预览数据加载 composable
 * 负责 fetch blob、解析内容、管理 loading/error 状态及 ObjectURL 生命周期
 */
import { ref, onUnmounted } from 'vue'
import { getPreviewType, getExt } from './filePreviewUtils'

export interface PreviewFile {
  fileName: string
  fileSize?: number
  fileType?: string
  downloadPath: string
}

export function useFilePreview() {
  const loading = ref(false)
  const error = ref('')
  const textContent = ref('')
  const imageUrl = ref('')
  const mediaUrl = ref('')
  const fileBlob = ref<Blob | null>(null)
  const blobUrl = ref('')

  let currentObjectUrl = ''
  /** 每次 load() 调用时递增，用于丢弃过期的异步结果（防止快速切换文件的竞态条件） */
  let loadToken = 0

  function revokeObjectUrl() {
    if (currentObjectUrl) {
      URL.revokeObjectURL(currentObjectUrl)
      currentObjectUrl = ''
    }
  }

  function reset() {
    textContent.value = ''
    error.value = ''
    fileBlob.value = null
    blobUrl.value = ''
    mediaUrl.value = ''
    revokeObjectUrl()
    imageUrl.value = ''
  }

  async function load(file: PreviewFile) {
    const token = ++loadToken
    loading.value = true
    reset()

    const type = getPreviewType(file.fileType ?? '', file.fileName)
    try {
      const res = await fetch(file.downloadPath)
      // 如果已经发起了更新的请求，丢弃本次结果
      if (token !== loadToken) return
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const blob = await res.blob()
      if (token !== loadToken) return

      if (type === 'image') {
        // SVG 文件服务端可能返回 application/octet-stream，需强制修正 MIME type
        const ext = getExt(file.fileName)
        const svgBlob =
          ext === 'svg' && blob.type !== 'image/svg+xml'
            ? new Blob([await blob.arrayBuffer()], { type: 'image/svg+xml' })
            : blob
        currentObjectUrl = URL.createObjectURL(svgBlob)
        imageUrl.value = currentObjectUrl
      } else if (
        type === 'audio' ||
        type === 'video' ||
        type === 'pdf' ||
        type === 'pptx' ||
        type === 'docx'
      ) {
        // 浏览器原生 <video>/<audio>/<iframe> 直接吃 ObjectURL
        currentObjectUrl = URL.createObjectURL(blob)
        mediaUrl.value = currentObjectUrl
        blobUrl.value = currentObjectUrl
        fileBlob.value = blob
      } else if (
        type === 'text' ||
        type === 'markdown' ||
        type === 'html' ||
        type === 'csv' ||
        type === 'mermaid'
      ) {
        textContent.value = await blob.text()
      } else {
        // unsupported: 保留 blob / url 用于「下载」按钮
        fileBlob.value = blob
      }
    } catch (e: unknown) {
      if (token !== loadToken) return
      error.value = e instanceof Error ? e.message : '加载失败'
    } finally {
      if (token === loadToken) loading.value = false
    }
  }

  onUnmounted(revokeObjectUrl)

  return { loading, error, textContent, imageUrl, mediaUrl, blobUrl, fileBlob, load, reset }
}