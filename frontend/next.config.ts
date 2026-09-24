import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Eigenständiger Server-Build für ein schlankes Produktions-Image (siehe Dockerfile).
  output: "standalone",
  reactStrictMode: true,
};

export default nextConfig;
