import { useEffect, useMemo, useRef, useState } from "react";
import { Modal, Textarea } from "@mantine/core";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowLeft,
  ArrowRight,
  Bug,
  Check,
  ChevronDown,
  Copy,
  Crosshair,
  ExternalLink,
  Filter,
  Maximize2,
  Minimize2,
  PanelLeftClose,
  Search,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import { notifications } from "@mantine/notifications";
import {
  apiErrorMessage,
  decideReviewFinding,
  fetchAuthenticatedBlobWithHeaders,
  getArchiveMemberInventory,
  getEvidencePagePreviewUrl,
  openAuthenticatedResource,
} from "../../api";
import { useSession } from "../../App";
import type {
  DocType,
  DocumentSetOverview,
  EvidenceGraphEvidence,
  EvidenceGraphFinding,
  EvidenceGraphSnapshot,
  FindingPresentation,
  FindingDecision,
} from "../../types";
import { DOC_LABELS } from "../../types";
import {
  EmptyState,
  EvidenceRail,
  LoadingState,
  StatusPill,
} from "../../components/common";
import { instrumentComparisonFor } from "../../reviewLogic";

type Group = "missing" | "conflict" | "incomplete" | "confirm" | "complete";
type HighlightKind = "error" | "warning" | "success" | "reference";
interface EvidencePageGroup {
  key: string;
  records: EvidenceGraphEvidence[];
  sourceCount: number;
}
const groupMeta: Record<Group, { label: string; note: string }> = {
  missing: { label: "资料缺失", note: "计划要求未形成完整结果证据" },
  conflict: { label: "内容不一致", note: "同一事实在资料间出现矛盾" },
  incomplete: { label: "报告未填写完整", note: "报告必要内容缺失" },
  confirm: { label: "需要人工确认", note: "证据不足以自动裁定" },
  complete: { label: "证据链完整", note: "机器检查已闭合" },
};
const decisionOptions = [
  {
    code: "report_revision",
    label: "要求修改报告",
    decision: "confirmed" as const,
    status: "open",
  },
  {
    code: "raw_record_supplement",
    label: "要求补充原始记录",
    decision: "confirmed" as const,
    status: "open",
  },
  {
    code: "source_correction",
    label: "核对并修正资料",
    decision: "confirmed" as const,
    status: "open",
  },
  {
    code: "not_applicable",
    label: "本次不适用",
    decision: "dismissed" as const,
    status: "not_applicable",
  },
  {
    code: "false_positive",
    label: "机器判断有误",
    decision: "dismissed" as const,
    status: "dismissed",
  },
  {
    code: "deferred",
    label: "暂缓判断",
    decision: "unresolved" as const,
    status: "deferred",
  },
];

function presentationForFinding(
  finding: EvidenceGraphFinding,
): FindingPresentation {
  const stored = finding.metadata?.presentation;
  const storedPresentation =
    stored && typeof stored === "object"
      ? (stored as FindingPresentation)
      : undefined;
  const instrumentComparison = instrumentComparisonFor(finding);
  if (instrumentComparison && storedPresentation) {
    return {
      ...storedPresentation,
      subject: "检测报告与原始记录的仪器清单",
      checked: `逐台核对仪器身份（厂家、型号、编号）：检测报告 ${instrumentComparison.reportTotal} 台，原始记录 ${instrumentComparison.rawTotal} 台`,
      issue: instrumentComparison.issue,
      impact:
        "两份仪器清单未完全一致，无法确认报告列出的全部仪器都在实际执行记录中留有对应记录。",
    };
  }
  if (storedPresentation) return storedPresentation;
  const statusKey =
    finding.status === "confirmed_pass"
      ? "complete"
      : finding.status === "unresolved" || finding.status === "unresolved_advisory"
        ? "confirmation"
        : finding.status === "confirmed_error"
          ? "error"
          : "advisory";
  const statusLabel =
    statusKey === "complete"
      ? "证据完整"
      : statusKey === "error"
        ? "需要处理"
        : statusKey === "confirmation"
          ? "需要人工确认"
          : "需要关注";
  const metadata = finding.metadata || {};
  const rawSubject = String(
    metadata.test_item || metadata.item_name || metadata.report_name ||
      metadata.context_name || metadata.scope_name || finding.title,
  );
  const subject = ({
    client_name: "客户名称",
    client_address: "客户地址",
    sample_name: "样品名称",
    sample_model: "样品型号",
    report_no: "报告编号",
    test_plan_no: "测试计划编号",
  } as Record<string, string>)[rawSubject] || rawSubject.replace(
    /(执行与发布证据完整|证据尚未闭合|缺少必要执行或发布证据|未找到执行记录和报告结果|未找到原始记录执行证据|未找到检测报告发布证据)$/,
    "",
  ).trim() || "当前审核对象";
  const fallback: FindingPresentation = {
    version: "legacy-fallback",
    status_key: statusKey,
    status_label: statusLabel,
    subject,
    checked: "本次审核规则定义的原文证据和跨资料关系",
    issue: finding.description || finding.title,
    impact:
      statusKey === "complete"
        ? "证据链已闭合，无需处理。"
        : statusKey === "confirmation"
          ? "证据尚不足以自动裁定，不能直接判定为错误或通过。"
          : "当前证据链尚未闭合。",
  };
  if (!instrumentComparison) return fallback;
  return {
    ...fallback,
    subject: "检测报告与原始记录的仪器清单",
    checked: `逐台核对仪器身份（厂家、型号、编号）：检测报告 ${instrumentComparison.reportTotal} 台，原始记录 ${instrumentComparison.rawTotal} 台`,
    issue: instrumentComparison.issue,
    impact:
      "两份仪器清单未完全一致，无法确认报告列出的全部仪器都在实际执行记录中留有对应记录。",
  };
}

function findingGroup(finding: EvidenceGraphFinding): Group {
  if (finding.status === "confirmed_pass") return "complete";
  // An unresolved coverage identity is a confirmation task, not proof that
  // the source material is missing.  Only a conclusive missing proof belongs
  // in the missing queue.
  if (
    finding.status === "unresolved" ||
    finding.status === "unresolved_advisory" ||
    /IDENTITY|REVIEW/.test(finding.check_id)
  )
    return "confirm";
  if (
    finding.check_id === "GRAPH-COVERAGE-001" ||
    finding.metadata?.missing_docs
  )
    return "missing";
  if (/SIGN|PAGE|INCOMPLETE|REQUIRED-FIELD/.test(finding.check_id))
    return "incomplete";
  return "conflict";
}

function groupEvidenceByPage(
  evidence: EvidenceGraphEvidence[],
): EvidencePageGroup[] {
  const pages = new Map<string, EvidenceGraphEvidence[]>();
  evidence.forEach((item) => {
    const unitId = String(item.metadata?.unit_id || "");
    const sourceUnit = String(
      unitId.match(/member-(\d+)/)?.[1] ||
        item.metadata?.member_index ||
        item.metadata?.release_id ||
        "",
    );
    const key = [
      item.doc_id,
      item.filename,
      item.page_number || 0,
      sourceUnit,
    ].join("::");
    pages.set(key, [...(pages.get(key) || []), item]);
  });
  return [...pages].map(([key, records]) => {
    const seen = new Set<string>();
    const unique = records.filter((item) => {
      const bbox = item.bbox?.length ? item.bbox.join(",") : "";
      const quote = item.exact_quote?.trim() || "";
      const cell = `${item.sheet_name || ""}:${item.cell_range || ""}`;
      const anchor = bbox || quote || (cell !== ":" ? cell : item.evidence_id);
      if (seen.has(anchor)) return false;
      seen.add(anchor);
      return true;
    });
    return { key, records: unique.slice(0, 12), sourceCount: records.length };
  });
}

function highlightKind(
  evidence: EvidenceGraphEvidence,
  finding?: EvidenceGraphFinding,
): HighlightKind {
  if (!finding) return "warning";
  if (finding.check_id === "DOC-INSTRUMENT-CONSISTENCY-001") {
    const role = String(evidence.metadata?.role || "");
    if (["matched_report", "matched_raw", "raw_inventory"].includes(role))
      return "success";
    if (["report_only", "raw_only"].includes(role)) return "error";
  }
  if (findingGroup(finding) === "complete") return "success";
  const verdict = String(
    evidence.metadata?.verdict || evidence.metadata?.state || "",
  ).toLowerCase();
  if (verdict === "required" || findingGroup(finding) === "missing")
    return "reference";
  if (/fail|error|conflict|invalid/.test(verdict)) return "error";
  if (/pass|ok|match|valid/.test(verdict)) return "success";
  return finding.severity === "warning" ? "warning" : "error";
}

function instrumentEvidenceRoleLabel(evidence: EvidenceGraphEvidence) {
  if (evidence.metadata?.role === "report_only") return "报告额外";
  if (evidence.metadata?.role === "raw_only") return "原始记录额外";
  if (
    ["matched_report", "matched_raw", "raw_inventory"].includes(
      String(evidence.metadata?.role || ""),
    )
  )
    return "已匹配";
  return "";
}

function evidenceMemberIndex(evidence: EvidenceGraphEvidence) {
  const unitId = String(evidence.metadata?.unit_id || "");
  return Number(
    unitId.match(/member-(\d+)/)?.[1] || evidence.metadata?.member_index || 0,
  );
}

function evidenceMemberLabel(evidence: EvidenceGraphEvidence) {
  const member = evidenceMemberIndex(evidence);
  return member ? `压缩包内第 ${member} 份文件` : "";
}

function evidenceTechnicalLocator(evidence: EvidenceGraphEvidence) {
  const unit = evidenceMemberLabel(evidence);
  const location = evidence.sheet_name
    ? `${evidence.sheet_name}${evidence.cell_range ? `:${evidence.cell_range}` : ""}`
    : `page-${evidence.page_number || "unknown"}`;
  return `${evidence.evidence_id} · ${evidence.doc_type}:${location}${unit ? `:${unit.replace(" ", "-")}` : ""}`;
}

function evidenceSourceLocation(evidence: EvidenceGraphEvidence) {
  const member = evidenceMemberLabel(evidence);
  const page = evidence.sheet_name
    ? `${evidence.sheet_name}${evidence.cell_range ? `:${evidence.cell_range}` : ""}`
    : `第 ${evidence.page_number || "—"} 页`;
  return `${page}${member ? ` · ${member}` : ""}`;
}

function evidenceAnchorLabel(evidence: EvidenceGraphEvidence) {
  return evidence.bbox?.length
    ? "已定位高亮"
    : "本页证据尚未定位到高亮区域";
}

function useEvidencePreview(url: string) {
  const empty = {
    source: "",
    anchorY: [] as Array<number | null>,
    highlightCount: 0,
    highlightStrategy: "none",
    cacheStatus: "",
    loading: false,
    error: "",
  };
  const [preview, setPreview] = useState(empty);
  useEffect(() => {
    if (!url) {
      setPreview(empty);
      return;
    }
    const controller = new AbortController();
    let objectUrl = "";
    setPreview({ ...empty, loading: true });
    fetchAuthenticatedBlobWithHeaders(url, controller.signal)
      .then(
        ({
          blob,
          anchorY: anchorHeader,
          highlightCount,
          highlightStrategy,
          cacheStatus,
        }) => {
          if (controller.signal.aborted) return;
          const anchorY = anchorHeader.split(",").map((value) => {
            const position = Number(value);
            return value !== "" && Number.isFinite(position) ? position : null;
          });
          objectUrl = URL.createObjectURL(blob);
          setPreview({
            source: objectUrl,
            anchorY,
            highlightCount,
            highlightStrategy,
            cacheStatus,
            loading: false,
            error: "",
          });
        },
      )
      .catch((error) => {
        if (controller.signal.aborted) return;
        if (error instanceof DOMException && error.name === "AbortError")
          return;
        setPreview({
          ...empty,
          error: error instanceof Error ? error.message : "证据原图加载失败",
        });
      });
    return () => {
      controller.abort();
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [url]);
  return preview;
}

function EvidenceImageViewport({
  url,
  alt,
  activeAnchor,
  focused,
  zoom = 100,
  className,
  onOpen,
  onLocationChange,
}: {
  url: string;
  alt: string;
  activeAnchor: number;
  focused: boolean;
  zoom?: number;
  className: string;
  onOpen?: () => void;
  onLocationChange?: (located: boolean) => void;
}) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const imageRef = useRef<HTMLImageElement>(null);
  const preview = useEvidencePreview(url);
  const located =
    preview.highlightCount > 0 &&
    preview.anchorY.some((position) => position != null);
  const scrollToAnchor = (behavior: ScrollBehavior = "smooth") => {
    const viewport = viewportRef.current;
    const image = imageRef.current;
    if (!viewport || !image) return;
    const anchorY = preview.anchorY[activeAnchor];
    if (!focused || !located || anchorY == null) {
      viewport.scrollTo({ top: 0, behavior });
      return;
    }
    const top =
      image.offsetTop +
      image.clientHeight * anchorY -
      viewport.clientHeight / 2;
    viewport.scrollTo({ top: Math.max(0, top), behavior });
  };
  useEffect(() => {
    const frame = window.requestAnimationFrame(() => scrollToAnchor("smooth"));
    return () => window.cancelAnimationFrame(frame);
  }, [
    preview.source,
    preview.anchorY,
    preview.highlightCount,
    activeAnchor,
    focused,
    zoom,
  ]);
  useEffect(() => {
    onLocationChange?.(located);
  }, [located, onLocationChange]);
  return (
    <div
      ref={viewportRef}
      data-active-anchor={activeAnchor}
      data-anchor-y={preview.anchorY
        .map((position) => (position == null ? "" : position.toFixed(6)))
        .join(",")}
      data-highlight-count={preview.highlightCount}
      data-highlight-strategy={preview.highlightStrategy}
      data-preview-cache={preview.cacheStatus}
      className={`${className} ${focused && located ? "is-focused" : "is-full-page"} ${located ? "is-located" : "is-unlocated"}`}
    >
      {preview.loading && (
        <div className="evidence-image-state">正在加载完整页面…</div>
      )}
      {preview.error && (
        <div className="evidence-image-state error">{preview.error}</div>
      )}
      {!preview.loading && !preview.error && preview.source && !located && (
        <div className="evidence-image-state error">
          本页证据尚未定位到高亮区域
        </div>
      )}
      {onOpen && preview.source && (
        <button
          type="button"
          className="evidence-preview-open"
          onClick={onOpen}
        >
          <Maximize2 />
          点击查看大图
        </button>
      )}
      {preview.source && (
        <img
          ref={imageRef}
          onLoad={() => scrollToAnchor("auto")}
          onClick={onOpen}
          style={{ width: `${zoom}%` }}
          src={preview.source}
          alt={alt}
        />
      )}
    </div>
  );
}

function EvidenceComparisonPanel({
  setId,
  graphId,
  page,
  finding,
  demo,
  onOpen,
}: {
  setId: string;
  graphId: string;
  page: EvidencePageGroup;
  finding: EvidenceGraphFinding;
  demo: boolean;
  onOpen: (pageKey: string) => void;
}) {
  const [anchor, setAnchor] = useState(0);
  const [focused, setFocused] = useState(true);
  const evidence = page.records[0];
  const kinds = page.records.map((item) => highlightKind(item, finding));
  const preview = getEvidencePagePreviewUrl(
    setId,
    graphId,
    evidence.evidence_id,
    page.records.slice(1).map((item) => item.evidence_id),
    kinds,
  );
  const memberIndex = evidenceMemberIndex(evidence);
  const inventoryQuery = useQuery({
    queryKey: ["archive-members", setId, evidence.doc_id],
    queryFn: () => getArchiveMemberInventory(setId, evidence.doc_id),
    enabled:
      !demo && evidence.doc_type === "original_records" && memberIndex > 0,
    staleTime: 5 * 60_000,
  });
  const memberFilename =
    inventoryQuery.data?.members.find(
      (item) => item.member_index === memberIndex,
    )?.filename || String(evidence.metadata?.source_filename || "");
  const member = memberIndex
    ? `压缩包内文件${inventoryQuery.data?.total ? ` ${memberIndex}/${inventoryQuery.data.total}` : ""}`
    : "";
  const displayFilename =
    memberFilename ||
    (memberIndex ? `压缩包内第 ${memberIndex} 份文件` : evidence.filename);
  const location = evidence.sheet_name
    ? `${evidence.sheet_name} ${evidence.cell_range || ""}`
    : `文件第 ${evidence.page_number || "—"} 页`;
  return (
    <article className={`evidence-compare-card ${kinds[anchor] || "warning"}`}>
      <header>
        <div>
          <span>
            {DOC_LABELS[evidence.doc_type]}
            {member ? ` · ${member}` : ""}
          </span>
          <h3>{displayFilename}</h3>
          <p>
            {location}
            {page.records.length > 1 ? ` · ${page.records.length} 处标记` : ""}
            {page.sourceCount > page.records.length
              ? ` · 已合并 ${page.sourceCount} 条重复引用`
              : ""}
          </p>
        </div>
        {page.records.length > 1 && (
          <div className="evidence-card-stepper">
            <button
              aria-label="上一处高亮"
              disabled={anchor === 0}
              onClick={() => {
                setAnchor((value) => Math.max(0, value - 1));
                setFocused(true);
              }}
            >
              <ArrowLeft />
            </button>
            <b>
              {anchor + 1}/{page.records.length}
            </b>
            <button
              aria-label="下一处高亮"
              disabled={anchor === page.records.length - 1}
              onClick={() => {
                setAnchor((value) =>
                  Math.min(page.records.length - 1, value + 1),
                );
                setFocused(true);
              }}
            >
              <ArrowRight />
            </button>
          </div>
        )}
      </header>
      {demo ? (
        <button
          className={`evidence-compare-preview ${focused ? "is-focused" : ""}`}
          onClick={() => onOpen(page.key)}
          aria-label={`打开${DOC_LABELS[evidence.doc_type]}${member ? member : ""}${location}大图`}
        >
          <MockEvidencePage page={page} kinds={kinds} zoom={100} />
        </button>
      ) : (
        <EvidenceImageViewport
          className="evidence-compare-preview"
          url={preview}
          activeAnchor={anchor}
          focused={focused}
          alt={`${DOC_LABELS[evidence.doc_type]}${member ? member : ""}${location}完整页高亮原文`}
          onOpen={() => onOpen(page.key)}
        />
      )}
    </article>
  );
}

function CoverageMissingPanel({
  docType,
  checkedRecords,
  checkedSources,
}: {
  docType: DocType;
  checkedRecords: number;
  checkedSources: number;
}) {
  return (
    <article className="evidence-missing-card">
      <header>
        <span>{DOC_LABELS[docType]}</span>
        <b>未找到对应项目证据</b>
      </header>
      <div>
        <span>
          <Search />
        </span>
        <h3>该资料中没有定位到本测试项目</h3>
        <p>
          系统已完成范围检索，但检索范围不等于直接证据，因此不再把无关页面逐张展示。
        </p>
      </div>
      <footer>
        <b>{checkedRecords}</b> 条检索记录 · <b>{checkedSources}</b>{" "}
        个来源单元已核查并收起
      </footer>
    </article>
  );
}

export default function FindingsStage({
  setId,
  overview,
  snapshot,
  loading,
  demo,
  drawerOpen,
  onDrawerClose,
}: {
  setId: string;
  overview?: DocumentSetOverview;
  snapshot?: EvidenceGraphSnapshot;
  loading: boolean;
  demo: boolean;
  drawerOpen: boolean;
  onDrawerClose: () => void;
}) {
  const { user } = useSession();
  const queryClient = useQueryClient();
  const [filter, setFilter] = useState<"attention" | "all" | "complete">(
    "attention",
  );
  const [query, setQuery] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [selectedEvidenceId, setSelectedEvidenceId] = useState("");
  const [zoom, setZoom] = useState(100);
  const [lightbox, setLightbox] = useState(false);
  const [focusMode, setFocusMode] = useState(true);
  const [currentPreviewLocated, setCurrentPreviewLocated] = useState(false);
  const previewLocated = demo || currentPreviewLocated;
  const [activeAnchor, setActiveAnchor] = useState(0);
  const [viewerWide, setViewerWide] = useState(false);
  const [resolution, setResolution] = useState("");
  const [comment, setComment] = useState("");
  const [saving, setSaving] = useState(false);
  const [detailWidth, setDetailWidth] = useState(340);
  const [technicalOpen, setTechnicalOpen] = useState(false);
  const [localDecisions, setLocalDecisions] = useState<FindingDecision[]>(
    snapshot?.decisions || [],
  );
  const dragStart = useRef<{ x: number; width: number } | null>(null);
  const findings = snapshot?.findings || [];
  const evidenceById = useMemo(
    () =>
      new Map(
        (snapshot?.evidence || []).map((item) => [item.evidence_id, item]),
      ),
    [snapshot?.evidence],
  );
  const decisionById = useMemo(
    () => new Map(localDecisions.map((item) => [item.finding_id, item])),
    [localDecisions],
  );
  const display = useMemo(
    () =>
      findings.filter((finding) => {
        const group = findingGroup(finding);
        const text =
          `${finding.title} ${finding.description} ${finding.metadata?.test_item || ""}`.toLowerCase();
        const filterMatch =
          filter === "all" ||
          (filter === "complete" ? group === "complete" : group !== "complete");
        return filterMatch && text.includes(query.trim().toLowerCase());
      }),
    [findings, filter, query],
  );
  const selected =
    findings.find((item) => item.finding_id === selectedId) || display[0];
  const selectedPresentation = selected
    ? presentationForFinding(selected)
    : undefined;
  const instrumentComparison = selected
    ? instrumentComparisonFor(selected)
    : undefined;
  const allSelectedEvidence =
    (selected?.evidence_ids
      .map((id) => evidenceById.get(id))
      .filter(Boolean) as EvidenceGraphEvidence[]) || [];
  const isCoverageFinding = selected?.check_id === "GRAPH-COVERAGE-001";
  const missingDocs = (
    Array.isArray(selected?.metadata?.missing_docs)
      ? selected.metadata.missing_docs
      : []
  ).filter(
    (doc: unknown): doc is DocType =>
      typeof doc === "string" && doc in DOC_LABELS,
  );
  // Missing-item findings retain all pages searched to prove absence. Keep them in the snapshot,
  // but do not present those unrelated pages as direct evidence for the selected test item.
  const coverageProofEvidence = isCoverageFinding
    ? allSelectedEvidence.filter((item) => missingDocs.includes(item.doc_type))
    : [];
  const selectedEvidence = isCoverageFinding
    ? allSelectedEvidence.filter((item) => !missingDocs.includes(item.doc_type))
    : allSelectedEvidence;
  const evidencePages = groupEvidenceByPage(selectedEvidence);
  const showEvidenceBoard =
    evidencePages.length > 1 || (isCoverageFinding && missingDocs.length > 0);
  const evidenceBoardCount =
    evidencePages.length + (isCoverageFinding ? missingDocs.length : 0);
  const currentEvidencePage =
    evidencePages.find((item) => item.key === selectedEvidenceId) ||
    evidencePages[0];
  const currentEvidence = currentEvidencePage?.records[0];
  const currentHighlightKinds =
    currentEvidencePage?.records.map((item) => highlightKind(item, selected)) ||
    [];
  const previewUrl =
    currentEvidence && currentEvidencePage
      ? getEvidencePagePreviewUrl(
          setId,
          snapshot?.run.graph_id || "",
          currentEvidence.evidence_id,
          currentEvidencePage.records.slice(1).map((item) => item.evidence_id),
          currentHighlightKinds,
        )
      : "";
  const selectedDecision = selected
    ? decisionById.get(selected.finding_id)
    : undefined;
  const attention = findings.filter(
    (item) => findingGroup(item) !== "complete",
  );
  const decided = attention.filter((item) =>
    decisionById.has(item.finding_id),
  ).length;
  const technicalLocators = selectedEvidence.map(evidenceTechnicalLocator);
  const technicalBundle = selected
    ? JSON.stringify(
        {
          set_id: setId,
          graph_id: snapshot?.run.graph_id || "",
          finding_id: selected.finding_id,
          check_id: selected.check_id,
          direct_evidence: technicalLocators,
          all_evidence_ids: allSelectedEvidence.map((item) => item.evidence_id),
        },
        null,
        2,
      )
    : "";

  async function copyTechnical(value: string, label: string) {
    try {
      await navigator.clipboard.writeText(value);
      notifications.show({
        color: "green",
        title: `${label}已复制`,
        message: value,
      });
    } catch {
      notifications.show({
        color: "red",
        title: "复制失败",
        message: "浏览器没有授予剪贴板权限。",
      });
    }
  }

  useEffect(() => {
    if (
      display.length &&
      !display.some((item) => item.finding_id === selectedId)
    )
      setSelectedId(display[0].finding_id);
  }, [display, selectedId]);
  useEffect(() => {
    setLocalDecisions(snapshot?.decisions || []);
  }, [snapshot?.decisions]);
  useEffect(() => {
    setSelectedEvidenceId(evidencePages[0]?.key || "");
    setZoom(100);
    setFocusMode(true);
    setCurrentPreviewLocated(false);
    setActiveAnchor(0);
    setTechnicalOpen(false);
    setResolution(selectedDecision?.resolution_code || "");
    setComment(selectedDecision?.comment || "");
  }, [selected?.finding_id]);
  useEffect(() => {
    setActiveAnchor(0);
    setFocusMode(true);
    setCurrentPreviewLocated(false);
  }, [currentEvidencePage?.key]);
  useEffect(() => {
    const move = (event: PointerEvent) => {
      if (dragStart.current)
        setDetailWidth(
          Math.max(
            340,
            Math.min(
              620,
              dragStart.current.width + dragStart.current.x - event.clientX,
            ),
          ),
        );
    };
    const up = () => {
      dragStart.current = null;
      document.body.classList.remove("is-resizing");
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
    return () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
    };
  }, []);
  useEffect(() => {
    const key = (event: KeyboardEvent) => {
      if (event.key === "+" || event.key === "=")
        setZoom((value) => Math.min(300, value + 20));
      if (event.key === "-") setZoom((value) => Math.max(50, value - 20));
      if (event.key === "Escape") {
        setLightbox(false);
        setViewerWide(false);
      }
    };
    window.addEventListener("keydown", key);
    return () => window.removeEventListener("keydown", key);
  }, []);

  async function saveDecision() {
    if (!selected || !resolution || !comment.trim()) {
      notifications.show({
        color: "yellow",
        title: "还不能保存",
        message: "请选择处理方式，并填写可以追溯的处理依据。",
      });
      return;
    }
    const option = decisionOptions.find((item) => item.code === resolution);
    if (!option) {
      notifications.show({
        color: "red",
        title: "处理方式无效",
        message: "该历史处理码已不受支持，请重新选择一项处理方式。",
      });
      return;
    }
    setSaving(true);
    try {
      if (!demo)
        await decideReviewFinding(
          setId,
          snapshot!.run.graph_id,
          selected.finding_id,
          {
            decision: option.decision,
            comment: comment.trim(),
            resolution_code: option.code,
            resolution_status: option.status,
          },
        );
      const saved: FindingDecision = {
        finding_id: selected.finding_id,
        decision: option.decision,
        comment: comment.trim(),
        resolution_code: option.code,
        resolution_status: option.status,
        actor_id: "当前审核员",
        created_at: new Date().toISOString(),
      };
      setLocalDecisions((current) => [
        ...current.filter((item) => item.finding_id !== selected.finding_id),
        saved,
      ]);
      if (demo)
        queryClient.setQueryData<EvidenceGraphSnapshot>(
          ["snapshot", setId, snapshot!.run.graph_id, demo],
          (current) =>
            current
              ? {
                  ...current,
                  decisions: [
                    ...(current.decisions || []).filter(
                      (item) => item.finding_id !== selected.finding_id,
                    ),
                    saved,
                  ],
                }
              : current,
        );
      notifications.show({
        color: "green",
        title: "人工结论已保存",
        message: `${option.label}：处理依据已写入审核快照。`,
      });
      queryClient.invalidateQueries({ queryKey: ["snapshot", setId] });
      // A decision changes the task queue counters and dashboard summaries.
      // Refresh those caches together with the current snapshot so returning
      // to another page never shows the previous pending count.
      queryClient.invalidateQueries({ queryKey: ["sets", demo] });
      queryClient.invalidateQueries({ queryKey: ["stats", demo] });
    } catch (error) {
      notifications.show({
        color: "red",
        title: "保存失败",
        message: apiErrorMessage(error),
      });
    } finally {
      setSaving(false);
    }
  }
  function selectFinding(id: string) {
    setSelectedId(id);
    onDrawerClose();
  }
  function nextFinding() {
    const index = display.findIndex(
      (item) => item.finding_id === selected?.finding_id,
    );
    if (display[index + 1]) setSelectedId(display[index + 1].finding_id);
  }

  if (loading) return <LoadingState label="正在加载问题、证据与人工结论…" />;
  if (!snapshot)
    return (
      <EmptyState
        icon="error"
        title="本次审核没有可读取的快照"
        description="返回机器审核步骤检查运行状态；失败不能被视为无问题。"
      />
    );
  return (
    <div className="findings-stage">
      <header className="findings-summary">
        <div>
          <span className="eyebrow">HUMAN DECISION</span>
          <h1>所见必有所据，所断必有所依。</h1>
        </div>
        <div className="finding-stats">
          <span className="danger">
            <b>
              {attention.filter((item) => item.severity === "error").length}
            </b>
            需要处理
          </span>
          <span className="warning">
            <b>
              {attention.filter((item) => item.severity !== "error").length}
            </b>
            需要确认
          </span>
          <span className="success">
            <b>
              {
                findings.filter((item) => findingGroup(item) === "complete")
                  .length
              }
            </b>
            证据完整
          </span>
        </div>
        <div className="decision-progress">
          <div>
            <span>人工裁决</span>
            <b>
              {decided} / {attention.length}
            </b>
          </div>
          <i>
            <em
              style={{
                width: `${attention.length ? (decided / attention.length) * 100 : 100}%`,
              }}
            />
          </i>
        </div>
      </header>
      <div
        className={`findings-workspace ${viewerWide ? "viewer-wide" : ""}`}
        style={{ "--detail-width": `${detailWidth}px` } as React.CSSProperties}
      >
        <aside className={`finding-queue ${drawerOpen ? "mobile-open" : ""}`}>
          <div className="queue-mobile-head">
            <b>问题列表</b>
            <button onClick={onDrawerClose}>
              <PanelLeftClose />
            </button>
          </div>
          <div className="queue-filters">
            <div>
              <button
                className={filter === "attention" ? "active" : ""}
                onClick={() => setFilter("attention")}
              >
                待处理 {attention.length}
              </button>
              <button
                className={filter === "all" ? "active" : ""}
                onClick={() => setFilter("all")}
              >
                全部 {findings.length}
              </button>
              <button
                className={filter === "complete" ? "active" : ""}
                onClick={() => setFilter("complete")}
              >
                已闭合
              </button>
            </div>
            <label>
              <Search size={14} />
              <input
                value={query}
                onChange={(event) => setQuery(event.currentTarget.value)}
                placeholder="搜索问题"
              />
              <Filter size={13} />
            </label>
          </div>
          <div className="queue-scroll">
            {(
              [
                "missing",
                "conflict",
                "incomplete",
                "confirm",
                "complete",
              ] as Group[]
            ).map((group) => {
              const groupItems = display.filter(
                (item) => findingGroup(item) === group,
              );
              return groupItems.length ? (
                <section className={`finding-group ${group}`} key={group}>
                  <div className="group-label">
                    <span>{groupMeta[group].label}</span>
                    <b>{groupItems.length}</b>
                  </div>
                  {groupItems.map((item) => {
                    const decision = decisionById.get(item.finding_id);
                    return (
                      <button
                        className={`${selected?.finding_id === item.finding_id ? "active" : ""} ${decision ? "decided" : ""}`}
                        key={item.finding_id}
                        onClick={() => selectFinding(item.finding_id)}
                      >
                        <div>
                          <span>
                            {item.metadata?.test_item || item.check_id}
                          </span>
                          <em>
                            {decision
                              ? "已处理"
                              : group === "complete"
                                ? "已闭合"
                                : "待处理"}
                          </em>
                        </div>
                        <h3>{item.title}</h3>
                        <p>{decision?.comment || item.description}</p>
                      </button>
                    );
                  })}
                </section>
              ) : null;
            })}
            {!display.length && (
              <EmptyState
                icon="search"
                title="没有匹配的问题"
                description="清除搜索或调整筛选条件。"
              />
            )}
          </div>
        </aside>
        {selected ? (
          <>
            <main className="evidence-pane">
              <div className="evidence-pane-head">
                <div>
                  <StatusPill status={selected.status}>
                    {findingGroup(selected) === "complete"
                      ? "证据完整"
                      : selected.severity === "error"
                        ? "需要处理"
                        : "需要确认"}
                  </StatusPill>
                  <h2>{selected.metadata?.test_item || selected.title}</h2>
                  <p>{selected.title}</p>
                </div>
                <span>
                  {findings.indexOf(selected) + 1} / {findings.length}
                </span>
              </div>
              <EvidenceRail
                states={Object.fromEntries(
                  (
                    [
                      "order_form",
                      "test_plan",
                      "original_records",
                      "final_report",
                    ] as DocType[]
                  ).map((doc) => [
                    doc,
                    selected.metadata?.missing_docs?.includes(doc)
                      ? "missing"
                      : selectedEvidence.some((item) => item.doc_type === doc)
                        ? selected.severity === "error"
                          ? "warn"
                          : "ok"
                        : "idle",
                  ]),
                )}
              />
              {showEvidenceBoard ? (
                <>
                  <div className="evidence-compare-head">
                    <div>
                      <b>
                        {isCoverageFinding
                          ? "同页核对必要证据"
                          : "同页查看全部证据"}
                      </b>
                      <span>
                        {isCoverageFinding
                          ? `${evidenceBoardCount} 个资料板块 · ${evidencePages.length} 个直接证据板块 · ${coverageProofEvidence.length} 条检索范围记录已收起`
                          : `${evidencePages.length} 个来源板块 · 文件名、成员、位置和原文均完整展示`}
                      </span>
                    </div>
                    <button onClick={() => setViewerWide((value) => !value)}>
                      {viewerWide ? <Minimize2 /> : <Maximize2 />}
                      {viewerWide ? "退出专注对比" : "专注对比"}
                    </button>
                  </div>
                  <div className="evidence-compare-grid">
                    {evidencePages.map((page) => (
                      <EvidenceComparisonPanel
                        key={page.key}
                        setId={setId}
                        graphId={snapshot.run.graph_id}
                        page={page}
                        finding={selected}
                        demo={demo}
                        onOpen={(pageKey) => {
                          setSelectedEvidenceId(pageKey);
                          setFocusMode(true);
                          setLightbox(true);
                        }}
                      />
                    ))}
                    {isCoverageFinding &&
                      missingDocs.map((doc) => {
                        const searched = coverageProofEvidence.filter(
                          (item) => item.doc_type === doc,
                        );
                        return (
                          <CoverageMissingPanel
                            key={`missing-${doc}`}
                            docType={doc}
                            checkedRecords={searched.length}
                            checkedSources={
                              groupEvidenceByPage(searched).length
                            }
                          />
                        );
                      })}
                  </div>
                </>
              ) : currentEvidence ? (
                <>
                  <div className="evidence-toolbar">
                    <div>
                      <b>{currentEvidence.filename}</b>
                      <span>
                        {evidenceMemberLabel(currentEvidence)
                          ? `${evidenceMemberLabel(currentEvidence)} · `
                          : ""}
                        {currentEvidence.sheet_name
                          ? `${currentEvidence.sheet_name} ${currentEvidence.cell_range || ""}`
                          : `文件第 ${currentEvidence.page_number || "—"} 页`}
                      </span>
                    </div>
                    <div className="evidence-view-actions">
                      <button
                        disabled={!previewLocated}
                        className={focusMode && previewLocated ? "active" : ""}
                        aria-pressed={focusMode && previewLocated}
                        onClick={() => setFocusMode((value) => !value)}
                      >
                        <Crosshair />
                        {!previewLocated
                          ? "未定位高亮"
                          : focusMode
                            ? "已定位高亮"
                            : "查看整页"}
                      </button>
                      {currentEvidencePage.records.length > 1 && (
                        <div className="anchor-stepper">
                          <button
                            aria-label="上一处高亮"
                            disabled={activeAnchor === 0}
                            onClick={() => {
                              setActiveAnchor((value) =>
                                Math.max(0, value - 1),
                              );
                              setFocusMode(true);
                            }}
                          >
                            <ArrowLeft />
                          </button>
                          <b>
                            {activeAnchor + 1}/
                            {currentEvidencePage.records.length}
                          </b>
                          <button
                            aria-label="下一处高亮"
                            disabled={
                              activeAnchor ===
                              currentEvidencePage.records.length - 1
                            }
                            onClick={() => {
                              setActiveAnchor((value) =>
                                Math.min(
                                  currentEvidencePage.records.length - 1,
                                  value + 1,
                                ),
                              );
                              setFocusMode(true);
                            }}
                          >
                            <ArrowRight />
                          </button>
                        </div>
                      )}
                      <button
                        aria-label="缩小证据页"
                        onClick={() =>
                          setZoom((value) => Math.max(60, value - 20))
                        }
                      >
                        <ZoomOut />
                      </button>
                      <span>{zoom}%</span>
                      <button
                        aria-label="放大证据页"
                        onClick={() =>
                          setZoom((value) => Math.min(220, value + 20))
                        }
                      >
                        <ZoomIn />
                      </button>
                      <button onClick={() => setViewerWide((value) => !value)}>
                        {viewerWide ? <Minimize2 /> : <Maximize2 />}
                        {viewerWide ? "退出专注" : "专注看图"}
                      </button>
                      <button onClick={() => setLightbox(true)}>
                        <Maximize2 />
                        全屏
                      </button>
                    </div>
                  </div>
                  {demo ? (
                    <div
                      className={`evidence-canvas ${focusMode ? "is-focused" : ""}`}
                    >
                      <MockEvidencePage
                        page={currentEvidencePage}
                        kinds={currentHighlightKinds}
                        zoom={zoom}
                      />
                    </div>
                  ) : (
                    <EvidenceImageViewport
                      className="evidence-canvas"
                      url={previewUrl}
                      activeAnchor={activeAnchor}
                      focused={focusMode}
                      zoom={zoom}
                      onLocationChange={setCurrentPreviewLocated}
                      alt={`${DOC_LABELS[currentEvidence.doc_type]}第${currentEvidence.page_number || ""}页完整原图${focusMode && previewLocated ? `，已滚动到第${activeAnchor + 1}处高亮` : ""}`}
                    />
                  )}
                </>
              ) : (
                <EmptyState
                  title="这条问题没有视觉证据"
                  description="缺失类问题可能只有计划要求证据；无证据的自动判错不应进入裁决队列。"
                />
              )}
            </main>
            <div
              className="panel-resizer"
              role="separator"
              aria-orientation="vertical"
              aria-label="调整问题详情宽度"
              onPointerDown={(event) => {
                dragStart.current = { x: event.clientX, width: detailWidth };
                document.body.classList.add("is-resizing");
              }}
            />
            <aside className="finding-detail">
              <div className={`finding-alert ${selected.severity}`}>
                <span>
                  {findingGroup(selected) === "complete" ? (
                    <Check />
                  ) : (
                    <AlertTriangle />
                  )}
                </span>
                <div>
                  <small>系统发现了什么</small>
                  <h2>{selectedPresentation?.status_label || "需要确认"}</h2>
                  <p>{selectedPresentation?.issue || selected.description}</p>
                </div>
              </div>
              <section>
                <div className="detail-section-title">
                  <h3>问题说明</h3>
                  <ChevronDown />
                </div>
                <dl className="comparison-list">
                  <div>
                    <dt>状态</dt>
                    <dd>{selectedPresentation?.status_label || "需要确认"}</dd>
                  </div>
                  <div>
                    <dt>核对对象</dt>
                    <dd>{selectedPresentation?.subject || "跨文档主体信息"}</dd>
                  </div>
                  <div>
                    <dt>已核对</dt>
                    <dd>{selectedPresentation?.checked || "本次审核规则定义的原文证据和跨资料关系"}</dd>
                  </div>
                  <div>
                    <dt>发现问题</dt>
                    <dd>{selectedPresentation?.issue || selected.description}</dd>
                  </div>
                  <div>
                    <dt>影响</dt>
                    <dd>{selectedPresentation?.impact || "当前证据链尚未闭合。"}</dd>
                  </div>
                  {Object.entries(
                    selected.metadata?.values_by_doc_type || {},
                  ).map(([doc, value]) => (
                    <div key={doc}>
                      <dt>{DOC_LABELS[doc as DocType] || doc}</dt>
                      <dd>
                        <ComparisonValue value={value} />
                      </dd>
                    </div>
                  ))}
                </dl>
              </section>
              {instrumentComparison && (
                <section className="instrument-set-comparison">
                  <div className="detail-section-title">
                    <h3>仪器清单配对结果</h3>
                    <span>
                      报告 {instrumentComparison.reportTotal} 台 · 原始记录 {instrumentComparison.rawTotal} 台
                    </span>
                  </div>
                  <div className="instrument-match-groups">
                    <article className="matched">
                      <header>
                        <b>已匹配</b>
                        <span>{instrumentComparison.matchedCount} 台</span>
                      </header>
                      {instrumentComparison.matched.map((row) => (
                        <p key={`matched-${row.manufacturer}-${row.model}-${row.serial_no}`}>
                          <b>{[row.manufacturer, row.model].filter(Boolean).join(" · ") || "未提取厂家/型号"}</b>
                          <span>仪器编号 {row.serial_no || "未提取"}</span>
                        </p>
                      ))}
                    </article>
                    <article className="different">
                      <header>
                        <b>检测报告额外</b>
                        <span>{instrumentComparison.reportOnlyCount} 台</span>
                      </header>
                      {instrumentComparison.reportOnly.length ? instrumentComparison.reportOnly.map((row) => (
                        <p key={`report-${row.manufacturer}-${row.model}-${row.serial_no}`}>
                          <b>{[row.manufacturer, row.model].filter(Boolean).join(" · ") || "未提取厂家/型号"}</b>
                          <span>仪器编号 {row.serial_no || "未提取"}</span>
                        </p>
                      )) : <em>无额外仪器</em>}
                    </article>
                    <article className="different">
                      <header>
                        <b>原始记录额外</b>
                        <span>{instrumentComparison.rawOnlyCount} 台</span>
                      </header>
                      {instrumentComparison.rawOnly.length ? instrumentComparison.rawOnly.map((row) => (
                        <p key={`raw-${row.manufacturer}-${row.model}-${row.serial_no}`}>
                          <b>{[row.manufacturer, row.model].filter(Boolean).join(" · ") || "未提取厂家/型号"}</b>
                          <span>仪器编号 {row.serial_no || "未提取"}</span>
                        </p>
                      )) : <em>无额外仪器</em>}
                    </article>
                  </div>
                </section>
              )}
              <section className="finding-source-summary">
                <div className="detail-section-title">
                  <h3>已找到的原文</h3>
                  <span>{selectedEvidence.length} 条直接证据</span>
                </div>
                <div className="finding-source-list">
                  {selectedEvidence.length ? selectedEvidence.map((evidence) => (
                    <button
                      type="button"
                      key={evidence.evidence_id}
                      className={instrumentEvidenceRoleLabel(evidence) ? `instrument-evidence ${highlightKind(evidence, selected)}` : ""}
                      onClick={() => {
                        const page = evidencePages.find((item) => item.records.some((record) => record.evidence_id === evidence.evidence_id));
                        if (page) {
                          setSelectedEvidenceId(page.key);
                          setFocusMode(true);
                        }
                      }}
                    >
                      <b>
                        {DOC_LABELS[evidence.doc_type] || evidence.doc_type}
                        {instrumentEvidenceRoleLabel(evidence) && (
                          <em>{instrumentEvidenceRoleLabel(evidence)}</em>
                        )}
                      </b>
                      <span>{evidence.exact_quote || "未提取原文"}</span>
                      <small>{evidenceSourceLocation(evidence)} · {evidenceAnchorLabel(evidence)}</small>
                    </button>
                  )) : <p>当前没有可展示的直接证据。</p>}
                </div>
              </section>
              {selectedPresentation?.llm_supplement?.status === "ready" && (
                <section className="finding-llm-supplement">
                  <div className="detail-section-title"><h3>补充判断</h3><span>基于已有证据</span></div>
                  <dl className="comparison-list">
                    <div><dt>判断</dt><dd>{selectedPresentation.llm_supplement.judgment}</dd></div>
                    <div><dt>判断依据</dt><dd>{selectedPresentation.llm_supplement.basis}</dd></div>
                    <div><dt>不确定点</dt><dd>{selectedPresentation.llm_supplement.uncertainty}</dd></div>
                    <div><dt>系统处理</dt><dd>{selectedPresentation.llm_supplement.handling}</dd></div>
                  </dl>
                </section>
              )}
              {findingGroup(selected) !== "complete" && (
                <section className="decision-box">
                  <h3>记录人工结论</h3>
                  <p>选择动作后，必须写明处理依据。</p>
                  <div className="decision-options">
                    {decisionOptions.map((option) => (
                      <button
                        className={resolution === option.code ? "selected" : ""}
                        key={option.code}
                        onClick={() => setResolution(option.code)}
                      >
                        <span>{resolution === option.code && <Check />}</span>
                        {option.label}
                      </button>
                    ))}
                  </div>
                  <Textarea
                    label="处理依据"
                    placeholder="说明为什么这样处理，并引用必要的业务背景。"
                    minRows={3}
                    value={comment}
                    onChange={(event) => setComment(event.currentTarget.value)}
                  />
                  <button
                    className="primary-button save-decision"
                    onClick={saveDecision}
                    disabled={saving}
                  >
                    {saving ? "正在保存…" : "保存人工结论"}
                  </button>
                </section>
              )}
              {selectedDecision && (
                <DecisionHistory decision={selectedDecision} />
              )}
              <div className="finding-nav">
                <button
                  disabled={display[0]?.finding_id === selected.finding_id}
                  onClick={() => {
                    const i = display.findIndex(
                      (item) => item.finding_id === selected.finding_id,
                    );
                    if (display[i - 1])
                      setSelectedId(display[i - 1].finding_id);
                  }}
                >
                  <ArrowLeft />
                  上一条
                </button>
                <button
                  disabled={display.at(-1)?.finding_id === selected.finding_id}
                  onClick={nextFinding}
                >
                  下一条
                  <ArrowRight />
                </button>
              </div>
              <details
                className="technical-info"
                open={technicalOpen}
                onToggle={(event) => setTechnicalOpen(event.currentTarget.open)}
              >
                <summary>
                  <span>
                    <Bug />
                    开发调试信息
                  </span>
                  <ChevronDown />
                </summary>
                <div className="technical-body">
                  <button
                    className="technical-copy-all"
                    onClick={() => copyTechnical(technicalBundle, "调试信息")}
                  >
                    <Copy />
                    一键复制调试信息
                  </button>
                  <dl>
                    <div>
                      <dt>检查规则</dt>
                      <dd>
                        <code>{selected.check_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>问题编号</dt>
                      <dd>
                        <code>{selected.finding_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>运行编号</dt>
                      <dd>
                        <code>{snapshot.run.graph_id}</code>
                      </dd>
                    </div>
                    <div>
                      <dt>任务编号</dt>
                      <dd>
                        <code>{setId}</code>
                      </dd>
                    </div>
                  </dl>
                  <div className="technical-evidence">
                    <header>
                      <span>
                        证据定位 <b>{selectedEvidence.length}</b>
                        {allSelectedEvidence.length > selectedEvidence.length
                          ? ` / 全部引用 ${allSelectedEvidence.length}`
                          : ""}
                      </span>
                    </header>
                    {technicalLocators.length ? (
                      technicalLocators.map((locator, index) => (
                        <div key={selectedEvidence[index].evidence_id}>
                          <code>{locator}</code>
                          {!demo && user?.role === "admin" && (
                            <a
                              href={`/operations?tab=logs&keyword=${encodeURIComponent(selectedEvidence[index].evidence_id)}`}
                              target="_blank"
                              rel="noreferrer"
                            >
                              日志
                              <ExternalLink />
                            </a>
                          )}
                        </div>
                      ))
                    ) : (
                      <p>这条问题没有可显示的直接证据定位。</p>
                    )}
                  </div>
                  {!demo && user?.role === "admin" && (
                    <a
                      className="technical-log-link"
                      href={`/operations?tab=logs&keyword=${encodeURIComponent(snapshot.run.graph_id)}`}
                      target="_blank"
                      rel="noreferrer"
                    >
                      <Bug />
                      查看本次运行日志
                      <ExternalLink />
                    </a>
                  )}
                </div>
              </details>
            </aside>
          </>
        ) : (
          <EmptyState
            title="请选择一条问题"
            description="从左侧队列开始逐条核查。"
          />
        )}
      </div>
      <Modal
        opened={lightbox && Boolean(currentEvidence)}
        onClose={() => setLightbox(false)}
        fullScreen
        title={currentEvidence?.filename}
        closeButtonProps={{ "aria-label": "关闭证据全屏预览" }}
        classNames={{
          content: "evidence-lightbox",
          body: "evidence-lightbox-body",
        }}
      >
        <div className="lightbox-toolbar">
          <span>
            {DOC_LABELS[currentEvidence?.doc_type || "final_report"]} · 第{" "}
            {currentEvidence?.page_number || "—"} 页 ·{" "}
            {!previewLocated
              ? "未定位到高亮"
              : focusMode
                ? `已滚动到第 ${activeAnchor + 1} 处`
                : "完整页顶部"}{" "}
            / {currentEvidencePage?.records.length || 0} 处
          </span>
          <div>
            <button
              disabled={!previewLocated}
              className={focusMode && previewLocated ? "active" : ""}
              aria-pressed={focusMode && previewLocated}
              onClick={() => setFocusMode((value) => !value)}
            >
              <Crosshair />
              {!previewLocated ? "未定位" : focusMode ? "已定位" : "定位高亮"}
            </button>
            {(currentEvidencePage?.records.length || 0) > 1 && (
              <>
                <button
                  aria-label="上一处高亮"
                  disabled={activeAnchor === 0}
                  onClick={() => {
                    setActiveAnchor((value) => Math.max(0, value - 1));
                    setFocusMode(true);
                  }}
                >
                  <ArrowLeft />
                </button>
                <b>
                  {activeAnchor + 1}/{currentEvidencePage?.records.length}
                </b>
                <button
                  aria-label="下一处高亮"
                  disabled={
                    activeAnchor ===
                    (currentEvidencePage?.records.length || 1) - 1
                  }
                  onClick={() => {
                    setActiveAnchor((value) =>
                      Math.min(
                        (currentEvidencePage?.records.length || 1) - 1,
                        value + 1,
                      ),
                    );
                    setFocusMode(true);
                  }}
                >
                  <ArrowRight />
                </button>
              </>
            )}
            <button
              aria-label="缩小证据页"
              onClick={() => setZoom((value) => Math.max(60, value - 25))}
            >
              <ZoomOut />
            </button>
            <b>{zoom}%</b>
            <button
              aria-label="放大证据页"
              onClick={() => setZoom((value) => Math.min(300, value + 25))}
            >
              <ZoomIn />
            </button>
            <a
              href={previewUrl || "#"}
              onClick={(event) => {
                event.preventDefault();
                if (previewUrl) void openAuthenticatedResource(previewUrl);
              }}
            >
              完整原图 <ExternalLink />
            </a>
          </div>
        </div>
        {currentEvidencePage &&
          (demo ? (
            <div className={`lightbox-canvas ${focusMode ? "is-focused" : ""}`}>
              <MockEvidencePage
                page={currentEvidencePage}
                kinds={currentHighlightKinds}
                zoom={zoom}
              />
            </div>
          ) : (
            <EvidenceImageViewport
              className="lightbox-canvas"
              url={previewUrl}
              activeAnchor={activeAnchor}
              focused={focusMode}
              zoom={zoom}
              onLocationChange={setCurrentPreviewLocated}
              alt={
                focusMode && previewLocated
                  ? `全屏完整页，已滚动到第${activeAnchor + 1}处高亮`
                  : "全屏多色证据完整页"
              }
            />
          ))}
      </Modal>
    </div>
  );
}

function DecisionHistory({ decision }: { decision: FindingDecision }) {
  return (
    <section className="decision-history">
      <h3>最近处理记录</h3>
      <div>
        <span>
          <Check />
        </span>
        <p>
          <b>{decision.actor_id || "当前审核员"}</b> ·{" "}
          {decision.created_at
            ? new Date(decision.created_at).toLocaleString("zh-CN")
            : "刚刚"}
          <br />
          {decision.comment}
        </p>
      </div>
    </section>
  );
}

function ComparisonValue({ value }: { value: unknown }) {
  const text = String(value ?? "—");
  const items = text
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  if (items.length <= 6) return <>{text}</>;
  return (
    <details className="comparison-values">
      <summary>
        {items.slice(0, 3).join("、")}…（共 {items.length} 项）
      </summary>
      <p>{items.join("、")}</p>
    </details>
  );
}

function MockEvidencePage({
  page,
  kinds,
  zoom,
}: {
  page: EvidencePageGroup;
  kinds: HighlightKind[];
  zoom: number;
}) {
  const evidence = page.records[0];
  const isReport = evidence.doc_type === "final_report";
  return (
    <div className="mock-evidence-page" style={{ width: `${zoom}%` }}>
      <div className="page-ruler">
        {[10, 20, 30, 40, 50, 60, 70, 80, 90].map((mark) => (
          <span key={mark}>{mark}</span>
        ))}
      </div>
      <header>
        <b>
          {isReport
            ? "EMC 检测报告 / TEST REPORT"
            : "原始检测记录 / RAW TEST RECORD"}
        </b>
        <span>Page {evidence.page_number || 1}</span>
      </header>
      <h3>反向电压试验 / Reverse voltage</h3>
      <table>
        <tbody>
          <tr>
            <th>样品编号</th>
            <td>E20260402869601-0016</td>
            <th>工作模式</th>
            <td>Mode 2</td>
          </tr>
          <tr>
            <th>试验电压</th>
            <td>14 V</td>
            <th>持续时间</th>
            <td>60 s</td>
          </tr>
          <tr>
            <th>功能状态</th>
            <td>C</td>
            <th>试验结果</th>
            <td>符合 / Pass</td>
          </tr>
        </tbody>
      </table>
      {page.records.map((item, index) => (
        <div
          key={item.evidence_id}
          className={`mock-highlight-box ${kinds[index]}`}
          style={{
            top: `${238 + index * 46}px`,
            left: `${index % 2 ? 48 : 25}%`,
            width: `${index % 2 ? 42 : 56}%`,
          }}
        >
          <i>{index + 1}</i>
        </div>
      ))}
      <p>
        试验过程中监测样品功能状态，试验结束后完成最终功能检查。结果记录与本页签名共同构成原始证据。
      </p>
      <div className="page-signatures">
        <span>试验：王工</span>
        <span>复核：李工</span>
        <span>日期：2026-04-03</span>
      </div>
    </div>
  );
}
