import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// The dev server proxies `/api` to a backend. Default is a local backend on
// 8000, but set VITE_PROXY_TARGET (e.g. in frontend/.env.local) to point the
// local frontend at a pod backend over an SSH tunnel — same-origin from the
// browser's view, so no CORS. Prod is unaffected: it serves same-origin.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const proxyTarget = env.VITE_PROXY_TARGET || 'http://127.0.0.1:8000'
  return {
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    port: 1420,
    strictPort: true,
    proxy: {
      '/api': {
        target: proxyTarget,
        changeOrigin: true,
        secure: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    emptyOutDir: true,
  },
  }
})

