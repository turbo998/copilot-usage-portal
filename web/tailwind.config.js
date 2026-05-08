/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0b0d12',
        panel: '#11141b',
        border: '#1f2530',
        accent: '#7aa2ff',
        muted: '#7d8597',
      },
    },
  },
  plugins: [],
};
