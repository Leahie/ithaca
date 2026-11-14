import { LayoutRenderLoader } from './loader';

/** Render page for the thumbnail job (WORKSTREAMS.md section 2.6). No app chrome. */
export default async function RenderLayoutPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <LayoutRenderLoader layoutId={decodeURIComponent(id)} />;
}
