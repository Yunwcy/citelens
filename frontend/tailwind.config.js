/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: { DEFAULT: "#1a1a18", soft: "#5f5e5a", faint: "#8a8984" },
        line: { DEFAULT: "#e4e2dc", strong: "#d3d1c7" },
        surface: { DEFAULT: "#ffffff", sunk: "#faf9f6" },
        // 品牌金取自 PEGA AI 識別。金色在白底上僅 1.8:1，遠低於 WCAG AA 的 4.5，
        // 因此拆成兩個角色：gold 只用於深底（頁首、標記），
        // 白底上的文字與互動元件一律使用加深後的 deep（6.2:1）。
        accent: {
          DEFAULT: "#7A5C18",   // 白底上的互動色　6.2:1
          deep: "#5E460F",      // 強調文字　　　　 9.1:1
          soft: "#FBF6E9",      // 淡金填色
          line: "#E8DCBE",      // 淡金邊框
          gold: "#DDBE6E",      // 展示金：僅限深底　9.7:1
        },
        brand: { DEFAULT: "#111110", soft: "#26251F" },
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
