import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  // Servita dietro tailscale serve --set-path=/tax
  base: "/",
  plugins: [react()],
  server: {
    port: 5173,
    // host: true espone Vite su tutte le interfacce di rete (0.0.0.0),
    // così da mobile/tablet sulla stessa WiFi puoi raggiungere il dev server.
    // Il proxy /api → backend resta server-side: il browser mobile parla solo
    // con Vite, niente problemi CORS, niente bisogno di esporre uvicorn.
    host: true,
    // Permette accesso da tunnel pubblici (Cloudflare quick tunnel = sottodominio
    // random di trycloudflare.com, ngrok = ngrok-free.app, localhost = locale,
    // localtunnel = loca.lt). Vite blocca host non whitelistati per difesa
    // anti-DNS-rebinding: in dev locale è ok aprire questi.
    allowedHosts: [
      "localhost",
      ".trycloudflare.com",
      ".ngrok-free.app",
      ".ngrok.io",
      ".loca.lt",
    ],
    proxy: {
      "/api": "http://localhost:8000",
    },
  },
});
