/**
 * A13-safe streaming client for the assistant.
 */

import { useAuthStore } from "@/store/useAuthStore";

export interface StartFrame {
  readonly seq: number;
  readonly message_id: string;
  readonly conversation_id: string;
  readonly model: string;
  readonly provider: string;
  readonly passages: number;
  readonly warnings: readonly string[];
  readonly resumable: boolean;
}

export interface TokenFrame {
  readonly seq: number;
  readonly text: string;
}

export interface CitationBoundingBox {
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
  readonly width: number | null;
  readonly height: number | null;
  readonly space: "pixels" | "points" | "normalized";
  readonly page: number | null;
}

export interface CitationSource {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly chunk_id: string;
  readonly chunk_index: number;
  readonly page_number: number | null;
  readonly bbox: CitationBoundingBox | null;
  readonly page_start_char: number | null;
  readonly page_end_char: number | null;
  readonly snippet: string;
  readonly similarity_score: number;
  readonly rank: number;
}

export interface CitationClaim {
  readonly claim_id: string;
  readonly text_span: readonly [number, number];
  readonly sources: readonly CitationSource[];
}

export interface CitationEnvelope {
  readonly seq?: number;
  readonly message_id: string;
  readonly conversation_id: string;
  readonly claims: readonly CitationClaim[];
  readonly context_hash: string | null;
  readonly audit_log_id: string | null;
  readonly model: string | null;
  readonly provider: string | null;
  readonly prompt_version: string | null;
  readonly generated_at: string | null;
  readonly passages_included: number;
  readonly passages_dropped_injection: number;
  readonly passages_dropped_budget: number;
  readonly truncated: boolean;
  readonly finish_reason: string | null;
  readonly usage_estimated: boolean;
}

export interface DoneFrame {
  readonly seq?: number;
  readonly finish_reason: string;
  readonly truncated: boolean;
  readonly usage_estimated: boolean;
  readonly resumed?: boolean;
}

export interface ErrorFrame {
  readonly code: string;
  readonly message: string;
  readonly retry_after?: number;
  readonly detail?: string;
}

export interface StreamHandlers {
  onStart?: (frame: StartFrame) => void;
  onToken?: (text: string, seq: number) => void;
  onCitations?: (envelope: CitationEnvelope) => void;
  onDone?: (frame: DoneFrame) => void;
  onError?: (frame: ErrorFrame) => void;
  onConnectionChange?: (state: ConnectionState) => void;
}

export type ConnectionState =
  | "connecting"
  | "streaming"
  | "reconnecting"
  | "closed";

export interface StreamOptions {
  readonly workspaceId: string;
  readonly conversationId: string;
  readonly content: string;
  readonly handlers: StreamHandlers;
  readonly signal?: AbortSignal;
  readonly maxRetries?: number;
}

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";
const DEFAULT_MAX_RETRIES = 6;
const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 15_000;

const backoffMs = (attempt: number): number => {
  const capped = Math.min(BASE_BACKOFF_MS * 2 ** attempt, MAX_BACKOFF_MS);
  return Math.random() * capped;
};

const sleep = (ms: number, signal?: AbortSignal): Promise<void> =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = () => {
      clearTimeout(timer);
      reject(new DOMException("Aborted", "AbortError"));
    };
    signal?.addEventListener("abort", onAbort, { once: true });
  });

interface ParsedFrame {
  readonly event: string;
  readonly data: unknown;
}

class SseParser {
  private buffer = "";
  private readonly decoder = new TextDecoder("utf-8");

  public push(chunk: Uint8Array): ParsedFrame[] {
    this.buffer += this.decoder.decode(chunk, { stream: true });
    return this.drain();
  }

  public flush(): ParsedFrame[] {
    this.buffer += this.decoder.decode();
    return this.drain();
  }

  private drain(): ParsedFrame[] {
    const frames: ParsedFrame[] = [];
    const normalised = this.buffer.replace(/\r\n/g, "\n");
    const blocks = normalised.split("\n\n");
    this.buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      const frame = SseParser.parseBlock(block);
      if (frame) {
        frames.push(frame);
      }
    }

    return frames;
  }

  private static parseBlock(block: string): ParsedFrame | null {
    let event = "message";
    const dataLines: string[] = [];

    for (const line of block.split("\n")) {
      if (line.length === 0 || line.startsWith(":")) {
        continue;
      }

      const colon = line.indexOf(":");
      const field = colon === -1 ? line : line.slice(0, colon);
      let value = colon === -1 ? "" : line.slice(colon + 1);
      if (value.startsWith(" ")) {
        value = value.slice(1);
      }

      if (field === "event") {
        event = value;
      } else if (field === "data") {
        dataLines.push(value);
      }
    }

    if (dataLines.length === 0) {
      return null;
    }

    try {
      return { event, data: JSON.parse(dataLines.join("\n")) };
    } catch {
      return null;
    }
  }
}

export class StreamNotResumableError extends Error {
  public constructor(message: string) {
    super(message);
    this.name = "StreamNotResumableError";
    Object.setPrototypeOf(this, StreamNotResumableError.prototype);
  }
}

class ResumableStream {
  private lastSeq = 0;
  private messageId: string | null = null;
  private resumable = false;
  private finished = false;
  private errored = false;

  public constructor(private readonly options: StreamOptions) {}

  private get handlers(): StreamHandlers {
    return this.options.handlers;
  }

  private setState(state: ConnectionState): void {
    this.handlers.onConnectionChange?.(state);
  }

  private authHeaders(): Record<string, string> {
    const token = useAuthStore.getState().token;
    return token ? { Authorization: `Bearer ${token}` } : {};
  }

  public async run(): Promise<void> {
    const maxRetries = this.options.maxRetries ?? DEFAULT_MAX_RETRIES;
    let attempt = 0;
    let started = false;

    while (!this.finished && !this.errored) {
      if (this.options.signal?.aborted) {
        this.setState("closed");
        return;
      }

      try {
        this.setState(started ? "reconnecting" : "connecting");

        const response = started
          ? await this.openResume()
          : await this.openGeneration();

        started = true;
        await this.consume(response);

        if (!this.finished && !this.errored) {
          attempt += 1;
          if (attempt > maxRetries) {
            this.fail({
              code: "STREAM_DISCONNECTED",
              message:
                "The connection dropped repeatedly. Reopen the conversation to see what was saved.",
            });
            return;
          }
          await sleep(backoffMs(attempt), this.options.signal);
          continue;
        }

        return;
      } catch (error) {
        if (this.options.signal?.aborted || isAbortError(error)) {
          this.setState("closed");
          return;
        }

        if (error instanceof StreamNotResumableError) {
          this.fail({ code: "STREAM_NOT_RESUMABLE", message: error.message });
          return;
        }

        attempt += 1;
        if (attempt > maxRetries) {
          this.fail({
            code: "NETWORK_ERROR",
            message:
              error instanceof Error
                ? error.message
                : "The connection could not be established.",
          });
          return;
        }

        await sleep(backoffMs(attempt), this.options.signal).catch(() => undefined);
      }
    }
  }

  private async openGeneration(): Promise<Response> {
    const { workspaceId, conversationId, content } = this.options;

    const response = await fetch(
      `${API_URL}/workspaces/${encodeURIComponent(workspaceId)}` +
        `/assistant/conversations/${encodeURIComponent(conversationId)}/messages/stream`,
      {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
          ...this.authHeaders(),
        },
        body: JSON.stringify({ content }),
        ...(this.options.signal ? { signal: this.options.signal } : {}),
      },
    );

    if (!response.ok) {
      await this.rejectFromStatus(response);
    }

    const headerId = response.headers.get("X-Message-Id");
    if (headerId) {
      this.messageId = headerId;
    }

    return response;
  }

  private async openResume(): Promise<Response> {
    if (!this.messageId) {
      throw new StreamNotResumableError(
        "The connection dropped before the answer was identified. Reopen the conversation to check whether it completed.",
      );
    }

    if (!this.resumable) {
      throw new StreamNotResumableError(
        "This answer cannot be resumed. Reopen the conversation to see what was saved.",
      );
    }

    const { workspaceId } = this.options;

    const response = await fetch(
      `${API_URL}/workspaces/${encodeURIComponent(workspaceId)}` +
        `/assistant/messages/${encodeURIComponent(this.messageId)}/stream` +
        `?from_seq=${this.lastSeq}`,
      {
        method: "GET",
        credentials: "include",
        headers: { Accept: "text/event-stream", ...this.authHeaders() },
        ...(this.options.signal ? { signal: this.options.signal } : {}),
      },
    );

    if (response.status === 404) {
      throw new StreamNotResumableError(
        "The saved output for this answer has expired. Reopen the conversation to see what was stored.",
      );
    }

    if (!response.ok) {
      await this.rejectFromStatus(response);
    }

    return response;
  }

  private async rejectFromStatus(response: Response): Promise<never> {
    let message = `The server refused the request (${response.status}).`;
    let code = `HTTP_${response.status}`;

    try {
      const body = (await response.json()) as Record<string, unknown>;
      if (typeof body.message === "string") {
        message = body.message;
      } else if (typeof body.detail === "string") {
        message = body.detail;
      }
      if (typeof body.code === "string") {
        code = body.code;
      }
    } catch {
      // Non-JSON body
    }

    if (response.status === 402 || response.status === 403) {
      this.fail({ code, message });
      throw new DOMException("Aborted", "AbortError");
    }

    if (response.status === 429) {
      const retryAfter = Number(response.headers.get("Retry-After") ?? "");
      this.fail({
        code,
        message,
        ...(Number.isFinite(retryAfter) ? { retry_after: retryAfter } : {}),
      });
      throw new DOMException("Aborted", "AbortError");
    }

    throw new Error(message);
  }

  private async consume(response: Response): Promise<void> {
    if (!response.body) {
      throw new Error("The server returned no response body.");
    }

    const reader = response.body.getReader();
    const parser = new SseParser();

    this.setState("streaming");

    try {
      for (;;) {
        const { done, value } = await reader.read();

        if (done) {
          for (const frame of parser.flush()) {
            this.dispatch(frame);
          }
          return;
        }

        if (value) {
          for (const frame of parser.push(value)) {
            this.dispatch(frame);
            if (this.finished || this.errored) {
              return;
            }
          }
        }
      }
    } finally {
      try {
        reader.releaseLock();
      } catch {
        // Released
      }
    }
  }

  private dispatch(frame: ParsedFrame): void {
    const data = frame.data as Record<string, unknown>;
    const seq = typeof data?.seq === "number" ? data.seq : null;

    if (seq !== null) {
      if (seq <= this.lastSeq) {
        return;
      }
      this.lastSeq = seq;
    }

    switch (frame.event) {
      case "start": {
        const start = data as unknown as StartFrame;
        this.messageId = start.message_id ?? this.messageId;
        this.resumable = start.resumable === true;
        this.handlers.onStart?.(start);
        break;
      }

      case "token": {
        const text = typeof data.text === "string" ? data.text : "";
        if (text) {
          this.handlers.onToken?.(text, seq ?? this.lastSeq);
        }
        break;
      }

      case "citations":
        this.handlers.onCitations?.(data as unknown as CitationEnvelope);
        break;

      case "done":
        this.finished = true;
        this.handlers.onDone?.(data as unknown as DoneFrame);
        this.setState("closed");
        break;

      case "error":
        this.fail(data as unknown as ErrorFrame);
        break;

      default:
        break;
    }
  }

  private fail(frame: ErrorFrame): void {
    if (this.errored || this.finished) {
      return;
    }
    this.errored = true;
    this.handlers.onError?.(frame);
    this.setState("closed");
  }
}

const isAbortError = (error: unknown): boolean =>
  error instanceof DOMException && error.name === "AbortError";

export const streamAssistantMessage = async (
  options: StreamOptions,
): Promise<void> => {
  await new ResumableStream(options).run();
};

export default streamAssistantMessage;
