import type { ListingFacts } from '@ithaca/schema';

export const money = (n: number) => `$${n.toLocaleString('en-US')}`;

/** The home's own listing as label and value pairs: what it costs and what you get. */
export function listingFacts(l: ListingFacts): { label: string; value: string }[] {
  const price = l.price != null ? money(l.price) : null;
  return [
    price && { label: l.price_kind === 'sale' ? 'Sold for' : l.price_kind === 'last_asking' ? 'Last asking price' : 'Asking price', value: price },
    l.beds != null && { label: 'Bedrooms', value: String(l.beds) },
    l.baths != null && { label: 'Bathrooms', value: String(l.baths) },
    l.sqft != null && { label: 'Size', value: `${l.sqft.toLocaleString('en-US')} sq ft` },
    l.price != null && l.sqft ? { label: 'Per sq ft', value: money(Math.round(l.price / l.sqft)) } : null,
    l.year_built != null && { label: 'Built', value: String(l.year_built) },
    l.lot_acres != null && { label: 'Lot', value: `${l.lot_acres.toFixed(2)} acres` },
    l.hoa_monthly != null && { label: 'HOA', value: `${money(Math.round(l.hoa_monthly))} a month` },
    l.days_on_market != null && { label: 'On the market', value: `${l.days_on_market} day${l.days_on_market === 1 ? '' : 's'}` },
  ].filter((f): f is { label: string; value: string } => !!f);
}
