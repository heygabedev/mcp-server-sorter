import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';

const apiUrl = process.env.SORTER_API_URL ?? 'http://127.0.0.1:8000';

export default defineConfig({
  plugins: [react()],
  server: { proxy: { '/api': apiUrl, '/health': apiUrl } },
  test: {
    environment: 'jsdom',
    setupFiles: ['./src/test-setup.ts'],
    include: ['src/**/*.test.tsx'],
  },
});
