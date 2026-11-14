import { JoinFamily } from '@/components/remote/JoinFamily';
import { config } from '@/lib/config';

export const metadata = { title: 'Join the family · Ithaca' };

/** A family member's phone. The family QR code is /join/{invite_token}?p={property_id}. */
export default async function JoinPage({ params, searchParams }: { params: Promise<{ token: string }>; searchParams: Promise<{ p?: string }> }) {
  const { token } = await params;
  const { p } = await searchParams;
  return <JoinFamily inviteToken={decodeURIComponent(token)} propertyId={p ?? config.defaultPropertyId} />;
}
