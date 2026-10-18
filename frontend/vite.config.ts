import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import type { InlineConfig } from 'vitest/node'

// Vitest 2 bundles its own Vite 5 types, so its `test` typing does not reach
// this project's Vite 6 config type. Declare the Vitest section here instead.
declare module 'vite' {
  interface UserConfig {
    test?: InlineConfig
  }
}

export default defineConfig({
  plugins: [react()],
  // Hashed build files live under /static/, so that they can never collide with the application's
  // /assets/<id> routes, and a host can cache them as immutable (DEPLOYMENT.md)
  build: { assetsDir: 'static' },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
  },
})
