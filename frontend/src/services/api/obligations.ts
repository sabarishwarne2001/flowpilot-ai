/** ARCH46-S2:api-client — obligations, holiday calendars and calendar feeds. Every route is gated on capability.obligations. */
import { apiClient, apiHref } from "@/services/api/client";
import { downloadBlob } from "@/services/api/audit";
import type {
  CalendarUpdateResult, CalendarView, DateCalculation, DateCalculationRequest, DocumentObligations, EntityObligations,
  ExtractResult, FeedCreate, FeedIssued, FeedList, HolidayCalendarCreate, HolidayCalendarList, HolidayCalendarRow,
  HolidayCalendarUpdate, ObligationCreate, ObligationDetail, ObligationList, ObligationUpdate, ObligationVerdict,
} from "@/types/obligations";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}`;
const one = (workspaceId: string, id: string): string => `${ws(workspaceId)}/obligations/${encodeURIComponent(id)}`;

export interface ObligationFilters {
  readonly state?: string;
  readonly kind?: string;
  readonly review?: string;
  readonly owner?: string;
  readonly q?: string;
  readonly due_from?: string;
  readonly due_to?: string;
  readonly entity_id?: string;
  readonly work_item_id?: string;
  readonly include_rejected?: boolean;
  readonly limit?: number;
}

export const obligationKeys = {
  all: (workspaceId: string) => ["obligations", workspaceId] as const,
  list: (workspaceId: string, filters: ObligationFilters) => ["obligations", workspaceId, "list", filters] as const,
  calendar: (workspaceId: string, from: string, to: string, owner: string) =>
    ["obligations", workspaceId, "calendar", from, to, owner] as const,
  detail: (workspaceId: string, id: string) => ["obligations", workspaceId, "detail", id] as const,
  document: (workspaceId: string, workItemId: string) => ["obligations", workspaceId, "document", workItemId] as const,
  entity: (workspaceId: string, entityId: string) => ["obligations", workspaceId, "entity", entityId] as const,
  calendars: (workspaceId: string) => ["obligations", workspaceId, "holiday-calendars"] as const,
  feeds: (workspaceId: string) => ["obligations", workspaceId, "feeds"] as const,
};

const clean = (filters: ObligationFilters): Record<string, string | number | boolean> => {
  const out: Record<string, string | number | boolean> = {};
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== null && value !== "") {
      out[key] = value as string | number | boolean;
    }
  }
  return out;
};

export const listObligations = async (workspaceId: string, filters: ObligationFilters = {}): Promise<ObligationList> =>
  (await apiClient.get<ObligationList>(`${ws(workspaceId)}/obligations`, { params: clean(filters) })).data;

export const getCalendar = async (workspaceId: string, from: string, to: string, owner?: string): Promise<CalendarView> =>
  (await apiClient.get<CalendarView>(`${ws(workspaceId)}/obligations/calendar`,
    { params: owner ? { from, to, owner } : { from, to } })).data;

export const createObligation = async (workspaceId: string, body: ObligationCreate): Promise<ObligationDetail> =>
  (await apiClient.post<ObligationDetail>(`${ws(workspaceId)}/obligations`, body)).data;

export const getObligation = async (workspaceId: string, id: string): Promise<ObligationDetail> =>
  (await apiClient.get<ObligationDetail>(one(workspaceId, id))).data;

export const updateObligation = async (workspaceId: string, id: string, body: ObligationUpdate): Promise<ObligationDetail> =>
  (await apiClient.patch<ObligationDetail>(one(workspaceId, id), body)).data;

export const completeObligation = async (workspaceId: string, id: string, note?: string): Promise<ObligationDetail> =>
  (await apiClient.post<ObligationDetail>(`${one(workspaceId, id)}/complete`, note ? { note } : {})).data;

export const waiveObligation = async (workspaceId: string, id: string, reason: string): Promise<ObligationDetail> =>
  (await apiClient.post<ObligationDetail>(`${one(workspaceId, id)}/waive`, { reason })).data;

export const reopenObligation = async (workspaceId: string, id: string): Promise<ObligationDetail> =>
  (await apiClient.post<ObligationDetail>(`${one(workspaceId, id)}/reopen`)).data;

export const reviewObligation = async (workspaceId: string, id: string, verdict: ObligationVerdict): Promise<ObligationDetail> =>
  (await apiClient.post<ObligationDetail>(`${one(workspaceId, id)}/review`, { verdict })).data;

export const deleteObligation = async (workspaceId: string, id: string): Promise<void> => {
  await apiClient.delete(one(workspaceId, id));
};

export const calculateDate = async (workspaceId: string, body: DateCalculationRequest): Promise<DateCalculation> =>
  (await apiClient.post<DateCalculation>(`${ws(workspaceId)}/obligations/calculate`, body)).data;

export const getDocumentObligations = async (workspaceId: string, workItemId: string): Promise<DocumentObligations> =>
  (await apiClient.get<DocumentObligations>(`${ws(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/obligations`)).data;

export const extractDocumentObligations = async (workspaceId: string, workItemId: string): Promise<ExtractResult> =>
  (await apiClient.post<ExtractResult>(
    `${ws(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/obligations/extract`)).data;

export const getEntityObligations = async (workspaceId: string, entityId: string): Promise<EntityObligations> =>
  (await apiClient.get<EntityObligations>(`${ws(workspaceId)}/entities/${encodeURIComponent(entityId)}/obligations`)).data;

export const listHolidayCalendars = async (workspaceId: string): Promise<HolidayCalendarList> =>
  (await apiClient.get<HolidayCalendarList>(`${ws(workspaceId)}/holiday-calendars`)).data;

export const createHolidayCalendar = async (workspaceId: string, body: HolidayCalendarCreate): Promise<HolidayCalendarRow> =>
  (await apiClient.post<HolidayCalendarRow>(`${ws(workspaceId)}/holiday-calendars`, body)).data;

export const updateHolidayCalendar = async (
  workspaceId: string, calendarId: string, body: HolidayCalendarUpdate,
): Promise<CalendarUpdateResult> =>
  (await apiClient.patch<CalendarUpdateResult>(`${ws(workspaceId)}/holiday-calendars/${encodeURIComponent(calendarId)}`,
    body)).data;

export const deleteHolidayCalendar = async (workspaceId: string, calendarId: string): Promise<void> => {
  await apiClient.delete(`${ws(workspaceId)}/holiday-calendars/${encodeURIComponent(calendarId)}`);
};

export const listFeeds = async (workspaceId: string): Promise<FeedList> =>
  (await apiClient.get<FeedList>(`${ws(workspaceId)}/calendar-feeds`)).data;

/** The token is in this response only; the server keeps its SHA-256. */
export const issueFeed = async (workspaceId: string, body: FeedCreate): Promise<FeedIssued> =>
  (await apiClient.post<FeedIssued>(`${ws(workspaceId)}/calendar-feeds`, body)).data;

export const revokeFeed = async (workspaceId: string, feedId: string): Promise<void> => {
  await apiClient.delete(`${ws(workspaceId)}/calendar-feeds/${encodeURIComponent(feedId)}`);
};

/**
 * The absolute feed URL a calendar app subscribes to, composed from the API
 * base and the token (never from a URL string in a response body): https for
 * copying, webcal for "open in my calendar".
 */
export const feedUrls = (token: string): { readonly https: string; readonly webcal: string } => {
  const https = new URL(apiHref(`/public/calendar-feeds/${encodeURIComponent(token)}.ics`), window.location.origin).toString();
  return { https, webcal: https.replace(/^https?:/, "webcal:") };
};

/** A one-off export (the session authorises it, so it goes through the API client). */
export const downloadObligations = async (workspaceId: string, format: "ics" | "csv"): Promise<void> => {
  const response = await apiClient.get<Blob>(`${ws(workspaceId)}/obligations/export`, {
    params: { format }, responseType: "blob", timeout: 120_000,
  });
  downloadBlob(response.data, `obligations.${format}`);
};
