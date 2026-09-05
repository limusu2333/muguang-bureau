const form = document.querySelector('#auth-form')
const email = document.querySelector('#email')
const password = document.querySelector('#password')
const passwordLabel = document.querySelector('#password-label')
const submit = document.querySelector('#submit-button')
const message = document.querySelector('#form-message')
const title = document.querySelector('#auth-title')
const subtitle = document.querySelector('#auth-subtitle')
const toggle = document.querySelector('#password-toggle')
const brand = document.querySelector('#auth-brand')
const version = document.querySelector('#auth-version')

const hash = new URLSearchParams(window.location.hash.slice(1))
const inviteToken = hash.get('token') || ''
const activationMode = Boolean(inviteToken)

function showMessage(text, ok = false) {
  message.textContent = text
  message.classList.toggle('ok', ok)
}

async function request(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options.headers || {}) },
  })
  const payload = await response.json().catch(() => ({}))
  if (!response.ok) throw new Error(payload.detail || '暂时无法完成，请稍后再试')
  return payload
}

function showRuntimeIdentity(versionName) {
  const value = String(versionName || '').trim() || '版本暂时无法确认'
  version.textContent = value
  brand.setAttribute('aria-label', `开发公司 ${value}`)
}

async function loadRuntimeIdentity() {
  try {
    const identity = await request('/auth/runtime-context', { cache: 'no-store' })
    showRuntimeIdentity(identity.version_name)
  } catch (_error) {
    showRuntimeIdentity('')
  }
}

async function prepare() {
  await loadRuntimeIdentity()
  if (!activationMode) {
    const response = await fetch('/auth/me', { cache: 'no-store' }).catch(() => null)
    if (response?.ok) window.location.replace('/')
    return
  }

  title.textContent = '设置你的登录密码'
  subtitle.textContent = '完成后会直接进入你的独立公司'
  passwordLabel.textContent = '新密码'
  password.autocomplete = 'new-password'
  submit.querySelector('span').textContent = '完成并进入'
  try {
    const detail = await request(`/auth/invite?token=${encodeURIComponent(inviteToken)}`)
    email.value = detail.email || ''
    email.readOnly = true
  } catch (error) {
    showMessage(error.message)
    submit.disabled = true
    password.disabled = true
  }
}

toggle.addEventListener('click', () => {
  const visible = password.type === 'text'
  password.type = visible ? 'password' : 'text'
  toggle.setAttribute('aria-label', visible ? '显示密码' : '隐藏密码')
  toggle.title = visible ? '显示密码' : '隐藏密码'
})

form.addEventListener('submit', async (event) => {
  event.preventDefault()
  showMessage('')
  if (!form.reportValidity()) return
  submit.disabled = true
  try {
    const path = activationMode ? '/auth/activate' : '/auth/login'
    const body = activationMode
      ? { token: inviteToken, password: password.value }
      : { account: email.value, password: password.value }
    const result = await request(path, { method: 'POST', body: JSON.stringify(body) })
    showMessage('登录成功，正在进入…', true)
    window.setTimeout(() => window.location.replace(result.handoff_url || '/'), 260)
  } catch (error) {
    showMessage(error.message)
    submit.disabled = false
  }
})

prepare()
