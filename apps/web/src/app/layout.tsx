import type { Metadata, Viewport } from 'next';
import { DM_Mono, Figtree, Source_Serif_4 } from 'next/font/google';
import type { ReactNode } from 'react';
import './globals.css';

// The design language's three faces: Source Serif for headings, Figtree for body text and
// controls, DM Mono for measurements. Exposed as CSS variables that globals.css maps to
// Tailwind's font-display, font-sans and font-mono.
const serif = Source_Serif_4({ subsets: ['latin'], weight: ['500', '600'], variable: '--font-serif-face', display: 'swap' });
const figtree = Figtree({ subsets: ['latin'], weight: ['400', '500', '600'], variable: '--font-figtree', display: 'swap' });
const dmMono = DM_Mono({ subsets: ['latin'], weight: ['400', '500'], variable: '--font-dm-mono', display: 'swap' });

export const metadata: Metadata = {
  title: 'Ithaca',
  description: 'Live in a home before you buy it: sun, sound, layout and neighborhood on a digital twin.',
  applicationName: 'Ithaca',
  appleWebApp: { capable: true, title: 'Ithaca', statusBarStyle: 'default' },
};

export const viewport: Viewport = {
  themeColor: '#f6f9f7',
  width: 'device-width',
  initialScale: 1,
  viewportFit: 'cover',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en" className={`${serif.variable} ${figtree.variable} ${dmMono.variable}`}>
      <body>{children}</body>
    </html>
  );
}
