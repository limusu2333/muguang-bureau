import { readdir, readFile, writeFile } from 'node:fs/promises'
import { extname, join } from 'node:path'

const replacements = [
  ['实例主人', '实例主人'],
  ['示例主人', '示例主人'],
  ['主人', '主人'],
  ['192.0.2.10', '192.0.2.10'],
  ['192.0.2.11', '192.0.2.11'],
]
const textExtensions = new Set(['.css', '.html', '.js', '.json', '.md', '.mjs', '.py', '.ts', '.tsx', '.yaml', '.yml'])

export async function scrubPrivateText(root) {
  for (const entry of await readdir(root, { withFileTypes: true })) {
    const path = join(root, entry.name)
    if (entry.isDirectory()) {
      await scrubPrivateText(path)
      continue
    }
    if (!entry.isFile() || !textExtensions.has(extname(entry.name))) continue
    const source = await readFile(path, 'utf8')
    const scrubbed = replacements.reduce(
      (text, [privateText, genericText]) => text.split(privateText).join(genericText),
      source,
    )
    if (scrubbed !== source) await writeFile(path, scrubbed, 'utf8')
  }
}

if (process.argv[1] && import.meta.url === new URL(`file://${process.argv[1]}`).href) {
  const root = process.argv[2]
  if (!root) throw new Error('缺少待净化目录参数')
  await scrubPrivateText(root)
}
