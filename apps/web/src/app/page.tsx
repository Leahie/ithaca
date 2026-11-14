import { redirect } from 'next/navigation';
import { config } from '@/lib/config';
import { OpenPropertyForm } from './OpenPropertyForm';

export default function Home() {
  if (config.defaultPropertyId) redirect(`/house/${encodeURIComponent(config.defaultPropertyId)}`);
  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col justify-center gap-6 p-6">
      <div>
        <div className="flex items-center gap-2 text-lg font-semibold">
          <span className="h-3 w-3 rounded-full bg-sun" />
          Ithaca
        </div>
        <p className="mt-2 text-sm leading-6 text-muted">Live in a home before you buy it. Open a property by its id, or set NEXT_PUBLIC_DEFAULT_PROPERTY_ID to skip this page.</p>
      </div>
      <OpenPropertyForm />
    </main>
  );
}
