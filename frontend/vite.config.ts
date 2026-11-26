import { defineConfig } from 'vitest/config';
import path from 'node:path';

const host = process.env.TAURI_DEV_HOST;
const backendDevPort = process.env.BACKEND_DEV_PORT || '8000';

export default defineConfig({
  root: path.resolve(__dirname),
  clearScreen: false,
  envPrefix: ['VITE_', 'TAURI_ENV_'],
  server: {
    port: 5173,
    strictPort: true,
    host: host || '127.0.0.1',
    hmr: host ? { protocol: 'ws', host, port: 5174 } : undefined,
    watch: {
      ignored: ['**/src-tauri/**', '**/engine/**', '**/tests/**', '**/*.db*']
    },
    proxy: {
      '/api': {
        target: `http://127.0.0.1:${backendDevPort}`,
        changeOrigin: true
      },
      '/ws': {
        target: `ws://127.0.0.1:${backendDevPort}`,
        ws: true
      }
    }
  },
  build: {
    outDir: path.resolve(__dirname, 'dist'),
    emptyOutDir: true,
    target: process.env.TAURI_ENV_PLATFORM === 'windows' ? 'chrome105' : 'safari14',
    minify: !process.env.TAURI_ENV_DEBUG ? 'esbuild' : false,
    sourcemap: !!process.env.TAURI_ENV_DEBUG,
    rollupOptions: {
      output: {
        manualChunks: {
          'lwc-engine': ['lightweight-charts']
        }
      }
    }
  },
  css: {
    postcss: {
      plugins: []
    }
  },
  test: {
    environment: 'node',
    include: ['src/**/*.test.ts']
  }
});
