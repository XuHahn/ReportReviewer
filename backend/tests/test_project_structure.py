"""Repository hygiene and architectural guardrails."""

from __future__ import annotations

import ast
import re
import subprocess
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[2]
SERVICES = ROOT / "backend" / "services"


def _relative_links(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    links = re.findall(r"\[[^]]+\]\(([^)]+)\)", text)
    return [
        unquote(link.split("#", 1)[0])
        for link in links
        if link and not re.match(r"^[a-z]+://", link) and not link.startswith("#")
    ]


def test_documentation_entry_points_and_links_exist():
    documents = [
        ROOT / "README.md",
        ROOT / "AGENTS.md",
        ROOT / "CLAUDE.md",
        ROOT / "design" / "README.md",
        ROOT / "example" / "README.md",
    ]
    for document in documents:
        assert document.is_file(), f"缺少文档入口: {document.relative_to(ROOT)}"
        for link in _relative_links(document):
            target = (document.parent / link).resolve()
            assert target.exists(), (
                f"{document.relative_to(ROOT)} 包含失效链接: {link}"
            )


def test_temporary_ux_previews_are_not_committed():
    root_previews = list(ROOT.glob("*preview*.html")) + list(ROOT.glob("*ux-options*.html"))
    assert root_previews == []
    assert not (ROOT / "design" / "previews").exists()


def test_python_backend_has_no_node_manifest():
    assert not (ROOT / "backend" / "package.json").exists()
    assert not (ROOT / "backend" / "package-lock.json").exists()


def test_v2_is_the_only_frontend_and_deployment_target():
    assert not (ROOT / "frontend").exists()
    assert (ROOT / "frontend-v2" / "package.json").is_file()
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "build: ./frontend-v2" in compose
    assert "build: ./frontend\n" not in compose
    assert "build: ./admin" not in compose


def test_launcher_variables_are_safe_next_to_utf8_punctuation():
    """macOS Bash 3.2 may absorb UTF-8 punctuation into an unbraced name."""
    launcher = ROOT / "start.sh"
    text = launcher.read_text(encoding="utf-8")
    offenders = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if re.search(r"\$[A-Za-z_][A-Za-z0-9_]*(?=[^\x00-\x7f])", line):
            offenders.append(line_number)
    assert offenders == [], f"启动脚本变量紧邻 UTF-8 标点且未加花括号: {offenders}"

    result = subprocess.run(
        ["/bin/bash", "-u", "-c", 'VISION_PID=123; echo "PID:${VISION_PID}，准备中"'],
        env={"PATH": "/usr/bin:/bin", "LC_ALL": "en_US.UTF-8"},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "PID:123" in result.stdout


def test_production_llm_calls_declare_task_kind():
    missing: list[str] = []
    for path in sorted(SERVICES.glob("*.py")):
        if path.name == "deepseek_client.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr != "_call_api":
                continue
            if not any(keyword.arg == "task_kind" for keyword in node.keywords):
                missing.append(f"{path.name}:{node.lineno}")
    assert missing == [], f"LLM 调用未声明 task_kind: {missing}"


def test_only_shared_client_calls_deepseek_sdk_directly():
    direct_call = "chat.completions.create"
    offenders = []
    for path in sorted(SERVICES.glob("*.py")):
        if path.name == "deepseek_client.py":
            continue
        if direct_call in path.read_text(encoding="utf-8"):
            offenders.append(path.name)
    assert offenders == []


def test_removed_review_stacks_cannot_reenter_production_code():
    production_roots = [ROOT / "backend", ROOT / "frontend-v2" / "src", ROOT / "scripts"]
    banned = (
        "audit_v2", "AUDIT_V2", "review-v2", "paddle_layout", "PaddleOCR",
        "pytesseract", "tesseract",
    )
    offenders: list[str] = []
    for production_root in production_roots:
        for path in production_root.rglob("*"):
            if (
                not path.is_file()
                or "__pycache__" in path.parts
                or "tests" in path.parts
                or "venv" in path.parts
            ):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".sh", ".txt"}:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for token in banned:
                if token in text:
                    offenders.append(f"{path.relative_to(ROOT)}:{token}")
    assert offenders == [], f"已废弃审核/OCR栈重新进入生产代码: {offenders}"
