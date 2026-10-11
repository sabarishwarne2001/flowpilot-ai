/**
 * Campaign session 2: the operator's Background jobs console (platform superadmins).
 * Mirrors backend/app/schemas/platform_jobs.py.
 */
export interface JobTypeSummary {
  readonly job_type: string;
  readonly pending: number;
  readonly running: number;
  readonly retrying: number;
  readonly dead: number;
  readonly succeeded_24h: number;
  readonly oldest_waiting_seconds: number | null;
}

export interface JobsSummary {
  readonly generated_at: string;
  readonly totals: {
    readonly pending: number;
    readonly running: number;
    readonly retrying: number;
    readonly dead: number;
    readonly expired_leases: number;
  };
  readonly by_type: readonly JobTypeSummary[];
}

export interface DeadJob {
  readonly id: string;
  readonly job_type: string;
  readonly organization_id: string | null;
  readonly organization_name: string | null;
  readonly work_item_id: string | null;
  readonly attempts: number;
  readonly max_attempts: number;
  readonly last_error: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface DeadJobList {
  readonly items: readonly DeadJob[];
  readonly total: number;
}

export interface JobRetryResult {
  readonly id: string;
  readonly status: string;
}
