/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  transpilePackages: ['react-force-graph-3d', 'three', 'three-render-objects', '3d-force-graph', 'force-graph'],
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
