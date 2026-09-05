import { readFile, writeFile } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { scrubPrivateText } from './净化私有文本.mjs'

const frontend = process.argv[2]
if (!frontend) throw new Error('缺少前端目录参数')

async function replaceExact(relativePath, before, after, expected = 1) {
  const path = join(frontend, relativePath)
  const source = await readFile(path, 'utf8')
  const count = source.split(before).length - 1
  if (count !== expected) {
    throw new Error(`${relativePath} 覆盖锚点数量异常：预期 ${expected}，实际 ${count}`)
  }
  await writeFile(path, source.split(before).join(after), 'utf8')
}

const here = dirname(fileURLToPath(import.meta.url))
await writeFile(
  join(frontend, 'src', 'multiuserIdentity.ts'),
  await readFile(join(here, 'multiuserIdentity.ts'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'multiuserSession.ts'),
  await readFile(join(here, 'multiuserSession.ts'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'components', 'AccountMenu.tsx'),
  await readFile(join(here, 'AccountMenu.tsx'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'components', 'AnnouncementModal.tsx'),
  await readFile(join(here, 'AnnouncementModal.tsx'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'components', 'RuntimeVersionBadge.tsx'),
  await readFile(join(here, 'RuntimeVersionBadge.tsx'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'components', 'FeedbackModal.tsx'),
  await readFile(join(here, 'FeedbackModal.tsx'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'styles', 'multiuserAccount.css'),
  await readFile(join(here, 'multiuserAccount.css'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'styles', 'announcement.css'),
  await readFile(join(here, 'announcement.css'), 'utf8'),
  'utf8',
)
await writeFile(
  join(frontend, 'src', 'styles', 'feedback.css'),
  await readFile(join(here, 'feedback.css'), 'utf8'),
  'utf8',
)

await replaceExact(
  'index.html',
  '    <title>开发公司 · 船主的窗户</title>',
  '    <title>开发公司 · 船主的窗户</title>\n    <script src="/instance.js"></script>',
)

await replaceExact(
  'src/components/HallView.tsx',
  "import { toast } from '../toast'",
  "import { toast } from '../toast'\nimport { instanceIdentity } from '../multiuserIdentity'",
)
await replaceExact(
  'src/components/HallView.tsx',
  '        晚上好，主人',
  '        晚上好，{instanceIdentity.callName}',
)
await replaceExact(
  'src/components/HallView.tsx',
  "  return who.includes('船主') || who.includes('示例主人') || who === '你'",
  "  return who.includes('船主') || who === '你'",
)

await replaceExact(
  'src/components/Pages.tsx',
  "import { renderLightText } from './文本渲染'",
  "import { renderLightText } from './文本渲染'\nimport { instanceIdentity } from '../multiuserIdentity'",
)

await replaceExact(
  'src/components/HoloChrome.tsx',
  "import OwnerAccountMenu from './OwnerAccountMenu'",
  "import AccountMenu from './AccountMenu'",
)
await replaceExact(
  'src/components/HoloChrome.tsx',
  '      <OwnerAccountMenu />',
  '      <AccountMenu theme={theme} setTheme={setTheme} />',
)

await replaceExact(
  'src/main.tsx',
  "import './styles/owner-account.css'",
  "import './styles/multiuserAccount.css'\nimport './styles/announcement.css'\nimport './styles/feedback.css'",
)
await replaceExact(
  'src/App.tsx',
  "import { HoloChrome } from './components/HoloChrome'",
  "import { HoloChrome } from './components/HoloChrome'\nimport AnnouncementModal from './components/AnnouncementModal'",
)
await replaceExact(
  'src/App.tsx',
  '      <ManageConfirmModal board={board} refresh={refreshAll} />',
  "      <AnnouncementModal ready={visualPhase === 'idle'} />\n      <ManageConfirmModal board={board} refresh={refreshAll} />",
)
await replaceExact(
  'src/components/Pages.tsx',
  '<span className="crew-owner-title">示例主人 · 船主</span>',
  '<span className="crew-owner-title">{instanceIdentity.displayName} · 船主</span>',
)
await replaceExact(
  'src/components/Pages.tsx',
  '<span className="crew-person">示例主人 · 船主</span>',
  '<span className="crew-person">{instanceIdentity.displayName} · 船主</span>',
)

await replaceExact(
  'src/api.ts',
  '// 数据层 · 接现有办公室后端',
  "import { multiuserRequestHeaders } from './multiuserSession'\n\n// 数据层 · 接现有办公室后端",
)
await replaceExact(
  'src/api.ts',
  "    headers: { 'Content-Type': 'application/json' },",
  "    headers: { 'Content-Type': 'application/json', ...multiuserRequestHeaders() },",
)

await scrubPrivateText(join(frontend, 'src'))
