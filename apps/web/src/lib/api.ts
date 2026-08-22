/**
 * Talking to the API.
 *
 * Everything the screens know about the backend is in this file and in the
 * types below. The base URL is configuration, because the API is a separate
 * service on a different host in production (chapter 10).
 */

/**
 * `127.0.0.1`, not `localhost`, and the difference is not cosmetic.
 *
 * Server components fetch through Node, and Node resolves `localhost` to `::1`
 * first. `python -m apps.api` binds `127.0.0.1` only (uvicorn's default), so a
 * server-rendered page against `localhost` hangs until it times out and the
 * screen shows "the service is unavailable" while `curl` from the same machine
 * answers instantly. The container publishes on both stacks, which is why this
 * only ever bites the venv setup - the one used to develop.
 */
export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000/api/v1";

export type JobState = "queued" | "running" | "ready" | "failed";

export type JobStep =
  | "ingesting"
  | "separating"
  | "transcribing_mix"
  | "transcribing_vocals"
  | "encoding"
  | "aligning";

export interface SongJob {
  id: string;
  state: JobState;
  current_step: JobStep | null;
  progress: number;
  error_code: string | null;
}

export interface LibrarySong {
  id: string;
  title: string;
  artist: string | null;
  duration_sec: number | null;
  status: "pending" | "processing" | "ready" | "failed";
  /**
   * D-28. Separate from `status` on purpose: a song can be playable while it is
   * still being processed, and that is the whole point of staged readiness.
   */
  is_playable: boolean;
  lyrics_status: "pending" | "line" | "word" | "missing";
  created_at: string;
  job: SongJob | null;
}

export interface Library {
  songs: LibrarySong[];
  total: number;
}

/** The shape every error from this API has, since T-1.2. */
export interface ApiErrorBody {
  error: { code: string; message: string };
  request_id: string;
}

export class ApiError extends Error {
  /** The code the dictionary turns into Hebrew. */
  readonly code: string;
  readonly requestId: string | null;

  constructor(code: string, message: string, requestId: string | null = null) {
    super(message);
    this.code = code;
    this.requestId = requestId;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      // The library changes whenever a job finishes, so a cached page would show
      // a song as "processing" long after it was ready.
      cache: "no-store",
    });
  } catch (cause) {
    // A network failure has no code of its own; give it one the dictionary
    // knows, so the screen shows a sentence rather than a stack trace.
    throw new ApiError("database_unavailable", String(cause), null);
  }

  if (!response.ok) {
    const requestId = response.headers.get("X-Request-ID");
    let body: Partial<ApiErrorBody> = {};
    try {
      body = (await response.json()) as ApiErrorBody;
    } catch {
      // A response that is not our error shape at all - a proxy's 502 page,
      // say. `unknown` is in the dictionary for exactly this.
    }
    throw new ApiError(
      body.error?.code ?? "unknown",
      body.error?.message ?? response.statusText,
      body.request_id ?? requestId,
    );
  }

  return (await response.json()) as T;
}

export interface JobStatus {
  id: string;
  song_id: string;
  state: JobState;
  current_step: JobStep | null;
  progress: number;
  is_playable: boolean;
  error_code: string | null;
  attempts: number;
  gpu_seconds: number | null;
}

export interface UploadResult {
  id: string;
  title: string;
  duration_sec: number;
  status: string;
  is_playable: boolean;
  sample_rate: number;
  channels: number;
  already_existed: boolean;
  job_id: string | null;
}

export function getLibrary(): Promise<Library> {
  return request<Library>("/songs");
}

export interface StemLink {
  kind: "vocals" | "drums" | "bass" | "other";
  url: string;
  format: string;
  bytes: number;
}

export interface PlayerSettings {
  key_shift: number;
  tempo_ratio: number;
  stem_volumes: Record<string, number> | null;
  lyric_offset_ms: number;
}

export interface SongDetail {
  id: string;
  title: string;
  artist: string | null;
  duration_sec: number | null;
  status: LibrarySong["status"];
  is_playable: boolean;
  lyrics_status: LibrarySong["lyrics_status"];
  original_key: string | null;
  bpm: number | null;
  stems: StemLink[];
  /** Saved settings, or the defaults. Sent with the song so opening one is a
   *  single request rather than two. */
  settings: PlayerSettings;
}

export function getSong(songId: string): Promise<SongDetail> {
  return request<SongDetail>(`/songs/${songId}`);
}

export interface LyricWord {
  text: string;
  start_ms: number;
  end_ms: number | null;
}

export interface LyricLine {
  index: number;
  text: string;
  /** Milliseconds from the start of the song. Null on a line nobody timed. */
  start_ms: number | null;
  /**
   * Null when the end is not known - T-2.5 says so rather than guessing, and
   * the player shows such a line until the next one starts.
   */
  end_ms: number | null;
  /** Empty unless the alignment was confident enough to keep them (D-09). */
  words: LyricWord[];
}

export interface SongLyrics {
  song_id: string;
  version: number;
  language: string;
  source: "db" | "mix_asr" | "vocals_asr" | "manual";
  is_verified: boolean;
  status: LibrarySong["lyrics_status"];
  lines: LyricLine[];
  versions: { version: number; source: string; language: string; created_at: string }[];
  created_at: string;
}

/** The 202 body: the pipeline is still working on the words. */
export interface LyricsPending {
  song_id: string;
  status: "pending";
  detail: string;
}

export function isPending(body: SongLyrics | LyricsPending): body is LyricsPending {
  return !("lines" in body);
}

/**
 * The words, or "not yet".
 *
 * D-28 opens the player before the lyrics exist, so 202 is a normal answer to a
 * normal request and not an error - which is why this returns a union rather
 * than throwing. The player polls while it is pending and the words appear
 * mid-song, which is chapter 8's "lyrics on the way" state.
 */
export function getLyrics(
  songId: string,
  version?: number,
): Promise<SongLyrics | LyricsPending> {
  const query = version === undefined ? "" : `?version=${version}`;
  return request<SongLyrics | LyricsPending>(`/songs/${songId}/lyrics${query}`);
}

/** One line on its way back to the API. No `index`: the order is the order. */
export interface LyricLineIn {
  text: string;
  start_ms: number | null;
  end_ms: number | null;
  words: LyricWord[];
}

/**
 * Save an edited set of words (T-2.8).
 *
 * A `PUT` that answers 201, because chapter 6 says an edit creates a version
 * and never overwrites one. Nothing is lost by saving: the version being edited
 * is still readable at `?version=` afterwards.
 */
export function saveLyrics(
  songId: string,
  lines: LyricLineIn[],
  language = "he",
): Promise<SongLyrics> {
  return request<SongLyrics>(`/songs/${songId}/lyrics`, {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ lines, language, source: "manual" }),
  });
}

/**
 * A signed link, made absolute.
 *
 * With the object store the API hands out absolute URLs at the bucket; with the
 * local backend it hands out root-relative ones at the API. Neither the player
 * nor the upload screen should care which it was given, so both go through
 * here.
 */
export function signedUrl(url: string): string {
  return url.startsWith("http") ? url : `${API_BASE.replace(/\/api\/v1$/, "")}${url}`;
}

export function stemUrl(link: StemLink): string {
  return signedUrl(link.url);
}

export function saveSettings(
  songId: string,
  settings: PlayerSettings,
): Promise<PlayerSettings> {
  return request<PlayerSettings>(`/songs/${songId}/settings`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(settings),
  });
}

export function getJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/jobs/${jobId}`);
}

export function retryJob(jobId: string): Promise<JobStatus> {
  return request<JobStatus>(`/jobs/${jobId}/retry`, { method: "POST" });
}

/** The SSE endpoint. EventSource needs a URL, not a fetch. */
export function jobEventsUrl(jobId: string): string {
  return `${API_BASE}/jobs/${jobId}/events`;
}

export interface UploadTicket {
  key: string;
  url: string;
  method: string;
  expires_in: number;
  max_bytes: number;
}

/** Step one of chapter 6's upload: ask where to put the file. */
export function createUploadTicket(file: File): Promise<UploadTicket> {
  return request<UploadTicket>("/songs/upload-url", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, bytes: file.size }),
  });
}

/**
 * Step two: the file goes straight to storage, and the API is not in the path.
 *
 * `XMLHttpRequest` rather than `fetch`, for the one thing fetch still cannot
 * do: report progress while a body is being *sent*. A 30MB upload with no
 * progress is indistinguishable from a hung one, and this is the screen where
 * the wait is longest.
 */
export function putToStorage(
  ticket: UploadTicket,
  file: File,
  onProgress: (fraction: number) => void,
): Promise<void> {
  return new Promise((resolve, reject) => {
    const request = new XMLHttpRequest();
    request.open(ticket.method, signedUrl(ticket.url));
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    request.addEventListener("load", () => {
      // Storage answers with XML, not with this API's error shape, so there is
      // nothing here to translate: any failure is one Hebrew sentence.
      if (request.status >= 200 && request.status < 300) resolve();
      else reject(new ApiError("upload_failed", `storage answered ${request.status}`));
    });
    request.addEventListener("error", () =>
      reject(new ApiError("upload_failed", "the upload did not reach storage")),
    );
    request.addEventListener("abort", () =>
      reject(new ApiError("upload_failed", "the upload was cancelled")),
    );
    request.send(file);
  });
}

/** Step three: make the song from what was uploaded, and start the work. */
export function createSong(uploadKey: string, filename: string): Promise<UploadResult> {
  return request<UploadResult>("/songs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ upload_key: uploadKey, filename }),
  });
}
