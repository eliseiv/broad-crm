import type { Config } from 'tailwindcss';

const config: Config = {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  darkMode: 'class',
  theme: {
    extend: {
      colors: {
        bg: {
          base: 'var(--bg-base)',
        },
        surface: {
          1: 'var(--surface-1)',
          2: 'var(--surface-2)',
          3: 'var(--surface-3)',
        },
        border: {
          subtle: 'var(--border-subtle)',
          strong: 'var(--border-strong)',
        },
        text: {
          primary: 'var(--text-primary)',
          secondary: 'var(--text-secondary)',
          tertiary: 'var(--text-tertiary)',
        },
        accent: {
          DEFAULT: 'var(--accent)',
          hover: 'var(--accent-hover)',
        },
        status: {
          green: 'var(--status-green)',
          yellow: 'var(--status-yellow)',
          red: 'var(--status-red)',
        },
        gauge: {
          track: 'var(--gauge-track)',
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
        card: '0 1px 0 rgba(255,255,255,0.03) inset, 0 8px 24px rgba(0,0,0,0.4)',
        sub: '0 1px 0 rgba(255,255,255,0.02) inset, 0 4px 12px rgba(0,0,0,0.3)',
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
