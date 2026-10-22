/**
 * vite.config.js - build tool configuration.
 *
 * The important part is the dev-server PROXY: while developing, the React app runs on
 * http://localhost:5173 and the API on http://localhost:5000. Any request the browser makes to
 * /api/... is forwarded to Express, so the client code can use relative URLs ("/api/analyses")
 * and there are no CORS problems. In production nginx does the same job (client/nginx.conf).
 */
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: process.env.VITE_API_TARGET || 'http://localhost:5000', changeOrigin: true },
    },
  },
});
