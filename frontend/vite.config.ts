import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// Dev: the Vite server proxies API + WebSocket to the FastAPI backend on :8100.
// Prod: FastAPI serves the built `dist/` itself.
// build stamp shown in feedback context (the public build comes from `git archive`, without .git)
const APP_VERSION = process.env.APP_VERSION || new Date().toISOString().slice(0, 16).replace('T', ' ')

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: { __APP_VERSION__: JSON.stringify(APP_VERSION) },
  server: {
    port: 5173,
    host: true,
    proxy: {
      '/api': 'http://127.0.0.1:8100',
      '/ws': { target: 'ws://127.0.0.1:8100', ws: true },
    },
  },
  build: { sourcemap: false, chunkSizeWarningLimit: 1200 },
})
