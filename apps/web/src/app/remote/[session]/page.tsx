import { RemoteController } from '@/components/remote/RemoteController';
import { config } from '@/lib/config';

export const metadata = { title: 'Remote · Ithaca' };

/** Phone controller. The pairing link is /remote/{session_id}?t={pair_token}&p={property_id}. */
export default async function RemotePage({ params, searchParams }: { params: Promise<{ session: string }>; searchParams: Promise<{ t?: string; p?: string }> }) {
  const { session } = await params;
  const { t, p } = await searchParams;
  return <RemoteController sessionId={decodeURIComponent(session)} token={t ?? ''} propertyId={p ?? config.defaultPropertyId} />;
}
