import type { MetadataRoute } from 'next';

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Ithaca',
    short_name: 'Ithaca',
    description: 'Second-screen controller and viewer for a home digital twin.',
    start_url: '/',
    display: 'standalone',
    orientation: 'portrait',
    background_color: '#f6f9f7',
    theme_color: '#f6f9f7',
    icons: [{ src: '/icon.svg', sizes: 'any', type: 'image/svg+xml', purpose: 'any' }],
  };
}
