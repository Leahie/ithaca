import type {
  CatalogPiece,
  AreaProfile,
  Household,
  InventoryItem,
  JoinHouseholdResult,
  JobRef,
  HousePiece,
  LayoutVariant,
  PlacePiece,
  NoiseReport,
  NoiseScenario,
  Ok,
  Amenity,
  SoundCatalog,
  SoundField,
  SoundPlaceSourcePayload,
  Opening,
  PropertyTwin,
  Session,
  SunReport,
  UploadResult,
  PhotoPoint,
  JobProgressPayload,
  FurnishRoomRequest,
  LibraryLook,
  LibraryPage,
} from '@ithaca/schema';
import type { Board, BoardEntry, BoardEntryCreate } from '@ithaca/schema';
import type { ResetCounts, ResetResult } from '@ithaca/schema';

/** What to list from the furniture library. Every field narrows; none lists everything. */
export interface LibraryQuery {
  /** Free words: names, kinds, colours, materials, looks. */
  q?: string;
  /** A key from the page's `categories`. */
  category?: string | null;
  /** A key from the page's `sources`. */
  source?: string | null;
  /** One design's key: all its colours and sizes, which are otherwise listed once. */
  family?: string | null;
  offset?: number;
  limit?: number;
}

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
  /** Places further out than the area profile lists, by free text ("coffee", "bus", "Kroger"). */
  searchArea(propertyId: string, query: string): Promise<Amenity[]>;
  /** The sounds that can be placed in the house, each with its level at 1 m. */
  getSoundPresets(propertyId: string): Promise<SoundCatalog>;
  /** How a sound placed in one room carries to every room, loudest first. */
  getSoundField(propertyId: string, source: SoundPlaceSourcePayload, doors: 'closed' | 'open'): Promise<SoundField>;
  ingestProperty(propertyId: string): Promise<JobRef>;

  // Stream B
  /** householdId: the family whose phones may join through the family QR code. */
  createSession(opts?: { propertyId?: string; householdId?: string }): Promise<Session>;
  /** fresh: a new family was asked for, so the host's current one is set aside instead of reused. */
  createHousehold(name: string, fresh?: boolean): Promise<Household>;
  getHousehold(householdId: string): Promise<Household>;
  /** A new family QR code; the old one stops working. */
  /** The screen leaves its family: the next one starts with an empty house. */
  leaveHousehold(): Promise<void>;
  rotateInvite(householdId: string): Promise<Household>;
  /** A phone joins the family. memberId: the member it joined as before, to come back as them. */
  joinHousehold(inviteToken: string, name: string, memberId?: string): Promise<JoinHouseholdResult>;
  joinSession(sessionId: string, pairToken: string): Promise<Ok>;
  upload(file: Blob, filename: string): Promise<UploadResult>;
  /** A background job's latest progress, for when its `job.progress` events don't reach this client. */
  getJob(jobId: string): Promise<JobProgressPayload>;
  /** at: the spot tapped on the photo (fractions of its size); only the thing there becomes a piece. */
  ingestObject(uploadId: string, at?: PhotoPoint): Promise<JobRef>;
  /**
   * A photo of the person's own room: everything of theirs in it is rebuilt in 3D and set up in
   * that room of the house (what stood there goes back to storage). A job: follow its progress.
   */
  furnishFromPhoto(propertyId: string, uploadId: string, roomId: string): Promise<JobRef>;
  // Furniture: docs/specs/furniture.md.
  /** Storage: the family's pieces (each with its owner). */
  listInventory(): Promise<InventoryItem[]>;
  getInventoryItem(itemId: string): Promise<InventoryItem>;
  /** Delete a piece for good; it leaves the house too. Only its owner (or the host) may. */
  deleteInventoryItem(itemId: string): Promise<void>;
  /** In the house: every piece standing in it, with its room and pose. */
  listHouseFurniture(propertyId: string): Promise<HousePiece[]>;
  /**
   * Place a piece, or move it. The server finds the room from the spot. By hand (`snap: false`)
   * any spot inside a room is saved, with `warnings`; `snap: true` takes the nearest valid spot.
   * 409 when the spot is in no room (or, snapping, nothing fits).
   */
  placePiece(propertyId: string, itemId: string, place: PlacePiece): Promise<HousePiece>;
  /** Take a piece out of the house. It stays in storage. */
  removePiece(propertyId: string, itemId: string): Promise<void>;
  /** Proposals: arrangements suggested for one room. Never furniture until kept. */
  listLayouts(propertyId: string, roomId?: string): Promise<LayoutVariant[]>;
  /**
   * One layout by id, for the render page (section 2.6). REST v0 has no such route yet;
   * the HTTP client asks for `GET /layouts/{id}`. See docs/stream-a.md, "Contract gaps".
   */
  getLayout(layoutId: string): Promise<LayoutVariant>;
  /** Keep a proposal: its room's furniture becomes exactly its pieces. */
  selectLayout(layoutId: string): Promise<LayoutVariant>;
  layoutFeedback(layoutId: string, feedback: string): Promise<LayoutVariant>;
  /** Up to three proposed layouts that add one piece to a room, each at a different spot. */
  placementOptions(ask: { property_id: string; room_id: string; item_id: string }): Promise<LayoutVariant[]>;
  /** Ready-made furniture that can be added without a photo. */
  listCatalog(): Promise<CatalogPiece[]>;
  addFromCatalog(catalogId: string): Promise<InventoryItem>;
  /** The furniture library: thousands of pieces from open 3D collections, a page at a time, with the menu's counts. */
  listLibrary(query?: LibraryQuery): Promise<LibraryPage>;
  /** Add a library piece to the family's furniture. Its model is downloaded on first use (5 to 20 seconds). */
  addFromLibrary(libraryId: string): Promise<InventoryItem>;
  /** The looks a room can be furnished in. */
  listLooks(): Promise<LibraryLook[]>;
  /**
   * The designer furnishes one room in a look: it picks pieces from the library, adds them and
   * arranges them. A job: follow its progress. When it is done the server shows the options on
   * every screen, to keep one.
   */
  furnishRoom(propertyId: string, body: FurnishRoomRequest): Promise<JobRef>;
  listMemory(): Promise<string[]>;
  getMemory(path: string): Promise<string>;
  putMemory(path: string, text: string): Promise<void>;
  deleteMemory(path: string): Promise<void>;

  // Reset everything: delete all data, for everyone, and start from scratch. Big screen only (a member's phone gets 403).
  /** What a reset would delete, as counts. Deletes nothing. */
  resetPreview(): Promise<ResetCounts>;
  /**
   * Delete everything. Resolves with what was deleted; `ok` is false and `failed` names the parts
   * when some of it could not be. Every screen is sent `app.reset` and its session ends.
   */
  resetEverything(): Promise<ResetResult>;

  // The family board: what each person wants from a home (packages/schema/board.py).
  /**
   * Everyone's cards, with the room wishes for one house. `householdId`: the family on this big
   * screen (none: the user's own board, with one card). A member's phone gets its family's board.
   */
  getBoard(opts: { propertyId?: string | null; householdId?: string | null }): Promise<Board>;
  /** A line written by hand. The big screen may write on any card, a member only on their own (403). */
  addBoardEntry(entry: BoardEntryCreate): Promise<BoardEntry>;
  updateBoardEntry(entryId: string, text: string): Promise<BoardEntry>;
  deleteBoardEntry(entryId: string): Promise<void>;

  /** Absolute URL for an asset URL the API returned (for loaders that need one). */
  assetUrl(url: string): string;
}
