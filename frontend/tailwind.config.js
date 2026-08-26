/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#1a1a18", soft: "#5f5e5a", faint: "#8a8984" },
        line: { DEFAULT: "#e4e2dc", strong: "#d3d1c7" },
        surface: { DEFAULT: "#ffffff", sunk: "#faf9f6" },
        accent: { DEFAULT: "#1d7a5f", soft: "#e6f2ee", deep: "#0f5741" },
      },
      fontFamily: {
        sans: ['-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', 'Roboto',
               '"Noto Sans TC"', '"PingFang TC"', '"Microsoft JhengHei"', 'sans-serif'],
        mono: ['"SF Mono"', 'ui-monospace', 'Menlo', 'monospace'],
      },
    },
  },
  plugins: [],
}
