import type { Config } from 'tailwindcss';

const config: Config = {
  content: [
    './pages/**/*.{js,ts,jsx,tsx,mdx}',
    './components/**/*.{js,ts,jsx,tsx,mdx}',
    './app/**/*.{js,ts,jsx,tsx,mdx}',
  ],
  theme: {
    extend: {
      colors: {
        bg: '#07070b',
        surface: '#0f0f14',
        border: '#1c1c26',
        cyan: '#00c8ff',
        orange: '#ff5c35',
        'text-primary': '#e8e8f0',
        'text-muted': '#6b6b80',
      },
      fontFamily: {
        syne: ['Syne', 'sans-serif'],
        mono: ['JetBrains Mono', 'monospace'],
        outfit: ['Outfit', 'sans-serif'],
      },
      borderRadius: {
        DEFAULT: '2px',
        sm: '2px',
        md: '2px',
        lg: '2px',
        xl: '2px',
        '2xl': '4px',
      },
      boxShadow: {
        'cyan-glow': '0 0 20px rgba(0, 200, 255, 0.15), 0 0 40px rgba(0, 200, 255, 0.05)',
        'cyan-glow-strong': '0 0 30px rgba(0, 200, 255, 0.3), 0 0 60px rgba(0, 200, 255, 0.1)',
        'orange-glow': '0 0 20px rgba(255, 92, 53, 0.15)',
      },
      animation: {
        'pulse-cyan': 'pulse-cyan 2s ease-in-out infinite',
        'grid-fade': 'grid-fade 3s ease-in-out infinite',
        'slide-in': 'slide-in 0.3s ease-out',
        'fade-in': 'fade-in 0.4s ease-out',
      },
      keyframes: {
        'pulse-cyan': {
          '0%, 100%': { boxShadow: '0 0 20px rgba(0, 200, 255, 0.15)' },
          '50%': { boxShadow: '0 0 40px rgba(0, 200, 255, 0.35)' },
        },
        'slide-in': {
          from: { transform: 'translateY(-10px)', opacity: '0' },
          to: { transform: 'translateY(0)', opacity: '1' },
        },
        'fade-in': {
          from: { opacity: '0' },
          to: { opacity: '1' },
        },
      },
    },
  },
  plugins: [],
};

export default config;
