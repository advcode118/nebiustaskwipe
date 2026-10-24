/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        glass: "#152028",
        pane: "#1c2b35",
        ink: "#e7eee8",
        mute: "#9aada4",
        line: "#2c414c",
        wipe: "#7eb8a4",
        record: "#d4564e",
        warn: "#e0b25a",
      },
      fontFamily: {
        sans: ["Bricolage Grotesque", "Trebuchet MS", "sans-serif"],
      },
    },
  },
  plugins: [],
};
