import { HomePage } from '@/components/home/HomePage';
import { config } from '@/lib/config';

/** The front page. The home on offer is the configured default (I-G19: no property id in code). */
export default function Home() {
  return <HomePage propertyId={config.defaultPropertyId} />;
}
