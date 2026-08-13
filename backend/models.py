from pydantic import BaseModel, Field
from datetime import datetime
from typing import Literal



class AuditLogEntry(BaseModel):
    id: int
    timestamp: str
    client_ip: str = ""
    employee_id: str = ""
    req_id: str = ""
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
    normalized_code: str = ""
    source_filename: str = ""
    file_sha256: str = ""
    page_count: int = 0
    knowledge_status: str = "manual"  # manual | pending | processing | ready | failed
    knowledge_error: str = ""
    knowledge_meta: dict = Field(default_factory=dict)
    chunk_count: int = 0
    graph_status: str = "not_started"  # not_started | extracting | pending_review | published | failed
    graph_error: str = ""
    graph_meta: dict = Field(default_factory=dict)
    graph_clause_count: int = 0
    graph_requirement_count: int = 0
    graph_pending_count: int = 0
    graph_confirmed_count: int = 0
    graph_rejected_count: int = 0
    latest_release_id: str = ""
    latest_release_number: int = 0
    release_count: int = 0
    selected_release_id: str = ""
    selected_release_number: int = 0
    created_by: str = ""
    created_at: str = ""
    updated_at: str = ""


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


class StandardKnowledgeChunk(BaseModel):
    id: str = ""
    standard_id: str = ""
    chunk_index: int = 0
    clause: str = ""
    title: str = ""
    page_start: int = 0
    page_end: int = 0
    content: str = ""
    structured: dict = Field(default_factory=dict)
    created_at: str = ""


class StandardRequirementParameter(BaseModel):
    id: str = ""
    name: str = ""
    symbol: str = ""
    comparator: str = ""
    value: str = ""
    value_min: str = ""
    value_max: str = ""
    unit: str = ""
    raw_text: str = ""


class StandardGraphRequirement(BaseModel):
    id: str = ""
    standard_id: str = ""
    clause_id: str = ""
    clause_number: str = ""
    clause_title: str = ""
    requirement_type: str = "other"
    test_item: str = ""
    statement: str = ""
    original_statement: str = ""
    interpretation_zh: str = ""
    interpretation_status: str = "pending"
    applicability: str = ""
    evidence_quote: str = ""
    page_start: int = 0
    page_end: int = 0
    source_chunk_id: str = ""
    confidence: float = 0.0
    review_status: str = "pending"
    review_comment: str = ""
    reviewed_by: str = ""
    reviewed_at: str = ""
    parameters: list[StandardRequirementParameter] = Field(default_factory=list)
    relations: list[dict] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""


class StandardGraphRelease(BaseModel):
    id: str = ""
    standard_id: str = ""
    release_number: int = 0
    status: str = "published"
    source_file_sha256: str = ""
    embedding_model: str = ""
    prompt_version: str = ""
    requirement_count: int = 0
    published_by: str = ""
    published_at: str = ""


class StandardGraphClause(BaseModel):
    id: str = ""
    standard_id: str = ""
    clause_number: str = ""
    title: str = ""
    parent_clause_number: str = ""
    page_start: int = 0
    page_end: int = 0
    source_chunk_id: str = ""
    requirements: list[StandardGraphRequirement] = Field(default_factory=list)


class StandardRequirementReviewRequest(BaseModel):
    review_status: str
    comment: str = ""
    clause_number: str | None = None
    clause_title: str | None = None
    requirement_type: str | None = None
    test_item: str | None = None
    statement: str | None = None
    interpretation_zh: str | None = None
    applicability: str | None = None
    parameters: list[StandardRequirementParameter] | None = None


class DocumentSetStandardsUpdate(BaseModel):
    standard_ids: list[str] = Field(default_factory=list)
    skips: list[dict[str, str]] = Field(default_factory=list)


class DocumentSetProjectGroupUpdate(BaseModel):
    project_group_id: str = ""


class StandardRequirementMappingRequest(BaseModel):
    release_id: str
    source_name: str
    mapping_type: str = "covered"  # covered | not_covered
    requirement_id: str = ""
    scope_type: str = "standard"  # standard | project | set
    scope_value: str = ""
    rationale: str = ""


# ── Auth / User models ─────────────────────────────────────────────

class User(BaseModel):
    employee_id: str
    role: str  # admin | reviewer | standard_reviewer | viewer
    name: str = ""
    created_at: str = ""


class LoginRequest(BaseModel):
    employee_id: str


class LoginResponse(BaseModel):
    token: str
    user: User


class UserCreateRequest(BaseModel):
    employee_id: str
    role: Literal["admin", "reviewer", "standard_reviewer", "viewer"] = "viewer"
    name: str = ""


class UserUpdateRoleRequest(BaseModel):
    role: Literal["admin", "reviewer", "standard_reviewer", "viewer"]


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


# ── Order Form (委托单) extraction models ────────────────────────────

class OrderFormContact(BaseModel):
    """Contact info shared across applicant/manufacturer/factory sections."""
    name: str = ""
    email: str = ""
    phone: str = ""  # always clean string, no float artifacts


class OrderFormCompany(BaseModel):
    """A company section (applicant, manufacturer, or factory)."""
    name_en: str = ""
    name_cn: str = ""
    address_en: str = ""
    address_cn: str = ""
    contact: OrderFormContact = Field(default_factory=OrderFormContact)


class OrderFormProduct(BaseModel):
    """Product information section."""
    name: str = ""
    part_number: str = ""
    main_test_model: str = ""
    trademark: str = ""
    voltage: str = ""
    work_frequency: str = ""


class OrderFormTestRequirements(BaseModel):
    """Test requirements section."""
    requirements_desc: str = ""
    test_specification: str = ""
    decision_rule: str = ""
    report_qualification: str = ""
    test_purpose: str = ""
    report_form: str = ""
    no_subcontract_acceptance: str = ""


class OrderFormOtherInfo(BaseModel):
    """Other special requirements and metadata."""
    software_version: str = ""
    hardware_version: str = ""
    report_delivery_method: str = ""
    sample_disposal_method: str = ""
    application_date: str = ""


class OrderFormData(BaseModel):
    """Complete structured extraction from a GRGT order form (.xls)."""
    template_id: str = ""
    applicant: OrderFormCompany = Field(default_factory=OrderFormCompany)
    manufacturer: OrderFormCompany = Field(default_factory=OrderFormCompany)
    factory: OrderFormCompany = Field(default_factory=OrderFormCompany)
    product: OrderFormProduct = Field(default_factory=OrderFormProduct)
    test_requirements: OrderFormTestRequirements = Field(default_factory=OrderFormTestRequirements)
    other_info: OrderFormOtherInfo = Field(default_factory=OrderFormOtherInfo)
    raw_notes: list[str] = Field(default_factory=list)


# ── Document Set (文档集) models ──────────────────────────────────────

DocType = Literal[
    "order_form", "test_plan", "original_records",
    "final_report", "test_standard",
]

DOC_TYPES: list[DocType] = [
    "order_form", "test_plan", "original_records",
    "final_report", "test_standard",
]

DOC_TYPE_LABELS: dict[DocType, str] = {
    "order_form": "委托单",
    "test_plan": "试验计划/大纲",
    "original_records": "全套原始记录",
    "final_report": "最终检测报告",
    "test_standard": "测试标准",
}

REQUIRED_DOC_TYPES: set[DocType] = {
    "order_form", "test_plan", "original_records", "final_report",
}


class SetDocument(BaseModel):
    """A single document within a document set, with version tracking."""
    doc_id: str = ""
    set_id: str = ""
    doc_type: DocType = "final_report"
    filename: str = ""
    file_size_kb: int = 0
    doc_version: int = Field(default=1, ge=1)
    parent_doc_id: str = ""       # "" = root version; non-empty = child version
    upload_order: int = 0
    plain_text: str = ""          # extracted text content (cached)
    html_content: str = ""        # HTML rendering (cached)
    extraction_status: str = "pending"  # pending | extracting | done | partial | failed
    extraction_quality: str = "pending"  # pending | complete | partial | failed
    extraction_meta: dict = Field(default_factory=dict)
    extraction_error: str = ""    # error message when extraction_status == 'failed'
    reviewed_by: str = ""
    reviewed_at: str = ""
    created_at: str = ""


class SetDocumentCreate(BaseModel):
    """Request model for adding a document to a set."""
    doc_type: DocType
    filename: str
    file_size_kb: int = 0
    replace_doc_id: str = ""      # if set, creates a new version of that doc


class DocumentSet(BaseModel):
    """A batch of related documents for one review submission."""
    set_id: str = ""
    status: str = "incomplete"  # incomplete | locked | reviewing | reviewed | error
    documents: list[SetDocument] = Field(default_factory=list)
    created_at: str = ""
    employee_id: str = ""
    project_group_id: str = ""
    project_group_name: str = ""
    missing_types: list[DocType] = Field(default_factory=list)  # computed


class DocumentSetListResponse(BaseModel):
    """List of document sets."""
    sets: list[DocumentSet]
    total: int


class ExtractedMetadata(BaseModel):
    """Cached extraction result for a specific document version."""
    extraction_id: str = ""
    set_id: str = ""
    doc_id: str = ""
    field_name: str = ""
    field_value: str = ""
    source_text: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_location: str = ""


class VersionDiff(BaseModel):
    """Field-level diff between two document versions."""
    field_name: str = ""
    old_value: str = ""
    new_value: str = ""
    changed: bool = False


# ── Test Plan (试验计划) extraction models ────────────────────────────

class TestPlanBasicInfo(BaseModel):
    part_name: str = ""; part_number: str = ""; supplier_name: str = ""
    test_standard: str = ""; sample_count: str = ""
    software_version: str = ""; hardware_version: str = ""
    plan_number: str = ""; test_location: str = ""


class TestPlanItem(BaseModel):
    code: str = ""; name: str = ""; is_executed: str = ""
    test_mode: str = ""; standard_clause: str = ""; acceptance: str = ""
    sample_requirements: dict[str, str] = Field(default_factory=dict)
    source_quote: str = ""
    source_location: str = ""


class TestPlanDetail(BaseModel):
    code: str = ""
    fields: dict[str, str] = Field(default_factory=dict)
    source_quote: str = ""
    source_location: str = ""


class TestPlanData(BaseModel):
    basic_info: TestPlanBasicInfo = Field(default_factory=TestPlanBasicInfo)
    test_items: list[TestPlanItem] = Field(default_factory=list)
    test_details: list[TestPlanDetail] = Field(default_factory=list)
    raw_text: str = ""
    extraction_quality: str = "complete"
    failed_passes: list[str] = Field(default_factory=list)
    extraction_metrics: dict = Field(default_factory=dict)


# ── Report (检测报告) extraction models ──────────────────────────────

class ReportCoverInfo(BaseModel):
    """Cover page of the final test report."""
    report_number: str = ""
    test_plan_number: str = ""
    client_name: str = ""
    client_address: str = ""
    sample_name: str = ""
    part_number: str = ""
    sample_model: str = ""
    receive_date: str = ""
    test_date_range: str = ""
    test_standards: list[str] = Field(default_factory=list)
    test_conclusion: str = ""
    preparer: str = ""
    reviewer: str = ""
    approver: str = ""
    issue_date: str = ""


class ReportResultItem(BaseModel):
    """A single row in the test results summary table."""
    test_item: str = ""
    test_mode: str = ""
    standard_requirement: str = ""
    grade_requirement: str = ""
    result: str = ""


class ReportSampleInfo(BaseModel):
    """Sample description section."""
    client_name: str = ""
    manufacturer_name: str = ""
    sample_name: str = ""
    model: str = ""
    part_number: str = ""
    hw_version: str = ""
    sw_version: str = ""
    rated_voltage: str = ""
    serial_numbers: list[str] = Field(default_factory=list)
    lab_sample_ids: list[str] = Field(default_factory=list)
    test_modes: list[dict] = Field(default_factory=list)
    monitoring_methods: list[dict] = Field(default_factory=list)
    performance_grades: list[dict] = Field(default_factory=list)


class ReportInstrument(BaseModel):
    """Per-item instrument list entry."""
    test_item: str = ""
    name: str = ""
    manufacturer: str = ""
    model: str = ""
    serial_no: str = ""
    calibration_end: str = ""


class ReportDataRow(BaseModel):
    """A single data row from the per-item test data tables."""
    test_item: str = ""
    freq_mhz: str = ""
    reading: str = ""
    correction_db: str = ""
    result_dbua: str = ""
    limit_dbua: str = ""
    margin_db: str = ""
    note: str = ""


class ReportTocItem(BaseModel):
    """A single entry in the report's table of contents.

    Extracted deterministically from the PDF text (regex, not AI).
    Each entry maps one test item code to its page range in the report body.
    """
    code: str = ""            # EQ/MC01
    name: str = ""            # 电源线传导发射测量
    page_start: int = 0       # first page (e.g. 15)
    page_end: int = 0         # last page (e.g. 25), 0 if single-page or unparseable


class ReportData(BaseModel):
    """Complete extraction result for the final test report."""
    cover: ReportCoverInfo = Field(default_factory=ReportCoverInfo)
    results: list[ReportResultItem] = Field(default_factory=list)
    sample: ReportSampleInfo = Field(default_factory=ReportSampleInfo)
    instruments: list[ReportInstrument] = Field(default_factory=list)
    deduplicated_instruments: list[ReportInstrument] = Field(default_factory=list)
    data_rows: list[ReportDataRow] = Field(default_factory=list)
    toc: list[ReportTocItem] = Field(default_factory=list)
    meta: dict = Field(default_factory=dict)
    raw_text: str = ""
    # v3: per-item extractions (populated when using universal extractor)
    item_extractions: list["TestItemExtraction"] = Field(default_factory=list)
    code_validation_issues: list["ValidationIssue"] = Field(default_factory=list)
    extraction_quality: str = "complete"
    failed_passes: list[str] = Field(default_factory=list)
    extraction_metrics: dict = Field(default_factory=dict)


# ── v3 Universal extraction models (per-test-item, type-aware) ──────────


class LimitEntry(BaseModel):
    """A single row in an emission-type limit table."""
    freq_range: str = ""       # "0.15-0.30MHz"
    peak_limit: float | None = None
    avg_limit: float | None = None
    qp_limit: float | None = None
    unit: str = ""             # dBμV / dBμA


class UniversalDataRow(BaseModel):
    """A single data row supporting both emission and immunity fields.

    Emission: freq_mhz, reading, correction_db, result_dbua, limit_dbua, margin_db
    Immunity: test_item, injection_point, spec_requirement, test_duration,
              required_level, actual_level, verdict
    """
    freq_mhz: str = ""
    reading: str = ""
    correction_db: str = ""
    result_dbua: str = ""
    limit_dbua: str = ""
    margin_db: str = ""
    test_item: str = ""
    injection_point: str = ""
    spec_requirement: str = ""
    test_duration: str = ""
    required_level: str = ""
    actual_level: str = ""
    verdict: str = ""
    note: str = ""


class SampleResultBlock(BaseModel):
    """Per-sample result data within a test item."""
    sample_id: str = ""
    mode: str = ""
    test_method: str = ""
    data_rows: list[UniversalDataRow] = Field(default_factory=list)


class TestResultsSection(BaseModel):
    """The test results section of a test item extraction."""
    background_data: list[UniversalDataRow] = Field(default_factory=list)
    sample_data: list[SampleResultBlock] = Field(default_factory=list)


class TestItemExtraction(BaseModel):
    """Complete extraction result for a single test item (emission or immunity)."""
    test_type: str = ""              # "emission" | "immunity"
    test_item_code: str = ""         # EQ/MC01
    test_item_name: str = ""
    limit_type: str = ""             # 频段限值 / 一般限值 / 基础限值
    limit_unit: str = ""             # dBμV / dBμA
    limit_entries: list[LimitEntry] = Field(default_factory=list)
    spec_parameters: list[dict] = Field(default_factory=list)
    required_level: str = ""
    test_procedures: list[str] = Field(default_factory=list)
    test_results: TestResultsSection = Field(default_factory=TestResultsSection)
    raw_section_text: str = ""
    ai_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ValidationIssue(BaseModel):
    """A code-level validation issue found during deterministic checks."""
    check_id: str = ""               # e.g. "C01", "C03"
    severity: str = "WARNING"        # CRITICAL | WARNING | INFO
    category: str = ""               # formula | background_noise | level_consistency
    field_name: str = ""
    test_item_code: str = ""
    description: str = ""
    expected: str = ""
    actual: str = ""
    row_index: int = -1              # 0-based, -1 = not applicable
