/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        // The public website (pages/Landing.tsx). System faces only: the CSP allows no external font
        display: ['"Segoe UI Variable Display"', '"SF Pro Display"', '-apple-system', 'BlinkMacSystemFont',
                  '"Helvetica Neue"', 'Helvetica', 'Arial', 'sans-serif'],
        body: ['"Segoe UI Variable Text"', '"SF Pro Text"', '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"',
               '"Helvetica Neue"', 'Arial', 'sans-serif'],
        label: ['ui-monospace', '"SF Mono"', '"Cascadia Mono"', '"JetBrains Mono"', 'Menlo', 'Consolas', 'monospace'],
      },
      colors: {
        ink: { DEFAULT: '#0B1018', raised: '#0F1620', line: '#1E2835', muted: '#8B97A5', soft: '#A9B3BE' },
        paper: { DEFAULT: '#F5F5F0', deep: '#ECECE4', line: '#D9D9CF', muted: '#5B636C' },
        mist: '#F4F4F2',
        graphite: '#101820',
        accent: { DEFAULT: '#168A82', light: '#4FC1B7', dark: '#0E6B65' },
      },
      keyframes: {
        draw: { to: { strokeDashoffset: '0' } },
        rise: { from: { opacity: '0', transform: 'translateY(10px)' }, to: { opacity: '1', transform: 'none' } },
      },
      animation: {
        draw: 'draw 1.6s ease-out forwards',
        rise: 'rise 0.7s ease-out both',
      },
    },
  },
  plugins: [],
}
