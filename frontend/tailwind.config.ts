import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      // channel-формат токенов (ADR-064 §A): каждый токен — rgb(var(--x) / <alpha-value>),
      // чтобы opacity-модификаторы Tailwind (bg-status-red/90, ring-accent/40 …) работали.
      // Значения переменных в index.css — space-separated RGB-триплеты (§B).
      colors: {
        bg: {
          base: 'rgb(var(--bg-base) / <alpha-value>)',
        },
        surface: {
          1: 'rgb(var(--surface-1) / <alpha-value>)',
          2: 'rgb(var(--surface-2) / <alpha-value>)',
          3: 'rgb(var(--surface-3) / <alpha-value>)',
        },
        border: {
          subtle: 'rgb(var(--border-subtle) / <alpha-value>)',
          strong: 'rgb(var(--border-strong) / <alpha-value>)',
        },
        text: {
          primary: 'rgb(var(--text-primary) / <alpha-value>)',
          secondary: 'rgb(var(--text-secondary) / <alpha-value>)',
          tertiary: 'rgb(var(--text-tertiary) / <alpha-value>)',
        },
        accent: {
          DEFAULT: 'rgb(var(--accent) / <alpha-value>)',
          hover: 'rgb(var(--accent-hover) / <alpha-value>)',
        },
        status: {
          green: 'rgb(var(--status-green) / <alpha-value>)',
          yellow: 'rgb(var(--status-yellow) / <alpha-value>)',
          red: 'rgb(var(--status-red) / <alpha-value>)',
        },
        gauge: {
          track: 'rgb(var(--gauge-track) / <alpha-value>)',
        },
      },
      fontFamily: {
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        card: '16px',
        sub: '12px',
        chip: '8px',
        // Панель дропдауна навигации (NavMenu) — «квадратнее» карточек/кнопок
        // (ADR-023, 08-design-system.md «Сетка, отступы, скругления»).
        nav: '6px',
      },
      boxShadow: {
        // Тема-зависимы (ADR-033): значения в CSS-переменных (index.css),
        // переключаются вместе с data-theme. Прежде были статичны под тёмный фон.
        card: 'var(--shadow-card)',
        sub: 'var(--shadow-sub)',
      },
      keyframes: {
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        shake: {
          '0%, 100%': { transform: 'translateX(0)' },
          '20%, 60%': { transform: 'translateX(-6px)' },
          '40%, 80%': { transform: 'translateX(6px)' },
        },
        'fade-in': {
          from: { opacity: '0', transform: 'translateY(4px)' },
          to: { opacity: '1', transform: 'translateY(0)' },
        },
        'overlay-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
        'content-in': {
          from: { opacity: '0', transform: 'translate(-50%, -48%) scale(0.97)' },
          to: { opacity: '1', transform: 'translate(-50%, -50%) scale(1)' },
        },
      },
      animation: {
        shimmer: 'shimmer 1.6s ease-in-out infinite',
        shake: 'shake 0.4s ease-in-out',
        'fade-in': 'fade-in 0.25s ease-out',
        'overlay-in': 'overlay-in 0.2s ease-out',
        'content-in': 'content-in 0.22s ease-out',
      },
    },
  },
  plugins: [],
};

export default config;
