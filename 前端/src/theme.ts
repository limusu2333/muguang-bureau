export type VisualTheme = 'deep-space' | 'mist'

export const THEME_STORAGE_KEY = 'xj_visual_theme'

export function readVisualTheme(): VisualTheme {
  try {
    return localStorage.getItem(THEME_STORAGE_KEY) === 'mist' ? 'mist' : 'deep-space'
  } catch {
    return 'deep-space'
  }
}

export function saveVisualTheme(theme: VisualTheme) {
  try {
    localStorage.setItem(THEME_STORAGE_KEY, theme)
  } catch {
    /* 浏览器禁用本地存储时，主题仍可在本次打开期间切换。 */
  }
}
