import { config } from '@/lib/config';
import { createFixtureApi } from './fixture';
import { createHttpApi } from './http';
import type { ApiClient } from './types';

export * from './types';

let client: ApiClient | null = null;

/** The app-wide API client: fixtures when NEXT_PUBLIC_USE_FIXTURES=1, HTTP otherwise. */
export function api(): ApiClient {
  client ??= config.useFixtures ? createFixtureApi() : createHttpApi();
  return client;
}
