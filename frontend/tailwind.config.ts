import type { Config } from "tailwindcss";

export default {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { 900: "#0f1a24", 800: "#14202b", 700: "#1b2a38", 600: "#25384a", 500: "#33485c" },
        fog: { 100: "#e6edf3", 300: "#b7c5d1", 500: "#8fa3b5", 700: "#5d7282" },
        amber: { DEFAULT: "#f2a93b", soft: "#f7c46f" },
        teal: { DEFAULT: "#3fc1c9" },
        risk: { high: "#e4573d", medium: "#f2a93b", low: "#e9d46a", none: "#6faf8e" },
      },
      fontFamily: {
        sans: ["'IBM Plex Sans'", "'Segoe UI'", "system-ui", "sans-serif"],
      },
      boxShadow: {
        panel: "0 18px 50px -20px rgba(0,0,0,.7), 0 0 0 1px rgba(255,255,255,.04)",
      },
    },
  },
  plugins: [],
} satisfies Config;
