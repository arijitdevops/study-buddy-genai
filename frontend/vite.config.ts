import path from 'node:path';

import react from '@vitejs/plugin-react';
import { defineConfig, loadEnv } from 'vite';

// The dev server proxies /api to the FastAPI backend so the browser sees a
// single origin. `changeOrigin` is off deliberately: the backend's CORS list
// and rate-limit keys are simpler when the Host header is preserved.
export default defineConfig(({ mode }) => {
  // Reads frontend/.env* so VITE_PROXY_TARGET works from the .env file too.
  const env = loadEnv(mode, process.cwd(), '');
  return {
    plugins: [react()],
    resolve: {
      alias: {
        '@': path.resolve(__dirname, './src'),
      },
    },
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: env.VITE_PROXY_TARGET || 'http://localhost:8000',
          changeOrigin: false,
          // SSE must not be buffered by the proxy.
          ws: false,
        },
      },
    },
    build: {
      outDir: 'dist',
      sourcemap: true,
      // KaTeX + highlight.js make one ~870 kB chunk; fine for this app.
      chunkSizeWarningLimit: 1000,
    },
  };
});
