import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    port: Number(process.env.FRONTEND_PORT ?? 5173),
    strictPort: true,
    // 127.0.0.1, not localhost: uvicorn binds IPv4 only and Node may resolve localhost to ::1.
    proxy: { '/api': `http://127.0.0.1:${process.env.BACKEND_PORT ?? 8000}` },
  },
})
