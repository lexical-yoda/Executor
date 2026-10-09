import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs locally on 1977 (see README, "Local development").
// EXECUTOR_API points the dev server at another instance instead, for example
// a deployed one reachable from this machine.
declare const process: { env: Record<string, string | undefined> }
const api = process.env.EXECUTOR_API ?? 'http://127.0.0.1:1977'

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': { target: api, changeOrigin: true } },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
  },
})
