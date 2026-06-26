/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        teal: { 50: '#E2F5F1', 100: '#C4EBE3', 500: '#1A9E8A', 600: '#0A7060', 700: '#085A4E' },
        health: { green: '#15803D', yellow: '#B85E00', red: '#B91C1C' },
      },
    },
  },
  plugins: [],
}
