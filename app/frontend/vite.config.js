import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// `npm run dev` proxies /api to the FastAPI backend (MDM_LOCAL=1).

export default defineConfig({
  plugins: [react()],
  server: {
    host: 'localhost',
    port: 5173,
    strictPort: true,
    proxy: { '/api': 'http://localhost:8000' },
  },
  build: { outDir: 'dist', sourcemap: false },
})
