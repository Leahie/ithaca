import type {
  AreaProfile,
  Household,
  InventoryItem,
  JoinHouseholdResult,
  JobRef,
  LayoutVariant,
  NoiseReport,
  NoiseScenario,
  Ok,
  Opening,
  PropertyTwin,
  Session,
  SunReport,
  UploadResult,
} from '@ithaca/schema';

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    /** I-G21: the backend feature exists but has no key configured. */
    readonly notConfigured = false,
  ) {
    super(message);
    this.name = 'ApiError';
  }
}

/**
 * The one typed client for every server call (A7). All payload types come from the
 * generated schema (I-G5). Two implementations: HTTP and fixtures (A6).
 */
export interface ApiClient {
  readonly mode: 'http' | 'fixture';

  // Stream C
  getTwin(propertyId: string): Promise<PropertyTwin>;
  putOpenings(propertyId: string, openings: Opening[]): Promise<Opening[]>;
  getSun(propertyId: string, roomId: string, start: string, end: string): Promise<SunReport>;
  getNoise(propertyId: string, roomId: string, scenario: NoiseScenario): Promise<NoiseReport>;
  getArea(propertyId: string): Promise<AreaProfile>;
  ingestProperty(propertyId: string): Promise<JobRef>;

  // Stream B
  /** householdId: the family whose phones may join through the family QR code. */
  createSession(opts?: { propertyId?: string; householdId?: string }): Promise<Session>;
  createHousehold(name: string): Promise<Household>;
  getHousehold(householdId: string): Promise<Household>;
  /** A new family QR code; the old one stops working. */
  rotateInvite(householdId: string): Promise<Household>;
  /** A phone joins the family. memberId: the member it joined as before, to come back as them. */
  joinHousehold(inviteToken: string, name: string, memberId?: string): Promise<JoinHouseholdResult>;
  joinSession(sessionId: string, pairToken: string): Promise<Ok>;
  upload(file: Blob, filename: string): Promise<UploadResult>;
  ingestObject(uploadId: string): Promise<JobRef>;
  listInventory(): Promise<InventoryItem[]>;
  getInventoryItem(itemId: string): Promise<InventoryItem>;
  deleteInventoryItem(itemId: string): Promise<void>;
  listLayouts(propertyId: string, roomId?: string): Promise<LayoutVariant[]>;
  /**
   * One layout by id, for the render page (section 2.6). REST v0 has no such route yet;
   * the HTTP client asks for `GET /layouts/{id}`. See docs/stream-a.md, "Contract gaps".
   */
  getLayout(layoutId: string): Promise<LayoutVariant>;
  selectLayout(layoutId: string): Promise<LayoutVariant>;
  layoutFeedback(layoutId: string, feedback: string): Promise<LayoutVariant>;
  listMemory(): Promise<string[]>;
  getMemory(path: string): Promise<string>;
  putMemory(path: string, text: string): Promise<void>;
  deleteMemory(path: string): Promise<void>;

  /** Absolute URL for an asset URL the API returned (for loaders that need one). */
  assetUrl(url: string): string;
}
