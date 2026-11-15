import { config } from '@/lib/config';

const KEY = 'ithaca.house';

/** Remember the house this tab has open, so other pages can lead back to it. */
export function rememberHouse(propertyId: string): void {
  try {
    window.sessionStorage.setItem(KEY, propertyId);
  } catch {
    /* the default property is used instead */
  }
}

/** The house this tab had open last, else the configured default, else none (''). */
export function lastHouse(): string {
  try {
    return window.sessionStorage.getItem(KEY) || config.defaultPropertyId;
  } catch {
    return config.defaultPropertyId;
  }
}

export const houseHref = (propertyId: string): string => (propertyId ? `/house/${encodeURIComponent(propertyId)}` : '/');
