import { defineConfig } from 'vite'
import { resolve } from 'path'
import react from '@vitejs/plugin-react'
import tailwindcss from 'tailwindcss'
import autoprefixer from 'autoprefixer'

// Admin dashboard SPA. Served at /admin by the control-panel. Shares dumb components/infra
// from ../shared via the @shared alias. Smaller dep surface than the user app.
const API_TARGET = process.env.VITE_API_TARGET || 'http://localhost:8081'

export default defineConfig({
  base: '/admin/',
  root: __dirname,
  publicDir: resolve(__dirname, '../shared/public'),
  resolve: {
    alias: { '@shared': resolve(__dirname, '../shared') },
  },
  // Per-SPA session token key — distinct from the user SPA so logins don't cross over.
  define: {
    __PRIVA_TOKEN_KEY__: JSON.stringify('priva-admin-token'),
  },
  // Pin tailwind config to THIS app (cwd may be the web/ root during `vite build admin`).
  css: {
    postcss: {
      plugins: [tailwindcss(resolve(__dirname, 'tailwind.config.js')), autoprefixer()],
    },
  },
  plugins: [react()],
  server: {
    port: 5174,
    proxy: {
      // Admin Console terminal — WS upgrade must proxy with ws:true (see ConsoleView).
      '/api/pty/ws': { target: API_TARGET, changeOrigin: true, ws: true },
      '/api': { target: API_TARGET, changeOrigin: true },
    },
  },
  build: {
    outDir: 'dist',
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          const parts = id.split('node_modules/')[1]?.split('/') || []
          const pkg = parts[0]?.startsWith('@') ? `${parts[0]}/${parts[1]}` : parts[0]
          if (['react', 'react-dom', 'scheduler', 'react-is', 'prop-types', 'use-sync-external-store'].includes(pkg)) return 'vendor-react'
          if (pkg === 'zustand') return 'vendor-state'
          if (pkg === 'lucide-react') return 'vendor-icons'
          if (pkg === 'i18next' || pkg === 'react-i18next') return 'vendor-i18n'
          if (
            pkg === 'recharts' ||
            pkg === 'victory-vendor' ||
            pkg?.startsWith('d3-') ||
            ['react-smooth', 'recharts-scale', 'decimal.js-light', 'eventemitter3', 'fast-equals', 'internmap', 'clsx', 'tiny-invariant', '@babel/runtime'].includes(pkg)
          ) {
            return 'vendor-charts'
          }
          if (pkg === 'framer-motion' || pkg === 'motion-utils' || pkg === 'motion-dom') return 'vendor-motion'
          return 'vendor-misc'
        },
      },
    },
  },
})
