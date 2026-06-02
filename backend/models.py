from pydantic import BaseModel, Field
from datetime import datetime


class ReviewItem(BaseModel):
    severity: str = "info"
    location: str = ""
    original_text: str = ""
    error_description: str = ""
    standard_reference: str = ""
    suggestion: str = ""
    highlighted: bool = True
    human_status: str = "pending"
    human_comment: str = ""
    annotated_by: str = ""
    annotated_at: str = ""


class ReviewResult(BaseModel):
    overall_result: str  # pass / fail / warning
    review_items: list[ReviewItem] = Field(default_factory=list)
    summary: str = ""


class ReportRecord(BaseModel):
    id: str | None = None
    filename: str
    overall_result: str
    original_result: str = ""
    review_items: list[ReviewItem] = Field(default_factory=list)
    highlighted_html: str = ""
    created_at: str | None = None
    employee_id: str = ""
    uploader_name: str = ""
    group_id: str = ""
    comparison: str = ""
    compared_with: str = ""
    tags: str = ""


class ReportListResponse(BaseModel):
    reports: list[ReportRecord]
    total: int


# ── Full-text search models ─────────────────────────────────────────

class SearchSnippets(BaseModel):
    location: str = ""
    original_text: str = ""
    error_description: str = ""
    standard_reference: str = ""
    suggestion: str = ""


class SearchResult(BaseModel):
    report_id: str
    filename: str
    overall_result: str
    created_at: str = ""
    employee_id: str = ""
    uploader_name: str = ""
    snippets: SearchSnippets = SearchSnippets()
    match_count: int = 0
    rank: float = 0.0


class SearchResponse(BaseModel):
    results: list[SearchResult]
    total: int
    query: str


class UploadResponse(BaseModel):
    report_id: str
    filename: str
    overall_result: str
    original_result: str = ""
    review_items: list[ReviewItem]
    highlighted_html: str
    created_at: str
    estimated_tokens: int = 0
    token_limit: int = 0
    truncated: bool = False
    employee_id: str = ""
    uploader_name: str = ""
    tags: str = ""
    group_id: str = ""
    comparison: str = ""
    compared_with: str = ""


class AuditLogEntry(BaseModel):
    id: int
    timestamp: str
    client_ip: str = ""
    employee_id: str = ""
    tags: str = ""
    action: str
    filename: str = ""
    file_size_kb: int = 0
    overall_result: str = ""
    issue_count: int = 0
    detail: str = ""


class AuditLogListResponse(BaseModel):
    entries: list[AuditLogEntry]
    total: int


# ── Statistics models ──────────────────────────────────────────────

class StatsOverview(BaseModel):
    total_reports: int
    total_issues: int
    pass_rate: float
    avg_issues_per_report: float
    avg_duration_ms: int


class SeverityDist(BaseModel):
    error: int = 0
    warning: int = 0
    info: int = 0


class DailyTrend(BaseModel):
    date: str
    uploads: int
    pass_count: int
    fail_count: int


class TopLocation(BaseModel):
    location: str
    count: int


class StatsResponse(BaseModel):
    overview: StatsOverview
    pass_fail: dict[str, int]
    severity_dist: SeverityDist
    trends: list[DailyTrend]
    top_locations: list[TopLocation]
    top_actions: dict[str, int]


# ── Review rules models ──────────────────────────────────────────────

class ReviewRule(BaseModel):
    id: str = ""
    name: str
    description: str = ""
    category: str = "other"  # limit / consistency / logic / format / other
    severity: str = "warning"  # error / warning / info
    keywords: list[str] = Field(default_factory=list)
    pattern: str = ""
    standard_id: str = ""
    suggestion_template: str = ""
    enabled: bool = True
    created_at: str = ""
    updated_at: str = ""


class RuleCreateRequest(BaseModel):
    name: str
    description: str = ""
    category: str = "other"
    severity: str = "warning"
    keywords: list[str] = Field(default_factory=list)
    pattern: str = ""
    standard_id: str = ""
    suggestion_template: str = ""
    enabled: bool = True


class RuleUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    category: str | None = None
    severity: str | None = None
    keywords: list[str] | None = None
    pattern: str | None = None
    standard_id: str | None = None
    suggestion_template: str | None = None
    enabled: bool | None = None


# ── EMC standards models ─────────────────────────────────────────────

class StandardClause(BaseModel):
    clause: str = ""
    title: str = ""
    description: str = ""
    limit_table: list[dict[str, str]] = Field(default_factory=list)


class EmcStandard(BaseModel):
    id: str = ""
    code: str
    title: str
    organization: str = ""  # CISPR / GB / EN / FCC / IEC
    category: str = ""     # radiated / conducted / immunity / ...
    version: str = ""
    clauses: list[StandardClause] = Field(default_factory=list)
    is_builtin: bool = False
    created_at: str = ""


class StandardCreateRequest(BaseModel):
    code: str
    title: str
    organization: str = ""
    category: str = ""
    version: str = ""
    clauses: list[StandardClause] = Field(default_factory=list)


class StandardListResponse(BaseModel):
    standards: list[EmcStandard]
    total: int


# ── Auth / User models ─────────────────────────────────────────────

class User(BaseModel):
    employee_id: str
    role: str  # admin | reviewer | viewer
    name: str = ""
    created_at: str = ""


class LoginRequest(BaseModel):
    employee_id: str


class LoginResponse(BaseModel):
    token: str
    user: User


class UserCreateRequest(BaseModel):
    employee_id: str
    role: str = "viewer"
    name: str = ""


class UserUpdateRoleRequest(BaseModel):
    role: str


class UpdateNameRequest(BaseModel):
    name: str = Field(..., min_length=1)


class UserListResponse(BaseModel):
    users: list[User]
    total: int


# ── System settings models ─────────────────────────────────────────────

class SystemSettings(BaseModel):
    settings: dict[str, str]


class SettingsUpdateRequest(BaseModel):
    settings: dict[str, str]


# ── Admin stats model ─────────────────────────────────────────────────

class AdminStatsResponse(BaseModel):
    overview: StatsOverview
    pass_fail: dict[str, int]
    severity_dist: SeverityDist
    trends: list[DailyTrend]
    top_locations: list[TopLocation]
    top_actions: dict[str, int]
    total_users: int = 0
    today_uploads: int = 0
    recent_activity: list[AuditLogEntry] = Field(default_factory=list)


class AnnotationUpdateRequest(BaseModel):
    human_status: str
    human_comment: str = ""
    since: str = ""  # client's last-known state timestamp for conflict detection (future: compare with annotated_at to reject stale writes)


# ── Tag models ──────────────────────────────────────────────────────

class Tag(BaseModel):
    id: str = ""
    name: str
    created_by: str = ""
    created_at: str = ""


class TagCreateRequest(BaseModel):
    name: str


class TagUpdateRequest(BaseModel):
    name: str


class FrontendLogEntry(BaseModel):
    ts: str = ""
    level: str = "INFO"
    msg: str = ""
    reqId: str | None = None
    module: str = ""
    userId: str | None = None
    ctx: dict | None = None
    error: str | None = None


class FrontendLogBatchRequest(BaseModel):
    logs: list[FrontendLogEntry]
