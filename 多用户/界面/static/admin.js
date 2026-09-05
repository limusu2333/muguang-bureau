const embedded = new URLSearchParams(window.location.search).get('embedded') === '1'

if (embedded) {
  document.documentElement.classList.add('embedded')
  document.body.classList.add('embedded')
}

function tellParent(view) {
  if (embedded && window.parent !== window) window.parent.postMessage({ type: 'xj-admin-view', view }, '*')
}

const shell = document.querySelector('#admin-shell')
const gate = document.querySelector('#admin-gate')
const ownerForm = document.querySelector('#owner-form')
const ownerAccount = document.querySelector('#owner-account')
const ownerPassword = document.querySelector('#owner-password')
const ownerMessage = document.querySelector('#owner-message')
const ownerTitle = document.querySelector('#owner-title')
const ownerSubtitle = document.querySelector('#owner-subtitle')
const ownerSubmit = document.querySelector('#owner-submit')
const ownerPasswordToggle = document.querySelector('#owner-password-toggle')
const adminContextLabel = document.querySelector('#admin-context-label')
const ownerCompanyIdentity = document.querySelector('#owner-company-identity')
const ownerVersion = document.querySelector('#owner-version')
const list = document.querySelector('#account-list')
const empty = document.querySelector('#empty-state')
const search = document.querySelector('#search-input')
const accountDialog = document.querySelector('#account-dialog')
const accountForm = document.querySelector('#account-form')
const accountMessage = document.querySelector('#account-message')
const inviteDialog = document.querySelector('#invite-dialog')
const inviteValue = document.querySelector('#invite-value')
const inviteNote = document.querySelector('#invite-note')
const confirmDialog = document.querySelector('#confirm-dialog')
const confirmTitle = document.querySelector('#confirm-title')
const confirmCopy = document.querySelector('#confirm-copy')
const confirmInputWrap = document.querySelector('#confirm-input-wrap')
const confirmInputLabel = document.querySelector('#confirm-input-label')
const confirmInput = document.querySelector('#confirm-input')
const confirmMessage = document.querySelector('#confirm-message')
const confirmButton = document.querySelector('#confirm-button')
const releaseButton = document.querySelector('#release-button')
const restartSupervisorButton = document.querySelector('#restart-supervisor-button')
const supervisorRestartDialog = document.querySelector('#supervisor-restart-dialog')
const supervisorRestartForm = document.querySelector('#supervisor-restart-form')
const supervisorRestartProgress = document.querySelector('#supervisor-restart-progress')
const supervisorRestartStage = document.querySelector('#supervisor-restart-stage')
const supervisorRestartMessage = document.querySelector('#supervisor-restart-message')
const supervisorRestartError = document.querySelector('#supervisor-restart-error')
const supervisorRestartConfirm = document.querySelector('#supervisor-restart-confirm')
const releaseDialog = document.querySelector('#release-dialog')
const releaseForm = document.querySelector('#release-form')
const releaseKicker = document.querySelector('#release-kicker')
const releaseTitle = document.querySelector('#release-title')
const releaseVersion = document.querySelector('#release-version')
const releaseTargets = document.querySelector('#release-targets')
const releaseSources = document.querySelector('#release-sources')
const releaseDevelopmentSource = document.querySelector('#release-development-source')
const releaseSourceCommit = document.querySelector('#release-source-commit')
const releaseSourceTime = document.querySelector('#release-source-time')
const releaseSourceSummary = document.querySelector('#release-source-summary')
const releaseCurrent = document.querySelector('#release-current')
const releaseTrackCopy = document.querySelector('#release-track-copy')
const releaseBootstrap = document.querySelector('#release-bootstrap')
const releaseBootstrapKind = document.querySelector('#release-bootstrap-kind')
const releaseBootstrapCopy = document.querySelector('#release-bootstrap-copy')
const releaseHostApproval = document.querySelector('#release-host-approval')
const releaseHostApprovalCopy = document.querySelector('#release-host-approval-copy')
const releaseHostApprovalConfirm = document.querySelector('#release-host-approval-confirm')
const releaseCopy = document.querySelector('#release-copy')
const releaseNotesWrap = document.querySelector('#release-notes-wrap')
const releaseNotes = document.querySelector('#release-notes')
const releaseNameWrap = document.querySelector('#release-name-wrap')
const releaseName = document.querySelector('#release-name')
const releaseProgress = document.querySelector('#release-progress')
const releaseStage = document.querySelector('#release-stage')
const releaseCounts = document.querySelector('#release-counts')
const releaseOutcome = document.querySelector('#release-outcome')
const releaseOutcomeTitle = document.querySelector('#release-outcome-title')
const releaseAccountEffect = document.querySelector('#release-account-effect')
const releaseTimelineWrap = document.querySelector('#release-timeline-wrap')
const releaseTimelineCount = document.querySelector('#release-timeline-count')
const releaseTimeline = document.querySelector('#release-timeline')
const releaseFailure = document.querySelector('#release-failure')
const releaseFailurePhase = document.querySelector('#release-failure-phase')
const releaseFailureCode = document.querySelector('#release-failure-code')
const releaseFailureSummary = document.querySelector('#release-failure-summary')
const releaseFailedChecks = document.querySelector('#release-failed-checks')
const releaseNextAction = document.querySelector('#release-next-action')
const releaseLogDetails = document.querySelector('#release-log-details')
const releaseLogMeta = document.querySelector('#release-log-meta')
const releaseLogRefresh = document.querySelector('#release-log-refresh')
const releaseLogContent = document.querySelector('#release-log-content')
const releaseLogMessage = document.querySelector('#release-log-message')
const releaseMessage = document.querySelector('#release-message')
const releaseHistory = document.querySelector('#release-history')
const releaseHistoryCount = document.querySelector('#release-history-count')
const releaseHistoryList = document.querySelector('#release-history-list')
const releaseConfirm = document.querySelector('#release-confirm')
const releaseRollback = document.querySelector('#release-rollback')
const releaseRollbackVersion = document.querySelector('#release-rollback-version')
const releaseRollbackConfirm = document.querySelector('#release-rollback-confirm')
const toast = document.querySelector('#toast')
const accountsView = document.querySelector('#accounts-view')
const announcementsView = document.querySelector('#announcements-view')
const feedbackView = document.querySelector('#feedback-view')
const announcementList = document.querySelector('#announcement-list')
const announcementEmpty = document.querySelector('#announcement-empty')
const newAccountButton = document.querySelector('#new-account-button')
const newAnnouncementButton = document.querySelector('#new-announcement-button')
const announcementDialog = document.querySelector('#announcement-dialog')
const announcementForm = document.querySelector('#announcement-form')
const announcementTitle = document.querySelector('#announcement-title')
const announcementBody = document.querySelector('#announcement-body')
const announcementMessage = document.querySelector('#announcement-message')
const announcementConfirm = document.querySelector('#announcement-confirm')
const announcementToolbar = document.querySelector('#announcement-toolbar')
const announcementBlockStyle = document.querySelector('#announcement-block-style')
const announcementFont = document.querySelector('#announcement-font')
const announcementSize = document.querySelector('#announcement-size')
const announcementEmojiToggle = document.querySelector('#announcement-emoji-toggle')
const announcementEmojiPanel = document.querySelector('#announcement-emoji-panel')
const announcementCount = document.querySelector('#announcement-count')
const announcementPreviewTitle = document.querySelector('#announcement-preview-title')
const announcementPreviewBody = document.querySelector('#announcement-preview-body')
const announcementDialogKicker = document.querySelector('#announcement-dialog-kicker')
const announcementDialogTitle = document.querySelector('#announcement-dialog-title')
const feedbackList = document.querySelector('#feedback-list')
const feedbackEmpty = document.querySelector('#feedback-empty')
const feedbackBadge = document.querySelector('#feedback-badge')
const feedbackFilter = document.querySelector('#feedback-filter')
const feedbackDialog = document.querySelector('#feedback-dialog')
const feedbackForm = document.querySelector('#feedback-form')
const feedbackDetailKind = document.querySelector('#feedback-detail-kind')
const feedbackDetailTitle = document.querySelector('#feedback-detail-title')
const feedbackDetailUser = document.querySelector('#feedback-detail-user')
const feedbackDetailTime = document.querySelector('#feedback-detail-time')
const feedbackDetailVersion = document.querySelector('#feedback-detail-version')
const feedbackDetailPage = document.querySelector('#feedback-detail-page')
const feedbackDetailBody = document.querySelector('#feedback-detail-body')
const feedbackDetailImageWrap = document.querySelector('#feedback-detail-image-wrap')
const feedbackDetailImage = document.querySelector('#feedback-detail-image')
const feedbackDetailStatus = document.querySelector('#feedback-detail-status')
const feedbackMessage = document.querySelector('#feedback-message')
const feedbackSave = document.querySelector('#feedback-save')

let setupMode = false
let accounts = []
let announcements = []
let feedback = []
let selectedFeedback = null
let pendingAction = null
let releaseStatus = null
let runtimeContext = null
let releasePollTimer = null
let watchedRelease = ''
let releaseLogCandidateId = ''
let releaseLogLoadedFor = ''
let releaseLogComplete = false
let releaseLogLoading = false
let releaseHostApprovalRequired = null
let supervisorRestartStatus = null
let supervisorRestartPollTimer = null
let watchedSupervisorRestart = ''
let announcementSelection = null
let editingAnnouncementId = null

const runningReleaseStates = new Set(['排队中', '验收中', '构建中', '更新账号中'])
const runningSupervisorRestartStates = new Set(['排队中', '重启中'])

function isDevelopmentAdmin() {
  return runtimeContext?.mode === 'development'
}

function showRuntimeIdentity(identity) {
  const versionName = String(identity?.display_name || identity?.version_name || '').trim() || '版本暂时无法确认'
  ownerVersion.textContent = versionName
  ownerCompanyIdentity.setAttribute('aria-label', `开发公司 ${versionName}`)
}

function applyRuntimeContext() {
  const development = isDevelopmentAdmin()
  showRuntimeIdentity(runtimeContext?.version_identity)
  adminContextLabel.textContent = development ? '开发管理' : '正式运行维护'
  releaseKicker.textContent = development ? '开发版发布' : '正式运行维护'
  releaseTitle.textContent = development ? '版本发布' : '版本维护'
  releaseDevelopmentSource.hidden = !development
  releaseConfirm.hidden = !development
  releaseNotesWrap.hidden = !development
  releaseNameWrap.hidden = !development
  releaseHostApproval.hidden = !development
  updateReleaseButton()
}

async function loadRuntimeContext() {
  runtimeContext = await api('/admin/runtime-context')
  applyRuntimeContext()
  return runtimeContext
}

async function loadPublicRuntimeIdentity() {
  try {
    const identity = await api('/admin/runtime-identity')
    showRuntimeIdentity(identity)
  } catch (_error) {
    showRuntimeIdentity(null)
  }
}

function cookie(name) {
  return document.cookie.split('; ').find((item) => item.startsWith(`${name}=`))?.slice(name.length + 1) || ''
}

async function api(url, options = {}) {
  const response = await fetch(url, {
    ...options,
    cache: 'no-store',
    headers: {
      'Content-Type': 'application/json',
      ...(options.method && options.method !== 'GET' ? { 'X-XJ-CSRF-Token': decodeURIComponent(cookie('xj-owner-csrf')) } : {}),
      ...(options.headers || {}),
    },
  })
  const body = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail = body.detail
    const error = new Error(
      typeof detail === 'string' ? detail : detail?.message || '操作没有完成',
    )
    if (detail && typeof detail === 'object') Object.assign(error, detail)
    throw error
  }
  return body
}

function notify(text) {
  toast.textContent = text
  toast.classList.add('show')
  window.setTimeout(() => toast.classList.remove('show'), 2200)
}

function supervisorRestartIsRunning(status = supervisorRestartStatus) {
  return Boolean(status && runningSupervisorRestartStates.has(status.status))
}

function supervisorRestartElapsed(status) {
  if (!status) return 0
  const started = Number(status.started_at || status.requested_at || 0)
  if (!started) return 0
  const ended = Number(status.completed_at || 0) || Date.now() / 1000
  return Math.max(0, Math.round(ended - started))
}

function renderSupervisorRestart() {
  const status = supervisorRestartStatus
  const running = supervisorRestartIsRunning(status)
  const hasStatus = Boolean(status)
  restartSupervisorButton.classList.toggle('running', running)
  restartSupervisorButton.textContent = running ? '后台重启中' : '重启后台'
  supervisorRestartProgress.hidden = !hasStatus
  supervisorRestartConfirm.disabled = running
  supervisorRestartConfirm.textContent = running ? '正在重启' : status?.status === '完成' ? '再次重启' : '确认重启'
  supervisorRestartStage.textContent = status?.stage || '准备重启'
  const elapsed = supervisorRestartElapsed(status)
  supervisorRestartMessage.textContent = status
    ? `${status.message || ''}${elapsed ? ` · 已用 ${elapsed} 秒` : ''}`
    : '只重新加载后台进程，不修改账号、版本或发布记录。'
  supervisorRestartError.textContent = status?.status === '失败' ? (status.error || status.message || '后台重启失败') : ''
}

async function loadSupervisorRestartStatus({ poll = false } = {}) {
  try {
    const body = await api('/admin/system-restart')
    supervisorRestartStatus = body.restart || null
    renderSupervisorRestart()
    if (supervisorRestartIsRunning()) {
      window.clearTimeout(supervisorRestartPollTimer)
      supervisorRestartPollTimer = window.setTimeout(() => loadSupervisorRestartStatus({ poll: true }).catch(() => {}), 1200)
    } else {
      window.clearTimeout(supervisorRestartPollTimer)
      supervisorRestartPollTimer = null
      if (poll && watchedSupervisorRestart && supervisorRestartStatus?.restart_id === watchedSupervisorRestart) {
        notify(supervisorRestartStatus.status === '完成' ? '多用户后台已恢复' : (supervisorRestartStatus.stage || '后台重启结束'))
        watchedSupervisorRestart = ''
      }
    }
  } catch (error) {
    if (supervisorRestartIsRunning()) {
      supervisorRestartProgress.hidden = false
      supervisorRestartStage.textContent = '正在重新连接后台'
      supervisorRestartMessage.textContent = '后台正在重启，页面会自动继续检查…'
      window.clearTimeout(supervisorRestartPollTimer)
      supervisorRestartPollTimer = window.setTimeout(() => loadSupervisorRestartStatus({ poll: true }).catch(() => {}), 1200)
      return
    }
    supervisorRestartError.textContent = error.message
  }
}

function statusLabel(status) {
  return status === '待绑定' ? ['待激活', 'pending'] : status === '在用' ? ['使用中', 'active'] : ['已停用', 'stopped']
}

function dateText(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '—' : date.toLocaleDateString('zh-CN')
}

function dateTimeText(value) {
  if (!value) return '—'
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? '—' : date.toLocaleString('zh-CN', { hour12: false })
}

function versionText(imageRef) {
  const value = String(imageRef || '')
  const tag = value.includes(':') ? value.slice(value.lastIndexOf(':') + 1) : ''
  if (!tag || tag === 'dev' || tag.startsWith('sha256')) return '开发版（尚未发布）'
  return releaseVersionText(tag)
}

function releaseVersionText(version) {
  if (!version || version === 'dev') return '开发版'
  const known = (releaseStatus?.versions || []).find((item) => item.version === version)
  return known?.version_name || version
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"]/g, (char) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[char])
}

function announcementPlainText() {
  return announcementBody.innerText
    .replace(/\u00a0/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}

function updateAnnouncementPreview() {
  const plain = announcementPlainText()
  announcementCount.textContent = `${plain.length} / 8000`
  announcementCount.classList.toggle('over-limit', plain.length > 8000)
  announcementPreviewTitle.textContent = announcementTitle.value.trim() || '公告标题'
  if (plain) {
    announcementPreviewBody.classList.remove('is-empty')
    announcementPreviewBody.innerHTML = announcementBody.innerHTML
  } else {
    announcementPreviewBody.classList.add('is-empty')
    announcementPreviewBody.textContent = '正文会在这里实时显示'
  }
}

function rememberAnnouncementSelection() {
  const selection = window.getSelection()
  if (!selection?.rangeCount || !announcementBody.contains(selection.anchorNode)) return
  announcementSelection = selection.getRangeAt(0).cloneRange()
}

function restoreAnnouncementSelection() {
  announcementBody.focus()
  if (!announcementSelection) return
  const selection = window.getSelection()
  selection.removeAllRanges()
  selection.addRange(announcementSelection)
}

function runAnnouncementCommand(command, value = null) {
  restoreAnnouncementSelection()
  document.execCommand('styleWithCSS', false, true)
  document.execCommand(command, false, value)
  rememberAnnouncementSelection()
  updateAnnouncementPreview()
}

function render() {
  const query = search.value.trim().toLowerCase()
  const rows = accounts.filter((account) => `${account.display_name} ${account.email} ${account.account_id}`.toLowerCase().includes(query))
  list.innerHTML = rows.map((account) => {
    const [label, className] = statusLabel(account.状态)
    const primary = account.状态 === '在用'
      ? `<button data-action="stop" data-id="${account.account_id}">停用账户</button>`
      : account.状态 === '停用'
        ? `<button data-action="restart" data-id="${account.account_id}">恢复账户</button>`
        : `<button data-action="invite" data-id="${account.account_id}">重新生成邀请</button>`
    const reset = account.状态 === '待绑定' ? '' : `<button data-action="reset" data-id="${account.account_id}">重置密码</button>`
    return `<tr>
      <td><div class="user-cell"><strong>${escapeHtml(account.display_name || account.call_name || '未命名')}</strong><span>${escapeHtml(account.email)}</span><code>编号 ${escapeHtml(account.account_id)}</code><code>版本 ${escapeHtml(versionText(account.image_ref))}</code></div></td>
      <td><span class="status-tag ${className}">${label}</span></td>
      <td>${Number(account.登录?.登录设备数 || 0)}</td>
      <td>${dateText(account.created_at)}</td>
      <td><details class="row-menu"><summary title="更多操作" aria-label="更多操作">⋯</summary><div>${primary}${reset}<button class="danger" data-action="purge" data-id="${account.account_id}">永久删除</button></div></details></td>
    </tr>`
  }).join('')
  empty.hidden = rows.length > 0
  document.querySelector('#count-all').textContent = accounts.length
  document.querySelector('#count-active').textContent = accounts.filter((item) => item.状态 === '在用').length
  document.querySelector('#count-stopped').textContent = accounts.filter((item) => item.状态 === '停用').length
}

function renderAnnouncements() {
  announcementList.innerHTML = announcements.map((announcement) => {
    const active = announcement.status === '已发布'
    return `<article class="announcement-item">
      <div class="announcement-item-copy">
        <div><span class="status-tag ${active ? 'active' : 'stopped'}">${escapeHtml(announcement.status)}</span><time>${escapeHtml(dateTimeText(announcement.published_at))}</time></div>
        <h2>${escapeHtml(announcement.title)}</h2>
        <p>${escapeHtml(announcement.body)}</p>
      </div>
      <details class="row-menu"><summary title="更多操作" aria-label="更多操作">⋯</summary><div>
        <button data-announcement-action="edit" data-id="${announcement.announcement_id}">编辑公告</button>
        <button data-announcement-action="${active ? 'withdraw' : 'publish'}" data-id="${announcement.announcement_id}">${active ? '撤下公告' : '重新发布'}</button>
      </div></details>
    </article>`
  }).join('')
  announcementEmpty.hidden = announcements.length > 0
}

function feedbackStatus(status) {
  if (status === '待处理') return ['待处理', 'pending']
  if (status === '处理中') return ['处理中', '']
  if (status === '已解决') return ['已解决', 'active']
  return ['不处理', 'stopped']
}

function renderFeedback() {
  const filter = feedbackFilter.value
  const rows = feedback.filter((item) => !filter || item.status === filter)
  feedbackList.innerHTML = rows.map((item) => {
    const [label, className] = feedbackStatus(item.status)
    return `<tr>
      <td><span class="status-tag ${className}">${escapeHtml(label)}</span></td>
      <td><div class="feedback-user"><strong>${escapeHtml(item.display_name || item.call_name || '未命名用户')}</strong><span>${escapeHtml(item.email)}</span></div></td>
      <td><span class="feedback-kind-tag">${escapeHtml(item.kind)}</span></td>
      <td><button class="feedback-preview" type="button" data-feedback-id="${item.feedback_id}">${escapeHtml(item.body)}</button></td>
      <td>${escapeHtml(dateTimeText(item.created_at))}</td>
      <td><button class="feedback-open" type="button" data-feedback-id="${item.feedback_id}">查看</button></td>
    </tr>`
  }).join('')
  feedbackEmpty.hidden = rows.length > 0
  const pending = feedback.filter((item) => item.status === '待处理').length
  feedbackBadge.textContent = pending
  feedbackBadge.hidden = pending === 0
}

async function loadAccounts() {
  const body = await api('/admin/accounts')
  accounts = body.accounts || []
  render()
}

async function loadAnnouncements() {
  const body = await api('/admin/announcements')
  announcements = body.announcements || []
  renderAnnouncements()
}

async function loadFeedback() {
  const body = await api('/admin/feedback')
  feedback = body.feedback || []
  renderFeedback()
}

function releaseIsRunning(record) {
  return Boolean(record && runningReleaseStates.has(record.status))
}

function renderReleaseSource(source) {
  const commit = source?.source
  releaseSources.hidden = !commit
  releaseSourceCommit.textContent = commit?.short_head || '—'
  releaseSourceTime.textContent = dateTimeText(commit?.committed_at)
  releaseSourceSummary.textContent = commit?.summary || '—'
  releaseCurrent.textContent = releaseVersionText(releaseStatus?.current_version)
}

function renderReleaseTrack(operation) {
  const formalVersion = releaseStatus?.product_version_name || releaseVersionText(releaseStatus?.current_version)
  releaseTrackCopy.textContent = isDevelopmentAdmin()
    ? `开发版仅供管理员验收与生成候选；分发账号继续运行${formalVersion && formalVersion !== '开发版' ? formalVersion : '已发布正式版'}。`
    : `正式用户继续运行${formalVersion && formalVersion !== '开发版' ? formalVersion : '当前已发布版本'}；此处只做版本查看、回退和后台维护。`

  // 首轮宿主引导是首次正式发布的一部分；普通发布的部署记录没有 bootstrap，必须保持隐藏。
  const deployment = releaseStatus?.latest_deployment
  const details = deployment?.details && typeof deployment.details === 'object' ? deployment.details : null
  const bootstrap = details?.bootstrap && typeof details.bootstrap === 'object' ? details.bootstrap : null
  const visible = Boolean(bootstrap && bootstrap.required !== false && operation?.type !== 'candidate')
  releaseBootstrap.hidden = !visible
  if (!visible) {
    releaseBootstrapKind.textContent = ''
    releaseBootstrapCopy.textContent = ''
    return
  }

  const kindLabels = {
    'first-three-role-host': '一次性三角色宿主切换',
    'first-host-bootstrap': '一次性宿主引导',
  }
  const kind = String(bootstrap.kind || '').trim()
  const mode = String(details?.host_switch?.mode || bootstrap.host_switch?.mode || '').trim()
  const fromVersion = String(bootstrap.from_legacy_version || '').trim()
  const targetVersion = String(bootstrap.target_version || '').trim()
  releaseBootstrapKind.textContent = kindLabels[kind] || '一次性首轮阶段'

  const lines = []
  const status = String(bootstrap.status || '').trim()
  if (status) lines.push(status)
  if (fromVersion || targetVersion) {
    const from = fromVersion ? releaseVersionText(fromVersion) : '旧运行版本'
    const target = targetVersion ? releaseVersionText(targetVersion) : '本次正式版本'
    lines.push(`从${from}切换到${target}。`)
  }
  if (mode === 'legacy-migration') lines.push('这是旧单进程到正式宿主的首轮切换；不会把开发版直接分发给账号。')
  releaseBootstrapCopy.textContent = lines.join(' ') || '首次宿主引导已纳入本次正式发布，完成后才会进入正常发布流程。'
}

function releaseOperation() {
  const deployment = releaseStatus?.latest_deployment
  const candidate = releaseStatus?.latest_candidate
  if (releaseIsRunning(deployment)) return { type: 'deployment', record: deployment }
  if (releaseIsRunning(candidate)) return { type: 'candidate', record: candidate }
  if (!deployment) return candidate ? { type: 'candidate', record: candidate } : null
  if (!candidate) return { type: 'deployment', record: deployment }
  return String(deployment.updated_at || '') >= String(candidate.updated_at || '')
    ? { type: 'deployment', record: deployment }
    : { type: 'candidate', record: candidate }
}

function releaseCandidateSourceHead(candidate) {
  const details = candidate?.details
  return String(details?.source?.head || details?.manifest?.source?.head || '').trim()
}

function releaseCandidateIsStale(candidate, source) {
  if (candidate?.status !== '待发布') return false
  const candidateHead = releaseCandidateSourceHead(candidate)
  const currentHead = String(source?.source?.head || '').trim()
  return Boolean(candidateHead && currentHead && candidateHead !== currentHead)
}

function operationKey(operation) {
  if (!operation) return ''
  return operation.record.deployment_id || operation.record.candidate_id || ''
}

function releaseOperationLabel(operation) {
  if (!operation) return '发布'
  if (operation.type === 'candidate') return '候选'
  return operation.record.kind === '回退' ? '回退' : '发布'
}

function releaseElapsed(record) {
  if (!record?.created_at) return ''
  const started = new Date(record.created_at).valueOf()
  const ended = new Date(record.completed_at || record.updated_at || Date.now()).valueOf()
  if (!Number.isFinite(started) || !Number.isFinite(ended) || ended < started) return ''
  const seconds = Math.max(0, Math.round((ended - started) / 1000))
  if (seconds < 60) return `${seconds} 秒`
  const minutes = Math.floor(seconds / 60)
  return `${minutes} 分 ${seconds % 60} 秒`
}

function compactTimeline(value) {
  if (!Array.isArray(value)) return []
  const result = []
  value.forEach((item) => {
    if (!item || typeof item !== 'object') return
    const stage = String(item.stage || '').trim()
    const status = String(item.status || '').trim()
    if (!stage && !status) return
    const entry = { at: item.at, status, stage: stage || status }
    const previous = result.at(-1)
    if (previous && previous.stage === entry.stage && previous.status === entry.status) result[result.length - 1] = entry
    else result.push(entry)
  })
  return result
}

function timelineTone(status) {
  if (['失败', '错误', '已中断'].some((value) => status.includes(value))) return 'failed'
  if (['完成', '通过', '待发布', '已封存', '成功'].some((value) => status.includes(value))) return 'done'
  return 'running'
}

function renderReleaseTimeline(latest) {
  const timeline = compactTimeline(latest?.details?.timeline)
  releaseTimelineWrap.hidden = timeline.length === 0
  releaseTimelineCount.textContent = timeline.length ? `${timeline.length} 个阶段记录` : ''
  releaseTimeline.replaceChildren(...timeline.map((item) => {
    const row = document.createElement('li')
    row.className = timelineTone(item.status)
    const marker = document.createElement('span')
    marker.className = 'release-timeline-marker'
    marker.setAttribute('aria-hidden', 'true')
    const copy = document.createElement('div')
    const title = document.createElement('strong')
    title.textContent = item.stage
    const meta = document.createElement('small')
    meta.textContent = [item.status, dateTimeText(item.at)].filter((value) => value && value !== '—').join(' · ')
    copy.append(title, meta)
    row.append(marker, copy)
    return row
  }))
}

function failedCheckText(item) {
  if (typeof item === 'string') return item
  if (!item || typeof item !== 'object') return ''
  return String(item.name || item.check || item.summary || '').trim()
}

function retryClassText(value) {
  const retryClass = String(value || '').trim()
  if (!retryClass) return ''
  const labels = {
    retryable: '可以重新尝试',
    retry: '可以重新尝试',
    transient: '环境恢复后可重新尝试',
    new_candidate: '需要生成新候选',
    'new-candidate': '需要生成新候选',
    not_retryable: '不能直接重试',
    'not-retryable': '不能直接重试',
    blocked: '已阻止继续发布',
    manual: '需要人工处理',
    environment: '修复本机环境后重新生成候选',
    'source-fix': '修正代码并提交后重新生成候选',
  }
  return labels[retryClass] || retryClass
}

function failurePhaseText(value) {
  const phase = String(value || '').trim()
  const labels = {
    environment: '发布环境预检',
    acceptance: '代码验收',
    'image-build': '镜像构建',
    'image-smoke': '镜像启动检查',
    'image-archive': '镜像封存',
    'release-system': '发布系统',
    delivery: '交付物校验',
    platform: '共享平台切换',
    account: '账号更新',
    activation: '版本生效',
    rollback: '自动回退',
  }
  return labels[phase] || phase || '本次操作'
}

function renderReleaseFailure(latest) {
  const failure = latest?.details?.failure
  const visible = latest?.status === '失败' || Boolean(failure)
  releaseFailure.hidden = !visible
  if (!visible) {
    releaseFailureSummary.textContent = ''
    releaseFailedChecks.replaceChildren()
    releaseNextAction.hidden = true
    return
  }
  const checks = Array.isArray(failure?.failed_checks)
    ? failure.failed_checks.map(failedCheckText).filter(Boolean)
    : []
  releaseFailurePhase.textContent = `${failurePhaseText(failure?.phase)}未通过`
  const rawExitCode = failure?.exit_code
  const exitCode = rawExitCode === null || rawExitCode === undefined || rawExitCode === ''
    ? Number.NaN
    : Number(rawExitCode)
  releaseFailureCode.textContent = [
    Number.isFinite(exitCode) ? `退出码 ${exitCode}` : '',
    retryClassText(failure?.retry_class),
  ].filter(Boolean).join(' · ')
  releaseFailureSummary.textContent = failure?.summary || latest?.details?.error || latest?.stage || '本次操作没有完成。'
  releaseFailedChecks.replaceChildren(...checks.map((text) => {
    const item = document.createElement('li')
    item.textContent = text
    return item
  }))
  releaseFailedChecks.hidden = checks.length === 0
  const nextAction = String(failure?.next_action || '').trim()
  releaseNextAction.hidden = !nextAction
  releaseNextAction.querySelector('p').textContent = nextAction
}

function defaultAccountEffect(operation, latest) {
  const current = releaseStatus?.product_version_name || releaseVersionText(releaseStatus?.current_version)
  if (!operation || operation.type === 'candidate') return `候选阶段不会更新账号；用户继续运行${current || '当前版本'}。`
  if (latest?.status === '完成') return `本次${releaseOperationLabel(operation)}已经完成，账号状态以当前运行版本为准。`
  if (latest?.status === '失败') return '发布没有完成；账号是否已全部退回，以失败说明和阶段记录为准。'
  return `正在处理 ${Number(latest?.target_count || 0)} 个账号，尚未完成版本切换。`
}

function renderReleaseOutcome(operation, latest, candidateStale = false) {
  if (!latest) {
    releaseOutcome.hidden = true
    return
  }
  const failure = latest.details?.failure
  const label = releaseOperationLabel(operation)
  let title = `${label}状态：${latest.status || latest.stage || '等待开始'}`
  if (failure?.summary) title = failure.summary
  else if (latest.status === '失败') title = `${label}未通过：${latest.stage}`
  else if (latest.status === '待发布' && candidateStale) title = '旧候选已过期，需要重新生成。'
  else if (latest.status === '待发布') title = '候选已通过完整验收，等待您命名并确认发布。'
  else if (latest.status === '完成') title = `${label}已经完成。`
  else if (releaseIsRunning(latest)) title = `${latest.stage || `${label}正在处理`}。`
  releaseOutcome.hidden = false
  releaseOutcome.className = `release-outcome ${latest.status === '失败' ? 'failed' : latest.status === '完成' || latest.status === '待发布' || latest.status === '已封存' ? 'done' : 'running'}`
  releaseOutcomeTitle.textContent = title
  releaseAccountEffect.textContent = failure?.account_effect || defaultAccountEffect(operation, latest)
}

function releaseHistoryItems() {
  const candidates = (releaseStatus?.candidates || []).map((record) => ({ type: '候选', record }))
  const deployments = (releaseStatus?.deployments || []).map((record) => ({ type: record.kind === '回退' ? '回退' : '发布', record }))
  return [...candidates, ...deployments]
    .sort((a, b) => String(b.record.updated_at || b.record.created_at || '').localeCompare(String(a.record.updated_at || a.record.created_at || '')))
    .slice(0, 8)
}

function renderReleaseHistory() {
  const items = releaseHistoryItems()
  releaseHistory.hidden = items.length === 0
  releaseHistoryCount.textContent = items.length ? `显示最近 ${items.length} 次` : ''
  releaseHistoryList.replaceChildren(...items.map(({ type, record }) => {
    const row = document.createElement('li')
    const top = document.createElement('div')
    const label = document.createElement('strong')
    const operationId = String(record.candidate_id || record.deployment_id || '')
    label.textContent = `${type}${operationId ? ` ${operationId.slice(-8)}` : ''}`
    const stale = record.candidate_id
      && record.candidate_id === releaseStatus?.latest_candidate?.candidate_id
      && releaseCandidateIsStale(record, releaseStatus.source)
    const status = document.createElement('span')
    status.className = `release-history-status ${timelineTone(String(record.status || ''))}`
    status.textContent = stale ? '已过期' : record.status || '未知'
    const time = document.createElement('time')
    time.textContent = dateTimeText(record.updated_at || record.created_at)
    top.append(label, status, time)
    const stage = document.createElement('p')
    stage.textContent = record.stage || '没有阶段说明'
    row.append(top, stage)
    return row
  }))
}

function resetReleaseLog(candidateId) {
  if (candidateId === releaseLogCandidateId) return
  releaseLogCandidateId = candidateId
  releaseLogLoadedFor = ''
  releaseLogComplete = false
  releaseLogLoading = false
  releaseLogDetails.open = false
  releaseLogMeta.textContent = '展开后加载'
  releaseLogContent.textContent = '尚未加载'
  releaseLogMessage.textContent = ''
}

async function loadReleaseLog({ force = false } = {}) {
  const candidateId = releaseLogCandidateId
  if (!candidateId || releaseLogLoading || (!force && releaseLogLoadedFor === candidateId && releaseLogComplete)) return
  releaseLogLoading = true
  releaseLogRefresh.disabled = true
  releaseLogMeta.textContent = '正在加载日志…'
  releaseLogMessage.textContent = ''
  try {
    const body = await api(`/admin/release-candidates/${encodeURIComponent(candidateId)}/log`)
    if (candidateId !== releaseLogCandidateId) return
    releaseLogLoadedFor = candidateId
    releaseLogComplete = Boolean(body.complete)
    releaseLogContent.textContent = body.log || '当前暂无日志。'
    const checksum = String(body.sha256 || '')
    releaseLogMeta.textContent = `${releaseLogComplete ? '日志完整' : '日志仍在写入'}${checksum ? ` · SHA-256 ${checksum.slice(0, 12)}…` : ''}`
  } catch (error) {
    if (candidateId === releaseLogCandidateId) {
      releaseLogMeta.textContent = '日志没有加载'
      releaseLogMessage.textContent = error.message
    }
  } finally {
    releaseLogLoading = false
    releaseLogRefresh.disabled = false
  }
}

function updateReleaseButton() {
  const running = releaseIsRunning(releaseOperation()?.record)
  releaseButton.classList.toggle('running', running)
  releaseButton.textContent = running ? '正在处理' : (isDevelopmentAdmin() ? '版本发布' : '版本维护')
}

function renderReleaseModal() {
  if (!releaseStatus) return
  const development = isDevelopmentAdmin()
  const operation = releaseOperation()
  const latest = operation?.record
  const running = releaseIsRunning(latest)
  const candidate = releaseStatus.latest_candidate
  const candidateStale = releaseCandidateIsStale(candidate, releaseStatus.source)
  const candidateReady = candidate?.status === '待发布' && !candidateStale
  const developmentAdmin = releaseStatus.development_admin || {}
  const reloadRequired = development && Boolean(developmentAdmin.reload_required)
  if (candidateStale && !running) releaseName.value = ''
  const candidateHostRuntime = candidate?.details?.host_runtime
  const approvalDetails = releaseHostApprovalRequired
    || (candidateReady && releaseStatus.host_runtime?.requires_approval
      ? { reason: releaseStatus.host_runtime.reason, candidate_id: candidate?.candidate_id }
      : null)
  const approvalVisible = Boolean(
    candidateReady
    && approvalDetails
    && !candidateHostRuntime?.status
    && !running,
  )
  releaseHostApproval.hidden = !development || !approvalVisible
  releaseHostApprovalCopy.textContent = approvalVisible
    ? `当前宿主依赖环境没有通过核对：${approvalDetails.reason || '需要先建立候选隔离环境'}。批准后只会创建并验收本次候选环境，不会替换正在运行的正式环境。`
    : ''
  releaseHostApprovalConfirm.disabled = !approvalVisible
  releaseVersion.textContent = development
    ? (candidateReady ? '待命名正式版' : '开发版（发布时命名）')
    : (releaseStatus.product_version_name || releaseVersionText(releaseStatus.current_version))
  releaseTargets.textContent = operation?.type === 'deployment' ? latest.target_count : releaseStatus.active_accounts
  renderReleaseSource(releaseStatus.source)
  releaseNotesWrap.hidden = !development || running || candidateReady
  releaseNameWrap.hidden = !development || !candidateReady || running
  releaseName.required = development && candidateReady && !running && !reloadRequired
  releaseProgress.hidden = !running
  releaseConfirm.hidden = !development || running
  releaseConfirm.textContent = reloadRequired ? '重启后台' : candidateReady ? '确认发布' : '生成候选'
  releaseConfirm.disabled = !development || (!running && !reloadRequired && (candidateReady
    ? !releaseName.value.trim() || approvalVisible
    : !releaseStatus.source?.ready))
  releaseMessage.textContent = ''
  renderReleaseTrack(operation)
  renderReleaseOutcome(operation, latest, candidateStale)
  renderReleaseTimeline(latest)
  renderReleaseFailure(latest)
  renderReleaseHistory()
  const candidateId = operation?.type === 'candidate'
    ? String(latest?.candidate_id || '')
    : operation?.record?.kind === '发布'
      ? String(operation.record.details?.candidate_id || '')
      : ''
  const logRecord = (releaseStatus.candidates || []).find((item) => item.candidate_id === candidateId)
  const hasReleaseLog = logRecord?.details?.manifest?.acceptance?.log?.file === 'release.log'
  resetReleaseLog(candidateId)
  releaseLogDetails.hidden = !candidateId || !hasReleaseLog
  const candidateRunning = operation?.type === 'candidate' && running
  if (releaseLogDetails.open && candidateId && (!releaseLogComplete || candidateRunning)) loadReleaseLog({ force: true }).catch(() => {})

  const rollbackVersions = (releaseStatus.versions || []).filter((item) => item.version !== releaseStatus.current_version)
  releaseRollback.hidden = running || rollbackVersions.length === 0
  releaseRollbackVersion.replaceChildren(...rollbackVersions.map((item) => {
    const option = document.createElement('option')
    option.value = item.version
    option.textContent = item.version_name || releaseVersionText(item.version)
    return option
  }))

  if (running) {
    releaseStage.textContent = latest.stage
    const elapsed = releaseElapsed(latest)
    releaseCounts.textContent = operation.type === 'deployment' && latest.target_count
      ? `已完成 ${latest.success_count} · 失败 ${latest.failed_count} · 共 ${latest.target_count}${elapsed ? ` · 已用 ${elapsed}` : ''}`
      : `正在验收候选${elapsed ? ` · 已用 ${elapsed}` : ''}`
    releaseCopy.textContent = latest.notes || candidate?.notes || '正在处理'
    return
  }
  if (reloadRequired) {
    releaseCopy.textContent = '后台还在运行上一份代码。点“重启后台”会同时重新加载正式用户后台和发布后台；两边恢复前不会允许发布。'
    return
  }
  if (latest?.status === '失败') {
    releaseCopy.textContent = latest.stage
    return
  }
  if (!development) {
    releaseCopy.textContent = '正式用户继续运行当前已发布版本；这里仅提供版本查看、回退和后台维护。'
    return
  }
  if (candidateStale) {
    releaseCopy.textContent = '开发版已经更新，旧候选已过期；请重新生成候选。'
    return
  }
  if (releaseStatus.current_version && !candidateReady) {
    releaseCopy.textContent = `${releaseStatus.product_version_name}正在运行；新内容仍在唯一开发版中。`
    return
  }
  if (candidateReady) {
    releaseCopy.textContent = '候选已通过完整验收。填写正式版本名称后，确认才会更新账号。'
    return
  }
  if (!releaseStatus.source?.ready) {
    releaseCopy.textContent = `当前不能生成候选：${releaseStatus.source?.reason || '无法确认发布来源'}。`
    return
  }
  releaseCopy.textContent = '将从唯一开发版生成一份独立候选；开发版没有正式版本号。'
}

async function loadReleaseStatus({ poll = false } = {}) {
  const previous = releaseOperation()
  releaseStatus = await api('/admin/releases')
  if (accounts.length) render()
  const latest = releaseOperation()
  updateReleaseButton()
  if (releaseDialog.open) renderReleaseModal()

  if (releaseIsRunning(latest?.record)) {
    watchedRelease = operationKey(latest)
    scheduleReleasePoll()
  } else {
    window.clearTimeout(releasePollTimer)
    releasePollTimer = null
    if (poll && watchedRelease && operationKey(latest) === watchedRelease) {
      notify(latest?.record?.status === '完成' ? `${releaseStatus.product_version_name}已发布` : latest?.record?.stage)
      watchedRelease = ''
      await loadAccounts()
    } else if (releaseIsRunning(previous?.record)) {
      await loadAccounts()
    }
  }
  return releaseStatus
}

function scheduleReleasePoll(delay = 1800) {
  window.clearTimeout(releasePollTimer)
  releasePollTimer = window.setTimeout(async () => {
    try {
      await loadReleaseStatus({ poll: true })
    } catch (_error) {
      if (watchedRelease) scheduleReleasePoll(3000)
    }
  }, delay)
}

function showInvite(result, reset = false) {
  document.querySelector('#invite-title').textContent = reset ? '密码重置链接已生成' : '邀请已经生成'
  inviteValue.value = result.invite_url || result.invite_path || result.invite_token
  inviteNote.textContent = result.invite_url ? '链接在有效期内只能使用一次。' : '公网地址尚未设置，目前显示的是相对路径。'
  inviteDialog.showModal()
}

async function start() {
  await loadPublicRuntimeIdentity()
  const setup = await api('/admin/setup-status')
  setupMode = !setup.configured
  if (setupMode) {
    ownerTitle.textContent = '设置管理员账户'
    ownerSubtitle.textContent = '这是第一次打开，设置一次账户和密码'
    ownerSubmit.textContent = '设置并进入'
    ownerAccount.maxLength = 80
    ownerAccount.autocomplete = 'username'
    ownerPassword.autocomplete = 'new-password'
  } else {
    if (setup.authenticated) return enter()
  }
  gate.hidden = false
  tellParent('login')
}

async function enter() {
  gate.hidden = true
  shell.hidden = false
  tellParent('management')
  await loadRuntimeContext()
  await Promise.all([loadAccounts(), loadAnnouncements(), loadFeedback(), loadReleaseStatus(), loadSupervisorRestartStatus()])
}

ownerForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  ownerMessage.textContent = ''
  ownerSubmit.disabled = true
  try {
    const result = await api(setupMode ? '/admin/setup' : '/admin/login', {
      method: 'POST',
      body: JSON.stringify({ account: ownerAccount.value, password: ownerPassword.value }),
    })
    ownerPassword.value = ''
    if (result.destination === 'company' && result.handoff_url) {
      tellParent('company')
      window.location.assign(result.handoff_url)
      return
    }
    await enter()
  } catch (error) {
    ownerMessage.textContent = error.message
  } finally {
    ownerSubmit.disabled = false
  }
})

ownerPasswordToggle.addEventListener('click', () => {
  const showing = ownerPassword.type === 'text'
  ownerPassword.type = showing ? 'password' : 'text'
  ownerPasswordToggle.setAttribute('aria-label', showing ? '显示密码' : '隐藏密码')
  ownerPasswordToggle.title = showing ? '显示密码' : '隐藏密码'
})

newAccountButton.addEventListener('click', () => {
  accountForm.reset()
  accountForm.elements.company_purpose.value = '帮我把手上的工作做好'
  accountForm.elements.chat_daily_usd.value = '1'
  accountForm.elements.chat_monthly_usd.value = '20'
  accountForm.elements.search_daily_usd.value = '0.2'
  accountForm.elements.search_monthly_usd.value = '5'
  accountMessage.textContent = ''
  accountDialog.showModal()
})

document.querySelectorAll('.admin-tab').forEach((button) => {
  button.addEventListener('click', () => {
    const view = button.dataset.view
    document.querySelectorAll('.admin-tab').forEach((item) => item.classList.toggle('active', item === button))
    accountsView.hidden = view !== 'accounts'
    announcementsView.hidden = view !== 'announcements'
    feedbackView.hidden = view !== 'feedback'
    newAccountButton.hidden = view !== 'accounts'
    releaseButton.hidden = view !== 'accounts'
    newAnnouncementButton.hidden = view !== 'announcements'
  })
})

feedbackFilter.addEventListener('change', renderFeedback)

feedbackList.addEventListener('click', (event) => {
  const button = event.target.closest('button[data-feedback-id]')
  if (!button) return
  selectedFeedback = feedback.find((item) => String(item.feedback_id) === button.dataset.feedbackId)
  if (!selectedFeedback) return
  feedbackDetailKind.textContent = selectedFeedback.kind
  feedbackDetailTitle.textContent = `反馈 #${selectedFeedback.feedback_id}`
  feedbackDetailUser.textContent = `${selectedFeedback.display_name || selectedFeedback.call_name || '未命名用户'} · ${selectedFeedback.email}`
  feedbackDetailTime.textContent = dateTimeText(selectedFeedback.created_at)
  feedbackDetailVersion.textContent = versionText(selectedFeedback.version)
  feedbackDetailPage.textContent = selectedFeedback.page || '—'
  feedbackDetailBody.textContent = selectedFeedback.body
  feedbackDetailStatus.value = selectedFeedback.status
  feedbackMessage.textContent = ''
  feedbackDetailImageWrap.hidden = !selectedFeedback.has_screenshot
  if (selectedFeedback.has_screenshot) feedbackDetailImage.src = `/admin/feedback/${selectedFeedback.feedback_id}/screenshot`
  else feedbackDetailImage.removeAttribute('src')
  feedbackDialog.showModal()
})

feedbackForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  if (event.submitter?.value === 'cancel' || !selectedFeedback) return feedbackDialog.close()
  feedbackSave.disabled = true
  feedbackMessage.textContent = ''
  try {
    await api(`/admin/feedback/${selectedFeedback.feedback_id}`, {
      method: 'PATCH', body: JSON.stringify({ status: feedbackDetailStatus.value }),
    })
    feedbackDialog.close()
    notify('反馈状态已保存')
    await loadFeedback()
  } catch (error) {
    feedbackMessage.textContent = error.message
  } finally {
    feedbackSave.disabled = false
  }
})

function openAnnouncementDialog(announcement = null) {
  announcementForm.reset()
  announcementBody.replaceChildren()
  editingAnnouncementId = announcement?.announcement_id || null
  announcementDialogKicker.textContent = editingAnnouncementId ? '保存后仍保持当前上下架状态' : '打开网页时显示'
  announcementDialogTitle.textContent = editingAnnouncementId ? '编辑公告' : '发布公告'
  announcementConfirm.textContent = editingAnnouncementId ? '保存修改' : '发布公告'
  if (announcement) {
    announcementTitle.value = announcement.title || ''
    if (announcement.body_rich) announcementBody.innerHTML = announcement.body_rich
    else announcementBody.append(document.createTextNode(announcement.body || ''))
  }
  announcementSelection = null
  window.getSelection()?.removeAllRanges()
  announcementEmojiPanel.hidden = true
  announcementEmojiToggle.setAttribute('aria-expanded', 'false')
  announcementMessage.textContent = ''
  updateAnnouncementPreview()
  announcementDialog.showModal()
  announcementTitle.focus()
}

newAnnouncementButton.addEventListener('click', () => {
  openAnnouncementDialog()
})

announcementTitle.addEventListener('input', updateAnnouncementPreview)
announcementBody.addEventListener('input', () => {
  rememberAnnouncementSelection()
  updateAnnouncementPreview()
})
announcementBody.addEventListener('keyup', rememberAnnouncementSelection)
announcementBody.addEventListener('mouseup', rememberAnnouncementSelection)
announcementBody.addEventListener('paste', (event) => {
  event.preventDefault()
  const text = event.clipboardData?.getData('text/plain') || ''
  document.execCommand('insertText', false, text)
})

announcementToolbar.addEventListener('mousedown', (event) => {
  if (event.target.closest('button')) event.preventDefault()
})
announcementToolbar.addEventListener('click', (event) => {
  const commandButton = event.target.closest('[data-editor-command]')
  if (commandButton) {
    runAnnouncementCommand(commandButton.dataset.editorCommand)
    return
  }
  const colorButton = event.target.closest('[data-editor-color]')
  if (colorButton) {
    runAnnouncementCommand('foreColor', colorButton.dataset.editorColor)
    return
  }
  const emojiButton = event.target.closest('[data-editor-emoji]')
  if (emojiButton) {
    runAnnouncementCommand('insertText', emojiButton.dataset.editorEmoji)
    announcementEmojiPanel.hidden = true
    announcementEmojiToggle.setAttribute('aria-expanded', 'false')
  }
})
announcementBlockStyle.addEventListener('change', () => {
  if (announcementBlockStyle.value) runAnnouncementCommand('formatBlock', announcementBlockStyle.value)
  announcementBlockStyle.value = ''
})
announcementFont.addEventListener('change', () => {
  if (announcementFont.value) runAnnouncementCommand('fontName', announcementFont.value)
  announcementFont.value = ''
})
announcementSize.addEventListener('change', () => {
  if (announcementSize.value) runAnnouncementCommand('fontSize', announcementSize.value)
  announcementSize.value = ''
})
announcementEmojiToggle.addEventListener('click', () => {
  announcementEmojiPanel.hidden = !announcementEmojiPanel.hidden
  announcementEmojiToggle.setAttribute('aria-expanded', String(!announcementEmojiPanel.hidden))
})

announcementForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  if (event.submitter?.value === 'cancel') return announcementDialog.close()
  if (!announcementForm.reportValidity()) return
  const body = announcementPlainText()
  if (!body) {
    announcementMessage.textContent = '请填写公告正文'
    announcementBody.focus()
    return
  }
  if (body.length > 8000) {
    announcementMessage.textContent = '公告正文不能超过 8000 个字符'
    announcementBody.focus()
    return
  }
  announcementConfirm.disabled = true
  announcementMessage.textContent = ''
  try {
    const editing = editingAnnouncementId !== null
    await api(editing ? `/admin/announcements/${editingAnnouncementId}` : '/admin/announcements', {
      method: editing ? 'PATCH' : 'POST',
      body: JSON.stringify({
        title: announcementTitle.value,
        body,
        body_rich: announcementBody.innerHTML,
      }),
    })
    announcementDialog.close()
    notify(editing ? '公告修改已保存' : '公告已发布；正式用户下次打开公司网页时会显示')
    await loadAnnouncements()
  } catch (error) {
    announcementMessage.textContent = error.message
  } finally {
    announcementConfirm.disabled = false
  }
})

announcementList.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-announcement-action]')
  if (!button) return
  const action = button.dataset.announcementAction
  if (action === 'edit') {
    const announcement = announcements.find((item) => String(item.announcement_id) === button.dataset.id)
    if (announcement) openAnnouncementDialog(announcement)
    return
  }
  button.disabled = true
  try {
    await api(`/admin/announcements/${button.dataset.id}/${action}`, { method: 'POST', body: '{}' })
    notify(action === 'withdraw' ? '公告已撤下' : '公告已重新发布')
    await loadAnnouncements()
  } catch (error) {
    notify(error.message)
  } finally {
    button.disabled = false
  }
})

releaseButton.addEventListener('click', async () => {
  releaseButton.disabled = true
  try {
    await loadReleaseStatus()
    if (!releaseIsRunning(releaseOperation()?.record) && !releaseStatus.latest_candidate) releaseNotes.value = ''
    renderReleaseModal()
    releaseDialog.showModal()
  } catch (error) {
    notify(error.message)
  } finally {
    releaseButton.disabled = false
  }
})

restartSupervisorButton.addEventListener('click', async () => {
  restartSupervisorButton.disabled = true
  try {
    await loadSupervisorRestartStatus()
    supervisorRestartError.textContent = ''
    renderSupervisorRestart()
    supervisorRestartDialog.showModal()
  } catch (error) {
    notify(error.message)
  } finally {
    restartSupervisorButton.disabled = false
  }
})

supervisorRestartForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  if (event.submitter?.value === 'cancel') return supervisorRestartDialog.close()
  if (supervisorRestartIsRunning()) return
  supervisorRestartConfirm.disabled = true
  supervisorRestartError.textContent = ''
  try {
    const result = await api('/admin/system-restart', { method: 'POST', body: '{}' })
    supervisorRestartStatus = result.restart || null
    watchedSupervisorRestart = supervisorRestartStatus?.restart_id || ''
    renderSupervisorRestart()
    window.clearTimeout(supervisorRestartPollTimer)
    supervisorRestartPollTimer = window.setTimeout(() => loadSupervisorRestartStatus({ poll: true }).catch(() => {}), 900)
  } catch (error) {
    supervisorRestartError.textContent = error.message
    supervisorRestartConfirm.disabled = false
  }
})

async function restartBackendsForRelease() {
  const currentState = releaseStatus?.development_admin || {}
  const expectedHead = String(currentState.current_head || '')
  const result = await api('/admin/system-restart', { method: 'POST', body: '{}' })
  supervisorRestartStatus = result.restart || null
  watchedSupervisorRestart = supervisorRestartStatus?.restart_id || ''
  const deadline = Date.now() + 180000
  while (Date.now() < deadline) {
    const elapsed = supervisorRestartElapsed(supervisorRestartStatus)
    releaseMessage.textContent = `${supervisorRestartStatus?.stage || '正在重启后台'}${elapsed ? ` · 已用 ${elapsed} 秒` : ''}`
    await new Promise((resolve) => window.setTimeout(resolve, 1000))
    try {
      const body = await api('/admin/system-restart')
      supervisorRestartStatus = body.restart || null
      if (supervisorRestartStatus?.status === '失败') {
        throw new Error(supervisorRestartStatus.error || supervisorRestartStatus.stage || '后台重启失败')
      }
      if (supervisorRestartStatus?.status !== '完成') continue
      await loadRuntimeContext()
      await loadReleaseStatus()
      const state = releaseStatus?.development_admin || {}
      if (!state.reload_required && (!expectedHead || state.loaded_head === expectedHead)) return
    } catch (error) {
      const message = String(error?.message || '')
      const temporarilyDisconnected = /Failed to fetch|Load failed|NetworkError/i.test(message)
      // 进程替换期间只有短暂断线可以继续等；权限、状态等真实错误立即显示。
      if (!temporarilyDisconnected) throw error
    }
  }
  throw new Error('后台没有在 180 秒内全部恢复；发布没有开始')
}

releaseForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  if (event.submitter?.value === 'cancel') return releaseDialog.close()
  if (!isDevelopmentAdmin()) {
    releaseMessage.textContent = '这是正式运行维护页。候选和发布只能从开发管理页操作。'
    return
  }
  if (releaseIsRunning(releaseOperation()?.record)) return
  if (releaseStatus?.development_admin?.reload_required) {
    releaseConfirm.disabled = true
    releaseMessage.textContent = '正在同时重启正式用户后台和发布后台…'
    try {
      await restartBackendsForRelease()
      releaseMessage.textContent = '两边后台都已加载完成。请重新确认发布。'
      notify('后台已全部重新加载')
      renderReleaseModal()
    } catch (error) {
      releaseMessage.textContent = error.message
    } finally {
      releaseConfirm.disabled = false
    }
    return
  }
  const candidate = releaseStatus?.latest_candidate
  const candidateStale = releaseCandidateIsStale(candidate, releaseStatus?.source)
  const candidateReady = candidate?.status === '待发布' && !candidateStale
  if (candidateReady && !releaseName.value.trim()) return (releaseMessage.textContent = '请先填写正式版本名称')
  if (!candidateReady && !releaseStatus?.source?.ready) return (releaseMessage.textContent = releaseStatus?.source?.reason || '发布来源尚未准备好')
  releaseConfirm.disabled = true
  releaseMessage.textContent = ''
  try {
    const result = await api(candidateReady ? '/admin/releases' : '/admin/release-candidates', {
      method: 'POST',
      body: JSON.stringify(candidateReady
        ? { candidate_id: candidate.candidate_id, version_name: releaseName.value.trim() }
        : { notes: releaseNotes.value, source_head: releaseStatus.source.source.head }),
    })
    if (result.candidate) {
      releaseStatus.latest_candidate = result.candidate
      releaseStatus.candidates = [
        result.candidate,
        ...(releaseStatus.candidates || []).filter((item) => item.candidate_id !== result.candidate.candidate_id),
      ]
    }
    if (result.deployment) releaseStatus.latest_deployment = result.deployment
    if (result.deployment?.details?.version_name) releaseName.value = result.deployment.details.version_name
    watchedRelease = result.candidate?.candidate_id || result.deployment?.deployment_id || ''
    renderReleaseModal()
    updateReleaseButton()
    window.clearTimeout(releasePollTimer)
    scheduleReleasePoll(900)
  } catch (error) {
    if (error.code === 'host-runtime-approval-required') {
      releaseHostApprovalRequired = {
        reason: error.reason || error.message,
        candidate_id: error.candidate_id || candidate?.candidate_id || '',
      }
      renderReleaseModal()
    }
    releaseMessage.textContent = error.message
  } finally {
    releaseConfirm.disabled = false
  }
})

releaseName.addEventListener('input', () => {
  if (releaseStatus?.latest_candidate?.status === '待发布'
    && !releaseCandidateIsStale(releaseStatus.latest_candidate, releaseStatus.source)
    && !releaseIsRunning(releaseOperation()?.record)) {
    releaseConfirm.disabled = !releaseName.value.trim()
  }
})

releaseHostApprovalConfirm.addEventListener('click', async () => {
  const candidate = releaseStatus?.latest_candidate
  const candidateId = String(candidate?.candidate_id || releaseHostApprovalRequired?.candidate_id || '')
  if (!candidateId || releaseHostApprovalConfirm.disabled) return
  releaseHostApprovalConfirm.disabled = true
  releaseHostApprovalCopy.textContent = '正在建立候选隔离环境并验收三个宿主服务；不会切换正式服务。'
  try {
    await api('/admin/releases/host-runtime/approve', {
      method: 'POST',
      body: JSON.stringify({
        candidate_id: candidateId,
        confirm: '为当前候选建立隔离环境',
      }),
    })
    releaseHostApprovalRequired = null
    await loadReleaseStatus()
    notify('候选隔离环境已通过验收，现在可以确认发布')
  } catch (error) {
    releaseHostApprovalCopy.textContent = error.message
    releaseHostApprovalConfirm.disabled = false
    await loadReleaseStatus().catch(() => {})
  }
})

releaseLogDetails.addEventListener('toggle', () => {
  if (releaseLogDetails.open) loadReleaseLog({ force: !releaseLogComplete }).catch(() => {})
})

releaseLogRefresh.addEventListener('click', () => {
  loadReleaseLog({ force: true }).catch(() => {})
})

releaseRollbackConfirm.addEventListener('click', async () => {
  const version = releaseRollbackVersion.value
  if (!version || releaseIsRunning(releaseOperation()?.record)) return
  releaseRollbackConfirm.disabled = true
  releaseMessage.textContent = ''
  try {
    const result = await api('/admin/releases/rollback', {
      method: 'POST',
      body: JSON.stringify({ version }),
    })
    releaseStatus.latest_deployment = result.deployment
    watchedRelease = result.deployment.deployment_id
    renderReleaseModal()
    updateReleaseButton()
    window.clearTimeout(releasePollTimer)
    scheduleReleasePoll(900)
  } catch (error) {
    releaseMessage.textContent = error.message
  } finally {
    releaseRollbackConfirm.disabled = false
  }
})

accountForm.addEventListener('submit', async (event) => {
  event.preventDefault()
  if (event.submitter?.value === 'cancel') return accountDialog.close()
  if (!accountForm.reportValidity()) return
  const values = Object.fromEntries(new FormData(accountForm))
  accountMessage.textContent = ''
  event.submitter.disabled = true
  try {
    const result = await api('/admin/accounts', { method: 'POST', body: JSON.stringify(values) })
    accountDialog.close()
    showInvite(result.invite)
    await loadAccounts()
  } catch (error) {
    accountMessage.textContent = error.message
  } finally {
    event.submitter.disabled = false
  }
})

list.addEventListener('click', async (event) => {
  const button = event.target.closest('button[data-action]')
  if (!button) return
  const account = accounts.find((item) => item.account_id === button.dataset.id)
  if (!account) return
  const action = button.dataset.action
  if (action === 'invite' || action === 'reset') {
    button.disabled = true
    try {
      const result = await api(`/admin/accounts/${account.account_id}/${action === 'invite' ? 'invite' : 'reset-password'}`, { method: 'POST', body: '{}' })
      showInvite(result, action === 'reset')
      await loadAccounts()
    } catch (error) { notify(error.message) } finally { button.disabled = false }
    return
  }
  pendingAction = { action, account }
  confirmInputWrap.hidden = action !== 'purge'
  confirmInputLabel.textContent = action === 'purge' ? `输入这个账户编号确认永久删除：${account.account_id}` : ''
  confirmInput.value = ''
  confirmMessage.textContent = ''
  if (action === 'stop') {
    confirmTitle.textContent = `停用 ${account.display_name || account.email}`
    confirmCopy.textContent = '用户会被退出，不能继续使用；她的资料会保留。'
    confirmButton.textContent = '停用账户'
  } else if (action === 'restart') {
    confirmTitle.textContent = `恢复 ${account.display_name || account.email}`
    confirmCopy.textContent = '用户可以重新登录，原来的资料保持不变。'
    confirmButton.textContent = '恢复账户'
  } else {
    confirmTitle.textContent = `永久删除 ${account.display_name || account.email}`
    confirmCopy.textContent = '这会删除该用户的公司、资料和模型凭据，无法撤销。'
    confirmButton.textContent = '永久删除'
  }
  confirmDialog.showModal()
})

document.querySelector('#confirm-form').addEventListener('submit', async (event) => {
  event.preventDefault()
  if (event.submitter?.value === 'cancel') return confirmDialog.close()
  const { action, account } = pendingAction || {}
  if (!action || !account) return
  if (action === 'purge' && confirmInput.value !== account.account_id) {
    confirmMessage.textContent = '账户编号不一致，未删除。'
    return
  }
  confirmButton.disabled = true
  try {
    const method = action === 'purge' ? 'DELETE' : 'POST'
    const path = action === 'purge' ? `/admin/accounts/${account.account_id}` : `/admin/accounts/${account.account_id}/${action}`
    await api(path, { method, body: JSON.stringify(action === 'purge' ? { confirm: account.account_id } : {}) })
    confirmDialog.close()
    notify(action === 'stop' ? '账户已停用' : action === 'restart' ? '账户已恢复' : '账户已永久删除')
    await loadAccounts()
  } catch (error) { confirmMessage.textContent = error.message } finally { confirmButton.disabled = false }
})

document.querySelector('#copy-button').addEventListener('click', async () => {
  await navigator.clipboard.writeText(inviteValue.value)
  notify('邀请链接已复制')
})
document.querySelector('#invite-close').addEventListener('click', () => inviteDialog.close())
document.querySelectorAll('[data-dialog-close]').forEach((button) => {
  button.addEventListener('click', () => button.closest('dialog')?.close())
})
document.querySelector('#refresh-button').addEventListener('click', () => window.location.reload())
search.addEventListener('input', render)
document.querySelector('#logout-button').addEventListener('click', async () => {
  await api('/admin/logout', { method: 'POST', body: '{}' })
  window.location.reload()
})
document.querySelector('#owner-close').addEventListener('click', () => {
  if (embedded && window.parent !== window) window.parent.postMessage({ type: 'xj-admin-close' }, '*')
})

start().catch((error) => { ownerMessage.textContent = error.message })
