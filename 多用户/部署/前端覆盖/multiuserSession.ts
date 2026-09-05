export function multiuserRequestHeaders(): Record<string, string> {
  const value = document.cookie
    .split('; ')
    .find((item) => item.startsWith('__Host-xj-csrf='))
    ?.slice('__Host-xj-csrf='.length)
  return value ? { 'X-XJ-CSRF-Token': decodeURIComponent(value) } : {}
}
