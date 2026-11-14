import type { NextConfig } from 'next';

const useFixtures = process.env.NEXT_PUBLIC_USE_FIXTURES === '1';

const nextConfig: NextConfig = {
  transpilePackages: ['@ithaca/schema'],
  // The dev badge would end up in thumbnails rendered from /render/layout in development.
  devIndicators: false,
  // The API cuts scan photos on first request (a 4k defurnished sweep takes up to a minute); the
  // proxy's default 30 s would drop it.
  experimental: { proxyTimeout: 120_000 },
  // Let a phone on the LAN load the dev server (comma-separated hosts, e.g. 192.168.1.20).
  // Quick tunnels (scripts/phone-tunnel.sh) are always allowed.
  allowedDevOrigins: ['*.trycloudflare.com', ...(process.env.DEV_ALLOWED_ORIGINS?.split(',').map((s) => s.trim()).filter(Boolean) ?? [])],
  async rewrites() {
    if (useFixtures) return [];
    const target = (process.env.API_PROXY_TARGET ?? 'http://localhost:8000').replace(/\/$/, '');
    // The session socket goes through the same origin too, so a phone (or a tunnel) needs only this port.
    return [
      { source: '/api/:path*', destination: `${target}/api/:path*` },
      { source: '/ws/:path*', destination: `${target}/ws/:path*` },
    ];
  },
};

export default nextConfig;
