/**
 * Talking to the API.
 *
 * Everything the screens know about the backend is in this file and in the
 * types below. The base URL is configuration, because the API is a separate
 * service on a different host in production (chapter 10).
 */

export const API_BASE = process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000/api/v1";

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

  constructor(code: string, message: string, requestId: string | null) {
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

export async function uploadSong(file: File): Promise<UploadResult> {
  const body = new FormData();
  body.append("file", file);
  // No Content-Type header: the browser has to set it, because only the browser
  // knows the multipart boundary it generated.
  return request<UploadResult>("/songs/upload", { method: "POST", body });
}
