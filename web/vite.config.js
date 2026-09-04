import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath, URL } from 'node:url'

// 构建产物直接输出到 Flask 的 static 目录。
// 注意: 构建会清空输出目录 —— 需要长期保留的静态资源放在 web/public/
// （构建时原样拷贝，如 notice.txt、explanation.png）。
export default defineConfig({
  plugins: [vue()],
  base: '/',
  resolve: {
    alias: {
      '@': fileURLToPath(new URL('./src', import.meta.url)),
    },
  },
  server: {
    // 本地开发时代理 API 到 master（默认 25565）
    proxy: {
      '/auth': 'http://127.0.0.1:25565',
      '/tasks': 'http://127.0.0.1:25565',
      '/nodes': 'http://127.0.0.1:25565',
      '/experiments': 'http://127.0.0.1:25565',
      '/admin': 'http://127.0.0.1:25565',
      '/static': 'http://127.0.0.1:25565',
      '/healthz': 'http://127.0.0.1:25565',
    },
  },
  build: {
    outDir: '../src/neu_box_webui/master/static',
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
  },
})
