/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: 'class',
  content: ['./app/templates/**/*.html'],
  theme: {
    extend: {
      colors: {
        dark: {
          800: 'var(--clr-panel)',
          900: 'var(--clr-input)',
          700: 'var(--clr-panel-alt)',
          600: 'var(--clr-toolbar)',
        },
        accent: { 500: '#6366f1', 600: '#4f46e5', 400: '#818cf8' },
      },
    },
  },
  plugins: [],
}
