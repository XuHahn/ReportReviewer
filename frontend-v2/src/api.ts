import axios from "axios";
import type {
  ArchiveMemberInventory,
  DocumentSetListItem,
  DocumentSetOverview,
  EmcStandard,
  EvidenceGraphRunSummary,
  EvidenceGraphSnapshot,
  LoginResponse,
  ProjectGroup,
  SetStatsResponse,
  StandardGraphClause,
  StandardGraphRequirement,
  StandardKnowledgeChunk,
  StandardReferenceResolution,
  StandardRequirementParameter,
  User,
  UserListResponse,
  VersionDiffItem,
  HealthStatus,
} from "./types";

const TOKEN_KEY = "emc_review_token";
export const getStoredToken = () => localStorage.getItem(TOKEN_KEY);
export const setStoredToken = (token: string) =>
  localStorage.setItem(TOKEN_KEY, token);
export const clearStoredToken = () => localStorage.removeItem(TOKEN_KEY);

const api = axios.create({ baseURL: "/api", timeout: 180_000 });
api.interceptors.request.use((config) => {
  const token = getStoredToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});
api.interceptors.response.use(
  (response) => response,
  (error) => {
    if (error.response?.status === 401) {
      clearStoredToken();
      window.dispatchEvent(new CustomEvent("auth:logout"));
    }
    if (error.response?.status === 403)
      window.dispatchEvent(new CustomEvent("auth:forbidden"));
    return Promise.reject(error);
  },
);

export function apiErrorMessage(error: unknown, fallback = "操作失败") {
  if (axios.isAxiosError(error))
    return String(error.response?.data?.detail || error.message || fallback);
  return error instanceof Error ? error.message : fallback;
}

export function createClientTraceId(prefix = "ui") {
  const random =
    typeof crypto !== "undefined" && typeof crypto.randomUUID === "function"
      ? crypto.randomUUID().replace(/-/g, "").slice(0, 12)
      : Math.random().toString(16).slice(2, 14);
  return `${prefix}-${new Date()
    .toISOString()
    .replace(/[-:.TZ]/g, "")
    .slice(0, 14)}-${random}`;
}

function sanitizeFrontendError(value: unknown, maxLength = 600) {
  return String(value || "")
    .replace(/https?:\/\/[^\s]+/g, "[url]")
    .slice(0, maxLength);
}

export async function reportFrontendError(payload: {
  traceId: string;
  route: string;
  errorName: string;
  errorMessage: string;
  stack?: string;
}) {
  try {
    await api.post("/logs/batch", {
      logs: [
        {
          ts: new Date().toISOString(),
          level: "ERROR",
          msg: "route_render_error",
          module: "frontend-v2",
          ctx: {
            trace_id: payload.traceId,
            route: sanitizeFrontendError(payload.route, 240),
            error_name: sanitizeFrontendError(payload.errorName, 120),
          },
          error: `${sanitizeFrontendError(payload.errorMessage, 240)}${payload.stack ? ` | ${sanitizeFrontendError(payload.stack)}` : ""}`,
        },
      ],
    });
  } catch {
    // The trace ID remains useful even when the logging endpoint is unavailable.
  }
}

export async function login(employeeId: string): Promise<LoginResponse> {
  const { data } = await api.post<LoginResponse>("/auth/login", {
    employee_id: employeeId,
  });
  setStoredToken(data.token);
  return data;
}
export async function getHealth(): Promise<HealthStatus> {
  return (await api.get<HealthStatus>("/health")).data;
}
export async function getCurrentUser() {
  return (await api.get<User>("/auth/me")).data;
}
export async function listUsers() {
  return (await api.get<UserListResponse>("/users")).data;
}
export async function createUser(employee_id: string, role: string, name = "") {
  return (await api.post<User>("/users", { employee_id, role, name })).data;
}
export async function updateUserRole(employeeId: string, role: string) {
  return (await api.put<User>(`/users/${employeeId}/role`, { role })).data;
}
export async function updateUserName(employeeId: string, name: string) {
  return (await api.put<User>(`/users/${employeeId}/name`, { name })).data;
}

export async function createDocumentSet() {
  return (await api.post<{ set_id: string; status: string }>("/sets")).data;
}
export async function listDocumentSets() {
  const response = (
    await api.get<{ sets: DocumentSetListItem[]; total: number }>("/sets")
  ).data;
  return {
    ...response,
    sets: response.sets.map((item) => ({
      ...item,
      documents_count: item.documents_count ?? item.doc_count ?? 0,
    })),
  };
}
export async function getSetStats(): Promise<SetStatsResponse> {
  const raw = (await api.get<any>("/sets/stats/overview")).data;
  const status = raw.status_dist || raw.status_counts || {};
  return {
    total_sets: Number(raw.total_sets || 0),
    draft_sets: Number(
      raw.draft_sets ?? (status.incomplete || 0) + (status.revision || 0),
    ),
    locked_sets: Number(
      raw.locked_sets ?? (status.locked || 0) + (status.reviewing || 0),
    ),
    reviewed_sets: Number(raw.reviewed_sets ?? (status.reviewed || 0)),
    total_issues: Number(raw.total_issues || 0),
    confirmed_issues: Number(raw.confirmed_issues || 0),
    pass_rate: Number(raw.pass_rate ?? raw.clean_rate ?? 0),
    average_review_seconds: Number(raw.average_review_seconds || 0),
    status_counts: status,
    avg_issues_per_set: Number(raw.avg_issues_per_set || 0),
    severity_dist: raw.severity_dist || {},
    trends: Array.isArray(raw.trends)
      ? raw.trends.map((item: any) => ({
          date: String(item.date || ""),
          count: Number(item.count || 0),
        }))
      : [],
    top_categories: Array.isArray(raw.top_categories)
      ? raw.top_categories.map((item: any) => ({
          category: String(item.category || ""),
          count: Number(item.count || 0),
        }))
      : [],
  };
}
export async function getDocumentSet(
  setId: string,
): Promise<DocumentSetOverview> {
  const raw = (await api.get<any>(`/sets/${setId}`)).data;
  const source = raw.documents || raw.files || [];
  const documents = source.map((item: any) => ({
    ...item,
    file_size:
      item.file_size ??
      (item.file_size_kb ? Number(item.file_size_kb) * 1024 : undefined),
    doc_version: item.doc_version ?? item.latest_version ?? 1,
    status: item.status || item.extraction_status,
    extraction_status: ["done", "partial"].includes(item.extraction_status)
      ? "completed"
      : item.extraction_status,
    page_count:
      item.page_count ??
      item.extraction_meta?.page_count ??
      (item.doc_type === "original_records"
        ? (item.member_count ?? item.extraction_meta?.member_count)
        : undefined),
  }));
  return {
    ...raw,
    documents,
    revision: Math.max(
      1,
      ...documents.map((item: any) => Number(item.doc_version || 1)),
    ),
  };
}
export async function deleteDocumentSet(setId: string) {
  return (await api.delete(`/sets/${setId}`)).data;
}
export async function updateDocumentSetStatus(setId: string, status: string) {
  return (await api.put(`/sets/${setId}/status`, { status })).data;
}
export async function updateDocumentSetProjectGroup(
  setId: string,
  project_group_id: string,
) {
  return (await api.put(`/sets/${setId}/project-group`, { project_group_id }))
    .data;
}

export async function addDocumentToSet(
  setId: string,
  file: File,
  docType: string,
  replaceDocId?: string,
  signal?: AbortSignal,
) {
  const body = new FormData();
  body.append("file", file);
  body.append("doc_type", docType);
  if (replaceDocId) body.append("replace_doc_id", replaceDocId);
  return (
    await api.post(`/sets/${setId}/documents`, body, { signal, timeout: 0 })
  ).data;
}
export async function deleteDocument(setId: string, docId: string) {
  return (await api.delete(`/sets/${setId}/documents/${docId}`)).data;
}
export async function lockDocumentSet(
  setId: string,
  options: { auto_gate?: boolean; exception_only?: boolean } = {},
) {
  return (await api.post(`/sets/${setId}/lock`, options)).data;
}
export async function createDocumentSetRevision(setId: string) {
  return (await api.post(`/sets/${setId}/revision`)).data;
}

export async function getVersionHistory(setId: string, docType: string) {
  return (await api.get(`/sets/${setId}/versions/${docType}`)).data as {
    versions: any[];
    total: number;
  };
}
export async function diffVersions(
  setId: string,
  oldDocId: string,
  newDocId: string,
) {
  return (
    await api.get(`/sets/${setId}/diff`, {
      params: { old_doc_id: oldDocId, new_doc_id: newDocId },
    })
  ).data as { diffs: VersionDiffItem[]; changed: VersionDiffItem[] };
}
export async function getArchiveMemberInventory(setId: string, docId: string) {
  const data = (
    await api.get<ArchiveMemberInventory>(
      `/sets/${setId}/documents/${docId}/archive-members`,
    )
  ).data;
  return {
    ...data,
    members: data.members.map((member) => ({
      ...member,
      filename: member.filename || member.source_filename || "",
    })),
  };
}

export async function startReviewRun(setId: string) {
  return (
    await api.post<{ set_id: string; run_id: string; status: string }>(
      `/sets/${setId}/review-runs`,
    )
  ).data;
}
export async function listReviewRuns(setId: string) {
  return (
    await api.get<{ runs: EvidenceGraphRunSummary[]; total: number }>(
      `/sets/${setId}/review-runs`,
    )
  ).data;
}
export async function getReviewRun(setId: string, runId: string) {
  return (
    await api.get<EvidenceGraphSnapshot>(`/sets/${setId}/review-runs/${runId}`)
  ).data;
}
export async function decideReviewFinding(
  setId: string,
  runId: string,
  findingId: string,
  body: {
    decision: "confirmed" | "dismissed" | "advisory" | "unresolved";
    comment: string;
    resolution_code?: string;
    resolution_status?: string;
  },
) {
  await api.put(
    `/sets/${setId}/review-runs/${runId}/findings/${findingId}/decision`,
    body,
  );
}

export async function downloadReviewRunExport(
  setId: string,
  runId: string,
  format: "xlsx" | "pdf" | "evidence",
) {
  const response = await api.get(`/sets/${setId}/review-runs/${runId}/export`, {
    params: { format },
    responseType: "blob",
    timeout: 180_000,
  });
  const disposition = String(response.headers["content-disposition"] || "");
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/)?.[1];
  const filename = encoded
    ? decodeURIComponent(encoded)
    : `${setId}-review.${format === "evidence" ? "zip" : format}`;
  const url = URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function normalizeStandard(item: any): EmcStandard {
  return {
    ...item,
    chunks_count: Number(item.chunks_count ?? item.chunk_count ?? 0),
    requirements_count: Number(
      item.requirements_count ?? item.graph_requirement_count ?? 0,
    ),
    pending_requirements_count: Number(
      item.pending_requirements_count ?? item.graph_pending_count ?? 0,
    ),
    published_release_count: Number(
      item.published_release_count ?? item.release_count ?? 0,
    ),
  };
}
export async function getStandards(params: Record<string, string> = {}) {
  return (await api.get<any[]>("/standards", { params })).data.map(
    normalizeStandard,
  );
}
export async function getStandardDetail(id: string) {
  return normalizeStandard((await api.get<any>(`/standards/${id}`)).data);
}
export async function createStandard(body: Partial<EmcStandard>) {
  return (await api.post<{ id: string }>("/standards", body)).data;
}
export async function deleteStandard(id: string) {
  await api.delete(`/standards/${id}`);
}
export async function uploadStandard(
  file: File,
  metadata: Record<string, string> = {},
) {
  const body = new FormData();
  body.append("file", file);
  Object.entries(metadata).forEach(
    ([key, value]) => value && body.append(key, value),
  );
  return (await api.post("/standards/upload", body, { timeout: 0 })).data;
}
export async function getStandardKnowledge(id: string, query = "", limit = 50) {
  const raw = (
    await api.get<any>(`/standards/${id}/knowledge`, {
      params: { query, limit },
    })
  ).data;
  return {
    ...raw,
    standard: normalizeStandard(raw.standard),
    chunks: (raw.chunks || []).map((item: any) => ({
      ...item,
      chunk_id: item.chunk_id || item.id,
      clause_number: item.clause_number || item.clause,
      clause_title: item.clause_title || item.title,
      text: item.text || item.content || "",
      page_number: item.page_number || item.page_start || undefined,
    })),
  } as {
    standard: EmcStandard;
    chunks: StandardKnowledgeChunk[];
    total: number;
  };
}
export async function retryStandardKnowledge(id: string) {
  return (await api.post(`/standards/${id}/knowledge/retry`)).data;
}
export async function rebuildStandardGraph(id: string) {
  return (await api.post(`/standards/${id}/graph/rebuild`)).data;
}
export async function retryFailedStandardGraphUnits(id: string) {
  return (await api.post(`/standards/${id}/graph/retry-failed`)).data;
}
export async function getStandardGraph(id: string) {
  return (
    await api.get<{
      standard: EmcStandard;
      clauses: StandardGraphClause[];
      requirements: StandardGraphRequirement[];
    }>(`/standards/${id}/graph`)
  ).data;
}
export async function reviewStandardRequirement(
  standardId: string,
  requirementId: string,
  body: {
    review_status: "confirmed" | "rejected";
    comment?: string;
    clause_number?: string;
    clause_title?: string;
    requirement_type?: string;
    test_item?: string;
    statement?: string;
    interpretation_zh?: string;
    applicability?: string;
    parameters?: StandardRequirementParameter[];
  },
) {
  return (
    await api.patch<StandardGraphRequirement>(
      `/standards/${standardId}/graph/requirements/${requirementId}`,
      body,
    )
  ).data;
}
export async function publishStandardGraph(id: string) {
  return (await api.post(`/standards/${id}/graph/publish`)).data;
}
export async function getStandardReleases(id: string) {
  return (await api.get(`/standards/${id}/releases`)).data;
}
export async function getStandardRelease(
  standardId: string,
  releaseId: string,
) {
  return (await api.get(`/standards/${standardId}/releases/${releaseId}`)).data;
}
export async function saveStandardRequirementMapping(
  standardId: string,
  body: Record<string, unknown>,
) {
  return (await api.post(`/standards/${standardId}/mappings`, body)).data;
}

export async function getDocumentSetStandards(setId: string) {
  return (await api.get(`/sets/${setId}/standards`)).data;
}
export async function getDocumentSetStandardReferences(setId: string) {
  return (
    await api.get<StandardReferenceResolution>(
      `/sets/${setId}/standard-references`,
    )
  ).data;
}
export async function updateDocumentSetStandards(
  setId: string,
  standard_ids: string[],
  skips: Array<{
    reference_code: string;
    normalized_code: string;
    reason: string;
  }> = [],
) {
  return (await api.put(`/sets/${setId}/standards`, { standard_ids, skips }))
    .data;
}

export async function getProjectGroups() {
  return (await api.get<ProjectGroup[]>("/groups")).data;
}
export async function createProjectGroup(
  name: string,
  description: string,
  member_ids: string[],
  category?: string,
) {
  return (
    await api.post("/groups", { name, description, member_ids, category })
  ).data;
}
export async function updateProjectGroup(
  id: string,
  body: Partial<ProjectGroup>,
) {
  return (await api.put<ProjectGroup>(`/groups/${id}`, body)).data;
}
export async function deleteProjectGroup(id: string) {
  return (await api.delete(`/groups/${id}`)).data;
}

export async function getTags() {
  return (await api.get("/tags")).data;
}
export async function createTag(name: string) {
  return (await api.post("/tags", { name })).data;
}
export async function updateTag(id: string, name: string) {
  return (await api.put(`/tags/${id}`, { name })).data;
}
export async function deleteTag(id: string) {
  await api.delete(`/tags/${id}`);
}

export async function getSystemSettings() {
  return (
    await api.get<{ settings: Record<string, string> }>("/admin/settings")
  ).data;
}
export async function updateSystemSettings(settings: Record<string, string>) {
  return (
    await api.put<{ settings: Record<string, string> }>("/admin/settings", {
      settings,
    })
  ).data;
}
export async function getSystemLogs(
  params: Record<string, string | number> = {},
) {
  return (
    await api.get<{ logs: Array<Record<string, any>>; total: number }>(
      "/admin/logs/view",
      { params },
    )
  ).data;
}

export const getDocumentFileUrl = (setId: string, docId: string) =>
  `/api/sets/${setId}/documents/${docId}/file`;
export const getArchiveMemberFileUrl = (
  setId: string,
  docId: string,
  memberIndex: number,
) =>
  `/api/sets/${setId}/documents/${docId}/archive-members/${memberIndex}/file`;
export const getEvidencePagePreviewUrl = (
  setId: string,
  runId: string,
  evidenceId: string,
  relatedEvidenceIds: string[] = [],
  highlightKinds: string[] = [],
  focusIndex?: number,
) => {
  const params = new URLSearchParams({ renderer: "semantic-multi-v1" });
  if (relatedEvidenceIds.length)
    params.set("related_evidence_ids", relatedEvidenceIds.join(","));
  if (highlightKinds.length)
    params.set("highlight_kinds", highlightKinds.join(","));
  if (typeof focusIndex === "number" && focusIndex >= 0)
    params.set("focus_index", String(focusIndex));
  return `/api/sets/${setId}/review-runs/${runId}/evidence/${evidenceId}/preview?${params}`;
};

export async function fetchAuthenticatedBlob(
  url: string,
  signal?: AbortSignal,
): Promise<Blob> {
  return (
    await api.get(url.startsWith("/api/") ? url.slice(4) : url, {
      responseType: "blob",
      signal,
    })
  ).data;
}

export interface EvidencePreviewResponse {
  blob: Blob;
  anchorY: string;
  highlightCount: number;
  highlightStrategy: string;
  cacheStatus: string;
  locatorEnrichedCount: number;
}

export async function fetchAuthenticatedBlobWithHeaders(
  url: string,
  signal?: AbortSignal,
): Promise<EvidencePreviewResponse> {
  const response = await api.get(url.startsWith("/api/") ? url.slice(4) : url, {
    responseType: "blob",
    signal,
  });
  return {
    blob: response.data,
    anchorY: String(response.headers["x-evidence-anchor-y"] || ""),
    highlightCount: Number(response.headers["x-evidence-highlight-count"] || 0),
    highlightStrategy: String(
      response.headers["x-evidence-highlight-strategy"] || "none",
    ),
    cacheStatus: String(response.headers["x-evidence-preview-cache"] || ""),
    locatorEnrichedCount: Number(
      response.headers["x-evidence-locator-enriched"] || 0,
    ),
  };
}

export async function openAuthenticatedResource(url: string): Promise<void> {
  const popup = window.open("", "_blank");
  try {
    const objectUrl = URL.createObjectURL(await fetchAuthenticatedBlob(url));
    if (popup) popup.location.href = objectUrl;
    else {
      const link = document.createElement("a");
      link.href = objectUrl;
      link.target = "_blank";
      link.rel = "noreferrer";
      link.click();
    }
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
  } catch (error) {
    popup?.close();
    throw error;
  }
}
