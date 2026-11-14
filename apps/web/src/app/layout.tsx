import type { Metadata, Viewport } from 'next';
import type { ReactNode } from 'react';
import './globals.css';

export const metadata: Metadata = {
  title: 'Ithaca',
  description: 'Live in a home before you buy it: sun, sound, layout and neighborhood on a digital twin.',
  applicationName: 'Ithaca',
  appleWebApp: { capable: true, title: 'Ithaca', statusBarStyle: 'black-translucent' },
};

export const viewport: Viewport = {
  themeColor: '#0e0d0b',
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
