import { HouseExperience } from '@/components/house/HouseExperience';

export default async function HousePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  return <HouseExperience propertyId={decodeURIComponent(id)} />;
}
