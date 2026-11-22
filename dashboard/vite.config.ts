import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Served by FastAPI at /dashboard after `npm run build` (one port with the simulator).
// `npm run dev` serves http://localhost:5173/dashboard/ and proxies /api to the API on :8000.
export default defineConfig({
  base: '/dashboard/',
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8000' },
  },
})
