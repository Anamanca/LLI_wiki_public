/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  serverExternalPackages: ['three', 'react-force-graph-3d'],
  experimental: {
    proxyTimeout: 300_000,
  },
  async rewrites() {
    return {
      beforeFiles: [],
      afterFiles: [
        {
          source: '/api/:path*',
          destination: 'http://backend-v2.llm-wiki.svc.cluster.local:8000/api/:path*',
        },
      ],
      fallback: [],
    };
  },
};

module.exports = nextConfig;
