/**
 * Who API calls are made as (X-User-Id, I-G18). The desktop is `config.userId`; a phone that
 * joined a household speaks for its member, so its uploads and memory are that member's own.
 */
import { config } from '@/lib/config';

let override: string | null = null;

export function setApiUser(userId: string | null): void {
  override = userId;
}

export function apiUserId(): string {
  return override ?? config.userId;
}
