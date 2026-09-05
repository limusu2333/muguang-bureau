import { resolve } from 'node:path'
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发期：把所有数据/动作/SSE 接口代理到本地办公室后端(8787)，
// 这样 React dev server(5173) 直接复用现有后端，一行后端代码不用改。
// 生产期：办公室.py serve 本目录 dist/，同源，不需要代理。
const 后端 = 'http://127.0.0.1:8787'
const 接口 = [
  // GET 取数据
  '/board', '/hall', '/room', '/inbox', '/meetings', '/state', '/run-events', '/运行身份',
  // SSE 实时流
  '/stream', '/rollcall',
  // POST 动作
  '/活厅', '/插话', '/approval_decide', '/活厅_验收', '/panic',
  '/room_say', '/meeting_say',
  // 附件等静态
  '/附件',
]

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: Object.fromEntries(
      接口.map((p) => [p, { target: 后端, changeOrigin: true }]),
    ),
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        // 大厅入口原样不动；butterfly.html 是单只雾蝶的独立验飞页，不接后端。
        main: resolve(__dirname, 'index.html'),
        butterfly: resolve(__dirname, 'butterfly.html'),
      },
    },
  },
})
