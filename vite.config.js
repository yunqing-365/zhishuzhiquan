import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

// https://vite.dev/config/
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendUrl = env.VITE_API_URL || 'http://localhost:8000'

  return {
    plugins: [
      tailwindcss(),
      react(),
    ],

    // 开发代理：把 /api 请求转发到后端，彻底避免 CORS 问题
    server: {
      port: 5173,
      proxy: {
        '/api': {
          target: backendUrl,
          changeOrigin: true,
          timeout: 10000,
          configure: (proxy) => {
            proxy.on('error', (err) => {
              console.warn('[vite-proxy] 后端连接失败:', err.message)
              console.warn('[vite-proxy] 确认后端已启动:', backendUrl)
            })
          },
        },
      },
    },

    // 生产构建：代码分割加快首屏
    build: {
      outDir: 'dist',
      sourcemap: false,
      rollupOptions: {
        output: {
          manualChunks: {
            vendor: ['react', 'react-dom'],
            charts: ['recharts'],
            icons: ['lucide-react'],
          },
        },
      },
    },
  }
})
