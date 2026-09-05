type InstanceIdentity = {
  accountId?: unknown
  bootId?: unknown
  displayName?: unknown
  callName?: unknown
}

declare global {
  interface Window {
    __XJ_INSTANCE__?: InstanceIdentity
  }
}

function safeText(value: unknown, fallback: string): string {
  return typeof value === 'string' && value.trim() ? value.trim() : fallback
}

const raw = window.__XJ_INSTANCE__ ?? {}

export const instanceIdentity = Object.freeze({
  accountId: safeText(raw.accountId, ''),
  bootId: safeText(raw.bootId, ''),
  displayName: safeText(raw.displayName, '主人'),
  callName: safeText(raw.callName, '老板'),
})
