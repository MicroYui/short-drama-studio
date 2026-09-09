import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import electron from 'vite-plugin-electron'
import renderer from 'vite-plugin-electron-renderer'
import path from 'path'
import { execFileSync } from 'node:child_process'

export default defineConfig({
  plugins: [
    { name: 'studio-export-runtime', buildStart() { execFileSync(process.execPath, ['scripts/build-studio-runtime.mjs'], { stdio: 'inherit' }) } },
    react(),
    ...(process.env.STUDIO_WEB_PREVIEW === '1' ? [] : [electron([
      {
        entry: 'electron/main.ts',
        onstart(options) {
          if (process.env.ELECTRON_DEBUG) {
            // --inspect and --remote-debugging-port must come before '.' (the app path)
            options.startup(['--inspect=9229', '--remote-debugging-port=9222', '.', '--no-sandbox'])
          } else {
            options.startup()
          }
        },
        vite: {
          build: {
            outDir: 'dist-electron',
            sourcemap: true,
            rollupOptions: {
              external: ['electron']
            }
          }
        }
      },
      {
        entry: 'electron/preload.ts',
        onstart(options) {
          options.reload()
        },
        vite: {
          build: {
            outDir: 'dist-electron',
            sourcemap: true,
            rollupOptions: {
              output: {
                format: 'cjs'  // Preload must be CommonJS
              }
            }
          }
        }
      }
    ]),
    renderer()])
  ],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './frontend')
    }
  },
  base: './',  // Use relative paths for Electron file:// protocol
  server: {
    host: '127.0.0.1',
    watch: { ignored: ['**/output/**'] },
    proxy: {
      '/__studio_backend': {
        target: process.env.STUDIO_BACKEND_URL || 'http://127.0.0.1:8018',
        rewrite: (url) => url.replace(/^\/__studio_backend/, ''),
        headers: process.env.STUDIO_BACKEND_TOKEN ? { Authorization: `Bearer ${process.env.STUDIO_BACKEND_TOKEN}` } : {},
      },
    },
  },
  build: {
    outDir: 'dist'
  }
})
