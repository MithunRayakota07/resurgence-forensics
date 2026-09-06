import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Ports are deliberately non-default. 5173/8000 are what every other Vite and
// FastAPI project on this machine grabs, and colliding with an unrelated
// project's dev server is a confusing way to lose an evening.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5183,
    strictPort: true,
    proxy: { '/api': 'http://127.0.0.1:8781' }
  }
})
