/// <reference types="vitest/config" />
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // FALLBACK-OK: interfaces.md FE-11 (the dev proxy targets :8420 unless BOTSWORTH_PORT says otherwise)
      '/api': `http://127.0.0.1:${process.env.BOTSWORTH_PORT ?? 8420}`,
    },
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    setupFiles: ['src/test-setup.ts'],
    // Per-test limit (default 5 s) is too tight for a blade test when 8 workers share the CPU; a hung test still fails.
    testTimeout: 20000,
  },
})
