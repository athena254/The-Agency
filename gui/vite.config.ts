import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// The built SPA is served by the Agency FastAPI prototype app:
//   /assets         -> src/agency/prototype/static
//   /mission-control -> static/mission-control/index.html
const OUT_DIR = '../src/agency/prototype/static/mission-control'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: '/assets/mission-control/',
  build: {
    outDir: OUT_DIR,
    emptyOutDir: true,
    sourcemap: false,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8001',
      '/v1': 'http://127.0.0.1:8000',
    },
  },
})
