import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base: "./" so the built bundle works from any static serve path, fully offline.
export default defineConfig({
  plugins: [react()],
  base: "./",
});
