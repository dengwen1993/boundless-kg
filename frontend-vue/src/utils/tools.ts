/** Heuristic to decide whether a tool-result string indicates failure. */
export function isErrorResult(r?: string): boolean {
  if (!r) return false
  const trimmed = r.trim()
  if (trimmed.startsWith('{') || trimmed.startsWith('[')) {
    try {
      const obj = JSON.parse(trimmed)
      if (obj && typeof obj === 'object' && !Array.isArray(obj)) {
        if (obj.ok === false) return true
        if (typeof obj.error === 'string' && obj.error.length > 0) return true
        if (obj.stage === 'error') return true
        return false
      }
    } catch {
      /* fall through to string fallback */
    }
  }
  return (
    /(?:["']error["']\s*[:=]\s*["'][^"']+["'])/.test(r) ||
    /Traceback \(most recent call last\)/.test(r) ||
    r.includes('❌')
  )
}
