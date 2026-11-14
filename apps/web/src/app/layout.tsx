import type { Metadata, Viewport } from 'next';
import { DM_Mono, Figtree, Fredoka } from 'next/font/google';
import type { ReactNode } from 'react';
import './globals.css';

// The design language's three faces: Fredoka for display and anything read mid-gesture, Figtree
// for body text, DM Mono for measurements. Exposed as CSS variables that globals.css maps to
// Tailwind's font-display, font-sans and font-mono.
const fredoka = Fredoka({ subsets: ['latin'], weight: ['500', '600'], variable: '--font-fredoka', display: 'swap' });
const figtree = Figtree({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-figtree', display: 'swap' });
const dmMono = DM_Mono({ subsets: ['latin'], weight: ['400', '500'], variable: '--font-dm-mono', display: 'swap' });

export const metadata: Metadata = {
  title: 'Ithaca',
  description: 'Live in a home before you buy it: sun, sound, layout and neighborhood on a digital twin.',
  applicationName: 'Ithaca',
  appleWebApp: { capable: true, title: 'Ithaca', statusBarStyle: 'default' },
};

export const viewport: Viewport = {
  themeColor: '#fbf3e4',
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${fredoka.variable} ${figtree.variable} ${dmMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
