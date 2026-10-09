import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the API runs locally on 1977 (see README, "Local development").
// EXECUTOR_API points the dev server at another instance instead, for example
// a deployed one reachable from this machine.
// EXECUTOR_LOCAL_API, when set too, serves the history endpoints and map tiles
// from a local backend while everything else comes from EXECUTOR_API.
declare const process: { env: Record<string, string | undefined> }
const api = process.env.EXECUTOR_API ?? 'http://127.0.0.1:1977'
const local = process.env.EXECUTOR_LOCAL_API ?? api
const localPaths = ['/api/events', '/api/recap', '/api/map', '/api/services', '/tiles']

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      ...Object.fromEntries(localPaths.map((path) => [path, { target: local, changeOrigin: true }])),
      '/api': { target: api, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    assetsDir: 'assets',
    sourcemap: false,
    // The map engine is one large, lazily loaded chunk by design.
    chunkSizeWarningLimit: 1200,
  },
})
