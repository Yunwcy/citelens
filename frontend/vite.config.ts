import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// 開發伺服器把 /api 代理到後端，讓開發與容器都走「同源路徑」。
// 這樣 api.ts 的 BASE 永遠是空字串，不需要在建置時注入位址 ——
// 少了這個代理，忘記帶 VITE_API 的一次建置就會把 localhost:8000 寫死進 bundle，
// 瀏覽器在 :3000 打 :8000 是跨來源，會被 CORS 擋成 Failed to fetch。
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/api': { target: 'http://localhost:8000', changeOrigin: true },
    },
  },
})
