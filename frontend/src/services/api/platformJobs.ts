/**
 * Campaign session 2: the operator's Background jobs console (superadmin: 404 for anyone else).
 */
import { apiClient } from "@/services/api/client";
import type { DeadJobList, JobRetryResult, JobsSummary } from "@/types/platformJobs";

const BASE = "/admin/jobs";

export const platformJobKeys = {
  all: () => ["platform-jobs"] as const,
  summary: () => ["platform-jobs", "summary"] as const,
  dead: (jobType: string) => ["platform-jobs", "dead", jobType] as const,
};

export const getJobsSummary = async (): Promise<JobsSummary> =>
  (await apiClient.get<JobsSummary>(`${BASE}/summary`)).data;

export const getDeadJobs = async (jobType: string): Promise<DeadJobList> =>
  (await apiClient.get<DeadJobList>(`${BASE}/dead`, { params: jobType ? { job_type: jobType } : {} })).data;

export const retryDeadJob = async (jobId: string): Promise<JobRetryResult> =>
  (await apiClient.post<JobRetryResult>(`${BASE}/${encodeURIComponent(jobId)}/retry`)).data;
