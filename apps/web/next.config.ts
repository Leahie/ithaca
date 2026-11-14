import type { NextConfig } from 'next';

const useFixtures = process.env.NEXT_PUBLIC_USE_FIXTURES === '1';

const nextConfig: NextConfig = {
  transpilePackages: ['@ithaca/schema'],
  // The dev badge would end up in thumbnails rendered from /render/layout in development.
  devIndicators: false,
  // Let a phone on the LAN load the dev server (comma-separated hosts, e.g. 192.168.1.20).
  allowedDevOrigins: process.env.DEV_ALLOWED_ORIGINS?.split(',').map((s) => s.trim()).filter(Boolean),
  async rewrites() {
    if (useFixtures) return [];
    const target = (process.env.API_PROXY_TARGET ?? 'http://localhost:8000').replace(/\/$/, '');
    return [{ source: '/api/:path*', destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
