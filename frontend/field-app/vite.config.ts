import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { VitePWA } from 'vite-plugin-pwa'

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: 'autoUpdate',
      workbox: {
        globPatterns: ['**/*.{js,css,html,ico,png,svg}'],
        runtimeCaching: [
          {
            urlPattern: /^https?:\/\/.*\/api\/v1\/(facilities|medicines)/,
            handler: 'StaleWhileRevalidate',
            options: { cacheName: 'api-cache', expiration: { maxAgeSeconds: 86400 } },
          },
        ],
      },
      manifest: {
        name: 'SmartHealth Field App',
        short_name: 'SmartHealth',
        description: 'PHC data entry — offline first',
        theme_color: '#0A7060',
        background_color: '#F5F4F0',
        display: 'standalone',
        icons: [
          { src: '/icon-192.png', sizes: '192x192', type: 'image/png' },
          { src: '/icon-512.png', sizes: '512x512', type: 'image/png' },
        ],
      },
    }),
  ],
  server: { port: 3001 },
})
