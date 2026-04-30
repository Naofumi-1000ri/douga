import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import path from 'path'
import { execSync } from 'child_process'
/// <reference types="vitest" />

// Get git commit hash for version display
const getGitHash = () => {
  try {
    return execSync('git rev-parse --short HEAD').toString().trim()
  } catch {
    return 'unknown'
  }
}

export default defineConfig({
  plugins: [react()],
  define: {
    __APP_VERSION__: JSON.stringify(getGitHash()),
    __BUILD_TIME__: JSON.stringify(new Date().toISOString()),
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          const normalized = id.split(path.sep).join('/')

          if (normalized.includes('/node_modules/')) {
            if (normalized.includes('/firebase/')) return 'vendor-firebase'
            if (
              normalized.includes('/react/') ||
              normalized.includes('/react-dom/') ||
              normalized.includes('/react-router-dom/') ||
              normalized.includes('/react-i18next/')
            ) {
              return 'vendor-react'
            }
            if (
              normalized.includes('/i18next/') ||
              normalized.includes('/i18next-browser-languagedetector/')
            ) {
              return 'vendor-i18n'
            }
            if (normalized.includes('/zustand/')) return 'vendor-zustand'
            if (normalized.includes('/@dnd-kit/')) return 'vendor-dnd'
            if (normalized.includes('/wavesurfer.js/')) return 'vendor-wavesurfer'
            if (normalized.includes('/axios/')) return 'vendor-axios'
          }

          if (normalized.includes('/src/store/projectStore')) return 'editor-store'
          if (normalized.includes('/src/hooks/')) return 'editor-hooks'
          if (normalized.includes('/src/utils/keyframes') || normalized.includes('/src/utils/volumeKeyframes') || normalized.includes('/src/utils/editorLayoutSettings')) {
            return 'editor-utils'
          }

          if (normalized.includes('/src/api/assets')) return 'editor-assets-api'
          if (normalized.includes('/src/api/sequences')) return 'editor-sequences-api'
          if (normalized.includes('/src/api/projects')) return 'editor-projects-api'
          if (normalized.includes('/src/api/operations')) return 'editor-operations-api'
          if (normalized.includes('/src/api/transcription')) return 'editor-transcription-api'
          if (normalized.includes('/src/api/aiV1')) return 'editor-ai-v1-api'
          if (normalized.includes('/src/api/aiVideo')) return 'editor-ai-video-api'
          if (normalized.includes('/src/api/members')) return 'editor-members-api'
          if (normalized.includes('/src/api/apiKeys')) return 'editor-api-keys-api'
          if (normalized.includes('/src/api/client')) return 'editor-http'
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  test: {
    globals: true,
    environment: 'node',
    include: ['src/**/*.test.ts', 'src/**/*.test.tsx'],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:8000',
        changeOrigin: true,
      },
    },
  },
})
