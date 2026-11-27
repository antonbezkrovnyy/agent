/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: '#0a0d12',
        panel: '#11161e',
        elevated: '#171d27',
        line: '#252c38',
        muted: '#8994a5',
        accent: '#77e0bd',
      },
      fontFamily: {
        sans: ['Inter', 'Segoe UI', 'sans-serif'],
        mono: ['Cascadia Code', 'Consolas', 'monospace'],
      },
      boxShadow: {
        panel: '0 16px 40px rgba(0, 0, 0, .22)',
      },
    },
  },
  plugins: [],
}
