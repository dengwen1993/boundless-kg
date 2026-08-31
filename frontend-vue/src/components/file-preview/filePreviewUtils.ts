/**
 * 文件预览公共工具：类型判断 + 图标 (Emoji) + 格式化
 *
 * 简化版：使用 emoji 作为图标，避免 PNG 资源管理；
 * 接入项目已有的 highlight.js / marked，不引入 @vue-office / markstream-vue。
 */

export type PreviewType =
  | 'image'
  | 'audio'
  | 'video'
  | 'text'
  | 'markdown'
  | 'html'
  | 'csv'
  | 'pdf'
  | 'pptx'
  | 'docx'
  | 'mermaid'
  | 'unsupported'

/** 与 FilePreviewDialog 中按 type 分发的组件 key 对齐 */
export const PREVIEW_TYPES: PreviewType[] = [
  'image',
  'audio',
  'video',
  'text',
  'markdown',
  'html',
  'csv',
  'pdf',
  'pptx',
  'docx',
  'mermaid',
  'unsupported',
]

export function getExt(fileName: string): string {
  const name = fileName.toLowerCase()
  const dot = name.lastIndexOf('.')
  return dot >= 0 ? name.slice(dot + 1) : ''
}

const IMAGE_EXTS = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp']
const AUDIO_EXTS = ['mp3', 'wav', 'ogg', 'flac', 'aac', 'm4a', 'opus']
const VIDEO_EXTS = ['mp4', 'webm', 'ogv', 'mov', 'avi', 'mkv', 'm4v']
const TEXT_EXTS = [
  'txt', 'log', 'json', 'yaml', 'yml', 'toml', 'xml', 'ini', 'env',
  'js', 'jsx', 'ts', 'tsx', 'mjs', 'cjs',
  'py', 'sh', 'bash', 'zsh',
  'java', 'kt', 'kts', 'scala',
  'go', 'rs',
  'c', 'h', 'cpp', 'cc', 'cxx', 'hpp',
  'cs', 'php', 'rb', 'swift', 'dart', 'r',
  'sql', 'graphql', 'gql',
  'css', 'scss', 'less', 'vue', 'svelte',
  'conf', 'cfg', 'properties',
]

/** 根据文件名 / mime 推断预览类型 */
export function getPreviewType(fileType: string, fileName: string): PreviewType {
  const ext = getExt(fileName)
  if (fileType?.startsWith('image/') || IMAGE_EXTS.includes(ext)) return 'image'
  if (fileType?.startsWith('audio/') || AUDIO_EXTS.includes(ext)) return 'audio'
  if (fileType?.startsWith('video/') || VIDEO_EXTS.includes(ext)) return 'video'
  if (ext === 'md' || ext === 'markdown') return 'markdown'
  if (ext === 'html' || ext === 'htm' || fileType === 'text/html') return 'html'
  if (ext === 'xlsx' || ext === 'xls' || ext === 'csv' || fileType === 'text/csv') {
    // CSV → 内置表格渲染；xlsx/xls 不内置预览（需要 SheetJS / @vue-office）
    return ext === 'csv' ? 'csv' : 'unsupported'
  }
  if (ext === 'pdf' || fileType === 'application/pdf') return 'pdf'
  if (ext === 'pptx' || ext === 'ppt') return 'pptx'
  if (ext === 'docx' || ext === 'doc') return 'docx'
  // Mermaid 思维导图 / 流程图等（.mmd / .mermaid）
  if (ext === 'mmd' || ext === 'mermaid') return 'mermaid'
  if (TEXT_EXTS.includes(ext)) return 'text'
  return 'unsupported'
}

/** 文件类型 → emoji 图标（用于卡片 / 预览头） */
export function getFileIcon(fileName: string): string {
  const ext = getExt(fileName)
  if (IMAGE_EXTS.includes(ext)) return '🖼️'
  if (AUDIO_EXTS.includes(ext)) return '🎵'
  if (VIDEO_EXTS.includes(ext)) return '🎬'
  if (ext === 'pdf') return '📕'
  if (ext === 'pptx' || ext === 'ppt') return '📽️'
  if (ext === 'xlsx' || ext === 'xls' || ext === 'csv') return '📊'
  if (ext === 'docx' || ext === 'doc') return '📄'
  if (ext === 'html' || ext === 'htm') return '🌐'
  if (ext === 'md' || ext === 'markdown') return '📝'
  if (ext === 'mmd' || ext === 'mermaid') return '🧠'
  if (ext === 'json' || ext === 'yaml' || ext === 'yml') return '⚙️'
  if (['js', 'ts', 'jsx', 'tsx', 'vue', 'py', 'go', 'rs', 'java', 'kt'].includes(ext)) return '💻'
  if (['zip', 'rar', '7z', 'tar', 'gz'].includes(ext)) return '🗜️'
  return '📄'
}

/** highlight.js 语言映射 */
export function getCodeLanguage(fileName: string): string {
  const ext = getExt(fileName)
  const map: Record<string, string> = {
    js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
    ts: 'typescript', tsx: 'typescript',
    py: 'python',
    java: 'java',
    kt: 'kotlin', kts: 'kotlin',
    scala: 'scala',
    go: 'go',
    rs: 'rust',
    c: 'c', h: 'c',
    cpp: 'cpp', cc: 'cpp', cxx: 'cpp', hpp: 'cpp',
    cs: 'csharp',
    php: 'php',
    rb: 'ruby',
    swift: 'swift',
    dart: 'dart',
    r: 'r',
    sql: 'sql',
    graphql: 'graphql', gql: 'graphql',
    css: 'css',
    scss: 'scss',
    less: 'less',
    vue: 'xml',
    svelte: 'xml',
    sh: 'bash', bash: 'bash', zsh: 'bash',
    json: 'json',
    yaml: 'yaml', yml: 'yaml',
    toml: 'ini',
    xml: 'xml',
    html: 'html', htm: 'html',
    ini: 'ini',
    conf: 'ini', cfg: 'ini', properties: 'ini',
  }
  return map[ext] ?? 'plaintext'
}

export function formatSize(bytes: number | undefined | null): string {
  if (bytes == null || bytes <= 0) return '--'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}