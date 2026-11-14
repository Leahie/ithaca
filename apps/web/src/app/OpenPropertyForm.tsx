'use client';

import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { Button } from '@/components/ui';

export function OpenPropertyForm() {
  const router = useRouter();
  const [id, setId] = useState('');
  return (
    <form
      className="flex gap-2"
      onSubmit={(e) => {
        e.preventDefault();
        if (id.trim()) router.push(`/house/${encodeURIComponent(id.trim())}`);
      }}
    >
      <input
        value={id}
        onChange={(e) => setId(e.target.value)}
        placeholder="Property id"
        aria-label="Property id"
        className="h-9 flex-1 rounded-md border border-line bg-surface px-3 text-sm placeholder:text-faint focus:border-line-strong focus:outline-none"
      />
      <Button type="submit" variant="primary" disabled={!id.trim()}>
        Open
      </Button>
    </form>
  );
}
