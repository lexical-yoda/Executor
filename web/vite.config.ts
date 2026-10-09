import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs locally on 1977 (see README, "Local development").
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:1977' },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
  },
})
