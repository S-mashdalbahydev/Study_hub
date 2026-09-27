import type { NextConfig } from "next";
const config: NextConfig = {
  output: "standalone",
  // Flush proxied SSE deltas immediately; gzip can buffer short tutor replies.
  compress: false,
  experimental: { proxyTimeout: 600000, proxyClientMaxBodySize: "130mb" },
  async rewrites() {
    return [
      {
        source: "/backend/:path*",
        destination: `${process.env.STUDY_API_INTERNAL_URL || "http://127.0.0.1:8000"}/:path*`,
      },
    ];
  },
};
export default config;
