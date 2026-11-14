'use client';

import dynamic from 'next/dynamic';

const LayoutRender = dynamic(() => import('@/components/render/LayoutRender').then((m) => m.LayoutRender), { ssr: false });

export function LayoutRenderLoader({ layoutId }: { layoutId: string }) {
  return <LayoutRender layoutId={layoutId} />;
}
