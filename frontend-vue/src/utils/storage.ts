/**
 * 用户偏好持久化（localStorage）
 *
 * 为什么用 localStorage 而不是 cookie：
 * - 纯前端偏好场景无需发送到服务端，cookie 每次请求都会带，增加带宽
 * - 容量 5-10MB 远超单个字符串需求
 * - API 同步、简单；隐私模式或存储满时 try/catch 降级为内存态
 */

const SELECTED_DOMAIN_KEY = 'bkg:selected_domain'

export function getSelectedDomain(): string | null {
  try {
    return localStorage.getItem(SELECTED_DOMAIN_KEY)
  } catch {
    // 隐私模式 / 禁用 storage 时静默失败
    return null
  }
}

export function setSelectedDomain(domain: string): void {
  try {
    localStorage.setItem(SELECTED_DOMAIN_KEY, domain)
  } catch {
    // 存储满 / 隐私模式时静默失败
  }
}

export function clearSelectedDomain(): void {
  try {
    localStorage.removeItem(SELECTED_DOMAIN_KEY)
  } catch {
    /* noop */
  }
}
