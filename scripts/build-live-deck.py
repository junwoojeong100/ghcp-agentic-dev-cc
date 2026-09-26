#!/usr/bin/env python3
"""Build the evidence-gated Korean live-demo deck; never run the demo itself.

Usage:
    python3 -B scripts/build-live-deck.py --require-screenshots
    python3 -B scripts/build-live-deck.py --facts production/ghcp-live/facts.json \
        --font "Malgun Gothic"

Output is ONLY deliverables/ghcp-live/ghcp-agentic-development.pptx. All nine
captures and the final MP4 are mandatory even without --require-screenshots.
No placeholders, network calls, CLI/model launches, or publication occur.

schemaVersion 1 contract (all paths resolve inside this repository):
    cli: {name, version, model}
    implementation: {status: "verified", changedFiles: [path, ...], codeHash,
                     evidence: [path, ...]}
    baselineTests: {passed, executor, evidence}
    redTests: {failed, executor, evidence}  # failed > 0; expected pre-fix run
    tests: {passed, failed, skipped, executor, evidence}
    browserChecks: {passed, failed, executor, evidence}
    delegation: {status: "completed", summary, evidence}
    review: {status: "completed", summary, evidence}
    approvals: [{kind, status, evidence}, ...]
    captures: {before, analysis, approval, implementation, tests, blocked,
               allowed, boundary, review}  # repo-relative image paths
    video: {path: "deliverables/ghcp-live/ghcp-cxo-demo-ko.mp4"}
    sources: [{source: path, kind: "terminal" | "app" | "card",
               evidence: [path, ...], sha256: actual_digest}, ...]
    limitations: [text, ...]

Evidence paths are repository-relative and identify existing, nonempty files.
implementation.evidence, approvals[].evidence and sources[].evidence must be
nonempty arrays; other evidence values accept a path or a nonempty path array.
implementation.changedFiles identifies repository-local source snapshots, not
files in the live recording workspace. Optional failed/skipped counts on
baselineTests and passed/skipped counts on redTests are supported. At least one
approval must have kind="implementation-plan" and status="approved", with the
actual explicit human decision preserved in its source record (including
chat-relayed approval). Every capture needs a matching sources entry whose
SHA-256 matches the file. Cards are labeled as evidence summaries, never as
native UI. Captures are never substituted for missing execution records.

The three additional evidence fields -- implementation.evidence, redTests,
and delegation -- prevent inferring implementation, red/green, or delegation
from a screenshot or from baseline passing tests. The producer attests to
record authenticity and codeHash; this builder validates files, schema and
completion gates, not the truth of arbitrary log prose. It does not recompute
an unspecified code-hash algorithm or count tests by guessing log formats.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import sys
import textwrap
from pathlib import Path
from urllib.parse import quote


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FACTS = ROOT / "production" / "ghcp-live" / "facts.json"
OUTPUT = ROOT / "deliverables" / "ghcp-live" / "ghcp-agentic-development.pptx"
VIDEO = ROOT / "deliverables" / "ghcp-live" / "ghcp-cxo-demo-ko.mp4"
CAPTURES = (
    "before", "analysis", "approval", "implementation", "tests", "blocked",
    "allowed", "boundary", "review",
)
CLI_DOCS = "https://docs.github.com/en/copilot/how-tos/copilot-cli"


def string(value, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label}: 비어 있지 않은 문자열이 필요합니다.")
    if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
        raise ValueError(f"{label}: 지원하지 않는 제어 문자가 있습니다.")
    return value.strip()


def object_at(data: dict, key: str) -> dict:
    value = data.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"{key}: 객체가 필요합니다.")
    return value


def count(data: dict, key: str, label: str, minimum: int = 0) -> int:
    value = data.get(key)
    if type(value) is not int or value < minimum:
        raise ValueError(f"{label}.{key}: {minimum} 이상의 정수가 필요합니다.")
    return value


def existing_file(raw, label: str, *, relative: bool = False) -> Path:
    value = string(raw, label)
    path = Path(value)
    if relative and path.is_absolute():
        raise ValueError(f"{label}: 저장소 기준 상대 경로를 사용하세요.")
    path = path if path.is_absolute() else ROOT / path
    try:
        resolved = path.resolve()
        resolved.relative_to(ROOT)
    except (ValueError, RuntimeError) as exc:
        raise ValueError(f"{label}: 저장소 밖 경로 또는 잘못된 링크는 허용하지 않습니다.") from exc
    if not resolved.is_file():
        raise ValueError(f"{label}: 실제 파일이 없습니다: {path}")
    if resolved.stat().st_size == 0:
        raise ValueError(f"{label}: 빈 파일은 실행 근거가 아닙니다: {path}")
    return resolved


def evidence_paths(raw, label: str, *, array: bool = False) -> list[Path]:
    values = [raw] if isinstance(raw, str) and not array else raw
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label}: 비어 있지 않은 실제 근거 경로 배열이 필요합니다.")
    return [existing_file(value, f"{label}[{i}]", relative=True) for i, value in enumerate(values)]


def file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


DRAFT_NOTICE = "검토용 초안 · 승인 연속 영상 미확보"


def video_contract():
    spec = importlib.util.spec_from_file_location("_live_video_contract", ROOT / "scripts/build-live-video.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_facts(path: Path, draft: bool = False) -> dict:
    source = existing_file(str(path), "facts")
    raw = source.read_bytes()
    try:
        facts = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"facts: 올바른 UTF-8 JSON이 필요합니다: {exc}") from exc
    if not isinstance(facts, dict):
        raise ValueError("facts: 최상위 값은 객체여야 합니다.")
    if type(facts.get("schemaVersion")) is not int or facts["schemaVersion"] != 1:
        raise ValueError("facts.schemaVersion: 지원하는 값은 정수 1입니다.")

    paths = {"facts": [source]}
    cli = object_at(facts, "cli")
    for key in ("name", "version", "model"):
        string(cli.get(key), f"cli.{key}")

    contract = video_contract()
    failures = contract.validate_review_draft(facts, draft)
    facts["_draft"] = draft
    if draft:
        paths["production"] = failures
        paths["openFindings"] = [p for item in facts["openFindings"]
                                 for p in evidence_paths(item["evidence"], "openFinding.evidence")]
    implementation = object_at(facts, "implementation")
    expected_status = "verified-with-open-finding" if draft else "verified"
    if implementation.get("status") != expected_status:
        raise ValueError(f"implementation.status: {expected_status} 상태여야 합니다.")
    string(implementation.get("codeHash"), "implementation.codeHash")
    changes = implementation.get("changedFiles")
    if not isinstance(changes, list) or not changes:
        raise ValueError("implementation.changedFiles: 실제 변경 파일 경로 배열이 필요합니다.")
    paths["changedFiles"] = [
        existing_file(value, f"implementation.changedFiles[{i}]", relative=True)
        for i, value in enumerate(changes)
    ]
    paths["implementation"] = evidence_paths(
        implementation.get("evidence"), "implementation.evidence", array=True
    )

    for key in ("baselineTests", "tests", "browserChecks", "redTests"):
        result = object_at(facts, key)
        string(result.get("executor"), f"{key}.executor")
        paths[key] = evidence_paths(result.get("evidence"), f"{key}.evidence")
        if key == "redTests":
            count(result, "failed", key, minimum=1)
            for optional in ("passed", "skipped"):
                if optional in result:
                    count(result, optional, key)
        else:
            count(result, "passed", key, minimum=1)
            if key != "baselineTests" or "failed" in result:
                if count(result, "failed", key):
                    raise ValueError(f"{key}: 실패한 최종 또는 시작 앱 검증이 있어 생성을 중단합니다.")
            if key == "tests" or "skipped" in result:
                count(result, "skipped", key)
    # A single authentic transcript may contain both RED and GREEN runs; the
    # producer must identify the runs accurately rather than split files for us.

    for key in ("delegation", "review"):
        record = object_at(facts, key)
        if record.get("status") != "completed":
            raise ValueError(f"{key}.status: 실제 실행이 completed 상태여야 합니다.")
        string(record.get("summary"), f"{key}.summary")
        paths[key] = evidence_paths(record.get("evidence"), f"{key}.evidence")

    approvals = facts.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        raise ValueError("approvals: 실제 사람의 구현 계획 승인 기록이 필요합니다.")
    approved_plan = []
    for i, approval in enumerate(approvals):
        if not isinstance(approval, dict):
            raise ValueError(f"approvals[{i}]: 객체가 필요합니다.")
        string(approval.get("kind"), f"approvals[{i}].kind")
        string(approval.get("status"), f"approvals[{i}].status")
        key = f"approvals[{i}]"
        paths[key] = evidence_paths(approval.get("evidence"), f"{key}.evidence", array=True)
        if approval["kind"] == "implementation-plan" and approval["status"] == "approved":
            approved_plan.append(key)
    if not approved_plan:
        raise ValueError("approvals: kind=implementation-plan, status=approved인 명시적 사람 승인이 필요합니다.")

    captures = object_at(facts, "captures")
    for key in CAPTURES:
        paths[f"captures.{key}"] = [
            existing_file(captures.get(key), f"captures.{key}", relative=True)
        ]
    sources = facts.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ValueError("sources: 실제 화면·카드의 출처와 SHA-256 기록 배열이 필요합니다.")
    capture_sources = {}
    for i, record in enumerate(sources):
        label = f"sources[{i}]"
        if not isinstance(record, dict):
            raise ValueError(f"{label}: 객체가 필요합니다.")
        source_path = existing_file(record.get("source"), f"{label}.source", relative=True)
        kind = string(record.get("kind"), f"{label}.kind")
        if kind not in {"terminal", "app", "card"}:
            raise ValueError(f"{label}.kind: terminal, app, card만 지원합니다.")
        if source_path in capture_sources:
            raise ValueError(f"{label}.source: 동일 출처를 중복 지정하지 마세요.")
        paths[label] = [source_path]
        paths[f"{label}.evidence"] = evidence_paths(
            record.get("evidence"), f"{label}.evidence", array=True
        )
        expected = string(record.get("sha256"), f"{label}.sha256").lower()
        if len(expected) != 64 or any(char not in "0123456789abcdef" for char in expected):
            raise ValueError(f"{label}.sha256: 64자리 SHA-256 값이 필요합니다.")
        if file_digest(source_path) != expected:
            raise ValueError(f"{label}.sha256: 현재 파일과 해시가 일치하지 않습니다.")
        if kind == "terminal":
            evidence = set()
            contract.terminal_provenance(source_path, record, {}, evidence, draft=draft, failures=failures)
            paths[f"{label}.provenance"] = sorted(evidence)
        capture_sources[source_path] = {"kind": kind, "sha256": expected,
                                        "evidenceKey": f"{label}.evidence"}
    for key in CAPTURES:
        if paths[f"captures.{key}"][0] not in capture_sources:
            raise ValueError(f"captures.{key}: 일치하는 sources 항목과 실제 근거가 필요합니다.")

    video = object_at(facts, "video")
    paths["video"] = [existing_file(video.get("path"), "video.path", relative=True)]
    if paths["video"][0] != VIDEO.resolve():
        raise ValueError(f"video.path: {VIDEO.relative_to(ROOT).as_posix()}만 지원합니다.")
    with paths["video"][0].open("rb") as stream:
        if stream.read(12)[4:8] != b"ftyp":
            raise ValueError("video.path: MP4 컨테이너 헤더를 확인할 수 없습니다.")

    limitations = facts.get("limitations")
    if not isinstance(limitations, list):
        raise ValueError("limitations: 문자열 배열이 필요합니다. 제약을 생략하지 마세요.")
    for i, item in enumerate(limitations):
        string(item, f"limitations[{i}]")
    facts["_paths"] = paths
    facts["_captureSources"] = capture_sources
    facts["_approvedPlan"] = approved_plan
    facts["_factsDigest"] = hashlib.sha256(raw).hexdigest()
    return facts


def load_helpers():
    # Import definitions only. The original main/build/notes are never invoked.
    # Suppress bytecode so this builder does not produce extra output files.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("_live_deck_helpers", ROOT / "scripts" / "build-deck.py")
        if spec is None or spec.loader is None:
            raise ValueError("기존 Deck 도우미를 읽을 수 없습니다.")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.dont_write_bytecode = previous


def excerpt(value, length: int = 80) -> str:
    text = " ".join(str(value).split())
    return text if len(text) <= length else text[:length - 1] + "…"


def relative_link(path: Path) -> str:
    return quote(Path(os.path.relpath(path, OUTPUT.parent)).as_posix(), safe="/.-_")


def make_deck(base, facts: dict, font: str):
    class LiveDeck(base.Deck):
        def __init__(self):
            super().__init__(facts, font, require_screenshots=True)
            props = self.prs.core_properties
            props.title = "사업 요청에서 검증까지"
            props.subject = "GitHub Copilot CLI 실제 실행 기록 기반 한국어 비공식 데모"
            props.author = "Independent demo production"
            props.keywords = "한국어, 실제 실행 근거, 사람 승인, 편집 가능한 슬라이드"
            props.comments = "GitHub 또는 Microsoft의 공식 자료·보증·후원을 뜻하지 않습니다."
            self.capture_sizes = {}
            for key in CAPTURES:
                path = self.path(f"captures.{key}")
                try:
                    with base.Image.open(path) as image:
                        if image.format not in {"PNG", "JPEG"}:
                            raise ValueError("PNG 또는 JPEG 캡처가 필요합니다.")
                        self.capture_sizes[key] = image.size
                        image.verify()
                    if min(self.capture_sizes[key]) <= 0:
                        raise ValueError("이미지 크기가 올바르지 않습니다.")
                except (OSError, ValueError, SyntaxError) as exc:
                    raise ValueError(f"captures.{key}: 실제 이미지 파일을 확인하세요: {exc}") from exc

        def path(self, key: str) -> Path:
            return self.facts["_paths"][key][0]

        def sources(self, *keys: str) -> str:
            rows = []
            for key in keys:
                for path in self.facts["_paths"][key]:
                    rows.append(f"{key}: {path.relative_to(ROOT).as_posix()}")
                    record = self.facts["_captureSources"].get(path)
                    if record:
                        rows.append(f"유형: {record['kind']} · SHA-256: {record['sha256']}")
                        rows.extend(
                            f"원시 근거: {item.relative_to(ROOT).as_posix()}"
                            for item in self.facts["_paths"][record["evidenceKey"]]
                        )
            return "\n".join(rows)

        def notes(self, slide, speech: str, detail: str = ""):
            # Deliberately replace historical global notes and executor claims.
            slide.notes_slide.notes_text_frame.text = (
                "발표자 메모\n" + speech + ("\n\n실행 근거\n" + detail if detail else "") +
                ("\n\n" + DRAFT_NOTICE + "\n보완 필요 1건 · 운영 적용 보류. HTTP 422 본문 읽기 실패 시 UI 잠금이 풀리는 정적 검토 지적입니다. 실행 재현·수정은 남아 있으며 서버 차단 우회는 아닙니다. 터미널 화면은 당시 정지 캡처이고 앱 동영상은 별도 제작 세션에서 녹화했습니다. 사용자 결정은 대화에서 확인 후 전달했으며, 후반 로컬 검증은 일괄 승인 범위에서 대행했습니다."
                 if self.facts["_draft"] else "")
            )

        def slide(self, section, title=None, subtitle=None):
            slide = self.prs.slides.add_slide(self.prs.slide_layouts[6])
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = base.rgb("ground")
            self.text(slide, "개발 실행 사례", .55, .23, 4.8, .3, 13, bold=True)
            self.text(slide, section, 6.6, .23, 6.18, .3, 12, "secondary", align=base.PP_ALIGN.RIGHT)
            self.box(slide, .55, .69, 12.23, .012, "line")
            if title:
                self.text(slide, title, .55, .95, 12.23, .8, 31, bold=True)
            if subtitle:
                self.text(slide, subtitle, .57, 1.8, 12.1, .48, 18, "secondary")
            self.box(slide, .55, 6.99, 12.23, .012, "line")
            footer = (DRAFT_NOTICE + " · 보완 필요 1건 · 운영 적용 보류" if self.facts["_draft"]
                      else "실제 실행 기록 기반 · 비공식 데모 · 운영 적용은 별도 판단")
            self.text(slide, footer, .55, 7.12, 10.8, .22, 10, "secondary")
            self.text(slide, f"{len(self.prs.slides):02d} / 12", 11.88, 7.1, .9, .25,
                      11, "secondary", align=base.PP_ALIGN.RIGHT)
            return slide

        def screenshot(self, slide, name, x, y, w, h):
            # The shared screenshot helper hardcodes historical directories.
            # Keep its aspect-fit behavior but use validated live capture paths.
            path = self.path(f"captures.{name}")
            iw, ih = self.capture_sizes[name]
            self.box(slide, x, y, w, h, "white", "line")
            scale = min((w - .07) / iw, (h - .07) / ih)
            pw, ph = iw * scale, ih * scale
            picture = slide.shapes.add_picture(
                str(path), base.Inches(x + (w - pw) / 2), base.Inches(y + (h - ph) / 2),
                width=base.Inches(pw), height=base.Inches(ph),
            )
            for props in picture._element.xpath(".//p:cNvPr"):
                props.set("descr", f"실제 실행 캡처 {name}. 출처 {path.relative_to(ROOT).as_posix()}")
            return picture

        def side(self, slide, heading: str, text: str, y: float = 2.57):
            self.text(slide, heading, 9.15, y, 3.58, .52, 23, "accent", True)
            self.text(slide, text, 9.15, y + .67, 3.58, 1.95, 21)

        def build(self):
            f = self.facts

            # 01 -- The business request, not a fabricated quotation/chat.
            s = self.slide("01 업무 요청", "사업 요청이 제품의 동작이 되는 순간",
                           "검토용 초안 · 보완 필요 1건 · 운영 적용 보류" if f["_draft"] else None)
            self.text(s, "기준 미달 견적은\n전송 전에 막아 주세요.", .59, 2.36, 6.0, 1.7, 32, bold=True)
            self.text(s, "업무 요청 요약 · 실제 대화 재현 아님", .62, 4.36, 5.95, .45, 15, "secondary")
            self.text(s, "GitHub Copilot CLI\n실제 구현·검증 기록으로 확인합니다.", .62, 5.2, 6.0, 1.18, 21, "accent")
            self.screenshot(s, "before", 6.99, 2.36, 5.73, 3.8)
            self.notes(s,
                "새 시스템 전체가 아니라 기존 견적 업무의 규칙 한 가지를 다룹니다. 왼쪽 문장은 업무 요청의 "
                "요약이며 고객의 실제 인용문이나 승인 대화를 만들어 낸 것이 아닙니다. 시작 앱과 이후 변경을 "
                "구분해 보여 주고, 실제 실행 기록으로 확인할 수 있는 범위만 설명합니다.",
                self.sources("captures.before", "implementation"))

            # 02 -- Do not copy historical prices, margins or test counts.
            s = self.slide("02 견적의 문제", "계산 결과만 보여 주면, 정책이 지켜질까요?")
            self.screenshot(s, "before", .59, 2.13, 8.16, 4.54)
            self.side(s, "변경 전", "금액 계산과\n전송 가능 판단은\n다른 문제입니다.")
            self.text(s, "실제 화면의 입력값과\n전송 동작을 확인합니다.", 9.15, 5.35, 3.57, 1.05, 19, "secondary")
            self.notes(s,
                "기준 미달 여부를 사람이 계산 결과만 보고 매번 해석해야 하는 문제를 짚습니다. 사진에 없는 "
                "가격·할인율·손실액을 임의로 덧붙이지 마세요. 변경 전 실제 화면의 입력과 결과를 읽고, 이후 "
                "화면에서 어떤 동작이 바뀌었는지 비교합니다.", self.sources("captures.before", "baselineTests"))

            # 03 -- Separate business policy from normal work and human authority.
            s = self.slide("03 정책과 의사결정", "정책은 지키고, 정상 업무는 이어 갑니다")
            self.screenshot(s, "boundary", .59, 2.13, 8.16, 4.54)
            for y, heading, body in (
                (2.32, "정책", "기준 미달 전송 차단"),
                (3.8, "정상 업무", "정상·경계 조건 확인"),
                (5.28, "사람의 결정", "변경 범위·운영 적용 판단"),
            ):
                self.text(s, heading, 9.15, y, 3.57, .46, 22, "accent", True)
                self.text(s, body, 9.15, y + .55, 3.57, .82, 20)
            self.notes(s,
                "업무 정책의 집행, 정상 업무의 연속성, 사람의 의사결정을 나눕니다. 경계 조건은 화면 표시와 "
                "실제 판정이 일치하는지 원시 검증 기록과 함께 확인하세요. 개발 계획을 승인한 사실만으로 "
                "운영 적용이나 예외 결재까지 승인된 것처럼 설명하지 않습니다.",
                self.sources("captures.boundary", "browserChecks", *f["_approvedPlan"]))

            # 04 -- Relative external link, never embedded video or a fake player.
            s = self.slide("04 실제 영상", "실제 캡처와 앱 녹화로 보는 검토용 초안" if f["_draft"]
                           else "요청부터 검토까지, 실제 실행을 봅니다")
            self.screenshot(s, "allowed", .59, 2.13, 8.16, 4.54)
            self.text(s, "검토용 영상 열기" if f["_draft"] else "실제 실행 영상 열기", 9.15, 2.73, 3.57, 1.12, 27, "accent", True,
                      link=relative_link(self.path("video")))
            self.text(s, "별도 MP4 파일\nPPT와 같은 폴더에 보관", 9.15, 4.15, 3.57, 1.2, 20)
            self.text(s, "영상 길이 ≠ 개발 소요시간", 9.15, 5.81, 3.57, .75, 17, "secondary")
            self.notes(s,
                "이미지는 실제 결과 화면이며 가짜 재생 UI가 아닙니다. 링크는 같은 폴더의 MP4를 엽니다. "
                "PowerPoint 보안 설정이 링크를 막으면 파일 탐색기에서 직접 여세요. PPT와 MP4를 함께 "
                "이동하면 상대 링크를 유지할 수 있습니다. 영상의 길이를 개발 리드타임이나 생산성 측정값으로 "
                "해석하지 않습니다. 편집·음성 등 제작상의 제약은 마지막 슬라이드의 실제 기록을 따릅니다.",
                self.sources("video", "captures.allowed"))

            # 05 -- The initial app is not attributed to this implementation run.
            s = self.slide("05 기존 시스템", "처음부터 만들지 않고, 현재 동작에서 시작")
            self.screenshot(s, "before", .59, 2.13, 8.16, 4.54)
            self.side(s, "이미 있던 것", "견적 화면\n계산·전송 흐름\n시작 앱 테스트")
            self.text(s, f"시작 앱 테스트 {f['baselineTests']['passed']}건 통과\n"
                      f"실행: {excerpt(f['baselineTests']['executor'], 24)}",
                      9.15, 5.38, 3.57, 1.12, 18, "secondary")
            self.notes(s,
                "시작 앱은 이번 변경 이전의 기준점입니다. 기존 시스템 전체를 이번 실행에서 새로 만들었다고 "
                "소개하지 마세요. 시작 앱의 통과 테스트는 기존 동작 확인이며 신규 요구사항이 구현됐다는 "
                "근거도, 신규 조건의 실패 실행인 RED도 아닙니다. 변경 파일은 저장소에 보관한 실제 스냅샷을 "
                "가리키며 라이브 촬영 작업 공간을 이 빌더가 열거나 수정하지 않습니다.",
                f"시작 앱 실행자: {f['baselineTests']['executor']}\n" + self.sources("baselineTests", "changedFiles"))

            # 06 -- Actual delegation must have its own completed execution record.
            s = self.slide("06 실제 에이전트 위임", "위임 요청과 반환 결과를 기록으로 확인")
            self.screenshot(s, "analysis", .59, 2.13, 8.16, 4.54)
            self.text(s, "기록된 위임", 9.15, 2.45, 3.57, .55, 23, "accent", True)
            self.text(s, excerpt(f["delegation"]["summary"], 78), 9.15, 3.14, 3.57, 2.55, 20)
            self.text(s, "계획 설명만으로\n위임 실행을 대신하지 않습니다.", 9.15, 5.84, 3.57, .89, 17, "secondary")
            self.notes(s,
                "실행 기록에 남은 위임 요청, 맡긴 범위, 반환 결과를 짚습니다. 화면의 역할 이름이나 계획 "
                "문구만으로 실제 하위 에이전트 실행을 주장하지 않습니다. 병렬 실행·사용자 지정 에이전트·"
                "특정 통신 프로토콜도 근거에 없으면 주장하지 마세요. 아래는 기록 생산자가 실제 위임을 "
                "확인한 요약입니다.\n" + f["delegation"]["summary"],
                self.sources("delegation", "captures.analysis", "implementation"))

            # 07 -- The capture and source record must be the real human decision.
            s = self.slide("07 사람의 계획 승인", "구현에 앞서, 사람이 범위를 승인합니다")
            self.screenshot(s, "approval", .59, 2.13, 8.16, 4.54)
            self.side(s, "명시적인 사람 승인", "대화에서 승인\nCLI에 선택 전달\n화면은 당시 정지 캡처" if f["_draft"]
                      else "실제 대화 기록\n구현 계획 승인\n승인 범위 그대로 전달")
            self.text(s, "계획 승인 ≠ 운영 출시 승인", 9.15, 5.86, 3.57, .77, 18, "secondary")
            self.notes(s,
                "실제 사용자의 명시적인 승인 대화와 그 승인이 전달된 기록을 보여 줍니다. 사람이 채팅으로 "
                "승인하고 실행 담당자가 전달한 경우 그것을 그대로 설명하세요. CLI 안에 존재하지 않는 승인 "
                "버튼이나 자율 승인을 재현하지 않습니다. 이 승인은 해당 구현 계획의 범위에 한정되며 다른 "
                "권한이나 운영 출시의 승인으로 확대하지 않습니다.",
                self.sources(*f["_approvedPlan"], "captures.approval"))

            # 08 -- Separate observed pre-fix failures from final passing checks.
            s = self.slide("08 실제 테스트 RED → GREEN", "실패 조건을 확인하고, 변경 뒤 다시 검증")
            self.screenshot(s, "tests", .59, 2.13, 8.16, 4.54)
            self.text(s, "RED · 변경 전 신규 조건", 9.15, 2.27, 3.57, .72, 21, "blocked", True)
            self.text(s, f"실패 {f['redTests']['failed']}건\n실행: {excerpt(f['redTests']['executor'], 23)}",
                      9.15, 3.06, 3.57, 1.08, 18)
            self.text(s, "GREEN · 변경 후 테스트", 9.15, 4.43, 3.57, .72, 21, "allowed", True)
            self.text(s, f"통과 {f['tests']['passed']} · 실패 {f['tests']['failed']} · 건너뜀 {f['tests']['skipped']}\n"
                      f"실행: {excerpt(f['tests']['executor'], 23)}", 9.15, 5.23, 3.57, 1.1, 18)
            self.notes(s,
                "RED는 신규 조건을 만족하지 못하는 실제 변경 전 실행입니다. 시작 앱 테스트 통과를 RED로 "
                "바꾸어 부르지 않습니다. GREEN은 구현 후 실제 실행한 테스트이며, 각 실행자를 facts의 "
                "executor 그대로 설명합니다. 에이전트가 실행하지 않은 검증을 에이전트의 성과로 돌리지 "
                "마세요. 두 실행의 테스트 개수가 같다는 가정이나 고정된 통과 건수는 없습니다. 건너뛴 "
                "테스트를 통과로 합산하지 않으며 서로 다른 실행의 수를 더하지 않습니다.",
                f"RED 실행자: {f['redTests']['executor']}\n"
                f"GREEN 실행자: {f['tests']['executor']}\n"
                f"브라우저 확인: 통과 {f['browserChecks']['passed']} · 실패 {f['browserChecks']['failed']}\n"
                f"브라우저 실행자: {f['browserChecks']['executor']}\n" +
                self.sources("redTests", "tests", "browserChecks", "captures.tests"))

            # 09 -- Actual before/after views, not painted application mockups.
            s = self.slide("09 변경 전과 후", "같은 업무, 정책을 반영한 동작")
            self.text(s, "변경 전 · 시작 앱", .61, 2.03, 5.9, .5, 22, bold=True)
            self.text(s, "변경 후 · 기준 미달 차단", 6.86, 2.03, 5.87, .5, 22, "blocked", True)
            self.screenshot(s, "before", .59, 2.7, 5.93, 3.47)
            self.screenshot(s, "blocked", 6.8, 2.7, 5.93, 3.47)
            self.text(s, "정상 견적과 경계 조건은 별도 검증 기록으로 함께 확인합니다.", .62, 6.43, 12.0, .4, 19, "secondary")
            self.notes(s,
                "두 실제 화면의 입력과 결과를 비교합니다. 동일 입력의 비교인지 원시 기록을 확인하면서 "
                "설명하고, 화면 밖의 서버 동작은 브라우저 검증 근거와 함께 확인하세요. 차단 사례만으로 "
                "완료를 판단하지 않고 정상 전송과 경계 조건도 확인했습니다. 이 자료는 기록된 데모 "
                "조건에서의 결과이며 모든 운영 데이터에 대한 보장은 아닙니다.",
                self.sources("captures.before", "captures.blocked", "captures.allowed", "captures.boundary", "browserChecks"))

            # 10 -- Qualitative observed value, not invented ROI or timing.
            s = self.slide("10 검토와 남은 조치", "보완 필요 1건 · 운영 적용 보류" if f["_draft"]
                           else "정책 반영, 정상 흐름, 검토 근거")
            self.screenshot(s, "review", .59, 2.13, 8.16, 4.54)
            if f["_draft"]:
                self.text(s, "422 응답 본문 오류", 9.15, 2.4, 3.57, .65, 22, "blocked", True)
                self.text(s, "본문을 읽지 못하면\n이전 허용 상태가 남아\n버튼이 다시 켜질 수 있음",
                          9.15, 3.2, 3.57, 1.85, 20)
                self.text(s, "정적 검토 · 실행 재현 미검증\n서버 차단 우회는 아님\n수정·재검증 후 채택 판단",
                          9.15, 5.4, 3.57, 1.25, 17, "secondary")
            elif f.get("bodyVerification", {}).get("status") == "passed":
                self.side(s, "검토 범위와 결과", "확정 결함 0건\nTAP 열람은 검토자 미완료\n원시 실행 결과는 별도 보존")
                self.text(s, "422 본문 오류·잠금·복구\n촬영 밖 실제 브라우저 검사 통과\n운영 채택은 별도 판단", 9.15, 5.4, 3.57, 1.28, 17, "secondary")
            else:
                self.side(s, "이번 실행에서 확인", "정책을 동작에 반영\n정상 조건도 검증\n변경·검토 근거 보관")
                self.text(s, "시간·비용 효과는 미측정\n운영 적합성은 별도 판단", 9.15, 5.61, 3.57, 1.07, 18, "secondary")
            self.notes(s,
                "관찰한 가치는 업무 규칙의 반영과 검토 가능한 근거입니다. 이번 데모만으로 비용 절감, "
                "속도 향상, 품질 개선 비율을 산출하지 않습니다. completed는 검토가 실행됐다는 뜻이지 "
                "지적 사항이 없다는 뜻이나 출시 승인이 아닙니다. 아래의 실제 검토 요약과 제약을 그대로 "
                "읽고 남은 조치는 숨기지 마세요.\n" + f["review"]["summary"],
                self.sources("review", "captures.review", "tests", "browserChecks"))

            # 11 -- A proposed pilot, not an already-approved deployment plan.
            s = self.slide("11 파일럿 제안", "업무 하나, 책임자, 비교 기준부터 정합니다")
            rows = (
                ("범위", "반복되는 개발 업무 하나 · 입력·완료 기준·제외 범위 합의"),
                ("책임", "업무 책임자: 정책 승인 / 개발 책임자: 변경 / 검토 책임자: 채택 판단"),
                ("비교", "리드타임 · 검토 소요시간 · 재작업 횟수"),
                ("판단", "유사한 변경끼리 전후 비교 · 실패·대기·수정 시간도 포함"),
            )
            for i, (label, value) in enumerate(rows):
                y = 2.25 + i * 1.06
                self.text(s, label, .63, y, 1.29, .55, 24, "accent", True)
                self.text(s, value, 2.15, y, 10.47, .82, 23)
            self.text(s, "목표 효과는 측정 뒤 판단합니다. 이번 데모는 ROI 보장이 아닙니다.", .64, 6.57, 12.0, .3, 16, "secondary")
            self.notes(s,
                "제안은 업무 한 가지로 시작하는 파일럿입니다. 특정 인물이 이미 책임을 수락했거나 도입이 "
                "승인됐다는 뜻은 아닙니다. 업무 책임자는 정책과 예외, 개발 책임자는 변경과 복구, 검토 "
                "책임자는 품질과 채택 결정을 맡도록 합의하세요. 리드타임은 요청 접수부터 검증 완료까지, "
                "검토 시간은 실제 검토에 쓴 시간, 재작업은 합의한 완료 기준을 맞추기 위한 수정 횟수로 "
                "정의할 수 있습니다. 난이도가 비슷한 업무끼리 같은 정의로 비교하고 실패·승인 대기·재수정도 "
                "제외하지 마세요. 측정 전부터 절감 비율이나 투자수익을 약속하지 않습니다.")

            # 12 -- Environment, local provenance, product reference, all limits.
            s = self.slide("12 환경·출처·제약", "무엇을 실행했고, 무엇을 아직 모르는가")
            self.text(s, "실행 환경", .63, 2.06, 5.85, .48, 22, "accent", True)
            self.text(s, f"{excerpt(f['cli']['name'], 34)} · {excerpt(f['cli']['version'], 25)}\n"
                      f"모델: {excerpt(f['cli']['model'], 55)}", .64, 2.64, 5.85, 1.24, 19)
            self.text(s, "실행 근거", .63, 4.09, 5.85, .48, 22, "accent", True)
            self.text(s, "facts.json 및 연결된 원시 기록 열기", .64, 4.68, 5.85, .65, 19,
                      link=relative_link(self.path("facts")))
            self.text(s, f"코드 식별자: {excerpt(f['implementation']['codeHash'], 42)}", .64, 5.48, 5.85, .62, 16, "secondary")
            self.text(s, "GitHub Copilot CLI 제품 참고 문서", .64, 6.2, 5.85, .5, 17, "accent", link=CLI_DOCS)
            self.box(s, 6.78, 2.08, .012, 4.55, "line")
            self.text(s, "기록된 제약", 7.1, 2.06, 5.55, .48, 22, "accent", True)
            limits = f["limitations"]
            displayed = (["보완 필요 1건 · 운영 적용 보류\n422 본문 오류의 UI 복구 누락",
                          "촬영 불완전 · 승인 연속 영상 미확보\n당시 정지 캡처와 별도 앱 영상 사용",
                          "대화에서 승인 후 CLI에 전달\n후반 로컬 검증은 일괄 승인 범위에서 대행"]
                         if f["_draft"] else [excerpt(value, 72) for value in limits[:3]])
            if not displayed:
                displayed = ["입력 facts에 추가 제약 미기재"]
            for i, value in enumerate(displayed):
                wrapped = value if f["_draft"] else "\n".join(textwrap.wrap(value, width=28))
                self.text(s, wrapped, 7.11, 2.66 + i * 1.05, 5.52, 1.01, 18)
            self.text(s, f"제약 {len(limits)}건 · 전체 원문과 출처는 발표자 노트\n공식 제품 자료·보증·후원이 아닙니다.",
                      7.11, 6.06, 5.52, .7, 15, "secondary")
            approval_details = "\n".join(
                f"승인 기록: {item['kind']} / {item['status']}" for item in f["approvals"]
            )
            self.notes(s,
                f"실행 도구: {f['cli']['name']}\n버전: {f['cli']['version']}\n모델: {f['cli']['model']}\n"
                f"생산자가 기록한 코드 식별자: {f['implementation']['codeHash']}\n"
                f"입력 facts SHA-256: {f['_factsDigest']}\n\n"
                "기록된 제약 원문\n" + ("\n".join(f"- {item}" for item in limits) or "추가 제약 미기재") +
                "\n\n검토 요약 원문\n" + f["review"]["summary"] + "\n\n" + approval_details +
                "\n\n이 빌더는 파일 존재·형식·상태를 확인하지만 원시 기록의 진위를 독립 인증하지 않습니다. "
                "실행자와 코드 식별자는 기록 생산자가 확인한 값입니다. 제품 참고 문서는 로컬 실행의 "
                "증명이 아니며 이 빌드는 최신 문서 내용을 온라인으로 확인하지 않습니다. "
                "근거 파일 링크를 사용하려면 저장소 폴더 구조를 유지하세요. 영상만 PPT와 같은 폴더에 "
                "있으면 영상 상대 링크는 유지됩니다.\n"
                f"글꼴: {self.font}. 글꼴은 미포함이며 다른 OS에서 줄바꿈을 확인하세요. "
                "본문·도형은 편집 가능하고 실제 캡처는 원본 이미지입니다.\n"
                "GitHub 및 Microsoft는 해당 제품·상표의 권리자이며 본 자료는 비공식 실행 사례입니다.",
                self.sources(*f["_paths"].keys()) + "\n제품 참고: " + CLI_DOCS)

            if len(self.prs.slides) != 12:
                raise ValueError("슬라이드 수는 정확히 12장이어야 합니다.")
            for number, slide in enumerate(self.prs.slides, 1):
                if not slide.notes_slide.notes_text_frame.text.strip():
                    raise ValueError(f"슬라이드 {number}: 발표자 노트가 없습니다.")
                for shape in slide.shapes:
                    if (shape.left < 0 or shape.top < 0 or
                            shape.left + shape.width > self.prs.slide_width + 10 or
                            shape.top + shape.height > self.prs.slide_height + 10):
                        raise ValueError(f"슬라이드 {number}: 범위를 벗어난 개체 {shape.name}")
            return self.prs

    return LiveDeck()


def check_output() -> None:
    # Do not follow output symlinks into another agent's files, even inside ROOT.
    for path in [OUTPUT, *OUTPUT.parents]:
        if path == ROOT:
            break
        if path.is_symlink():
            raise ValueError(f"출력 경로의 심볼릭 링크는 허용하지 않습니다: {path}")
    if OUTPUT.exists() and not OUTPUT.is_file():
        raise ValueError(f"출력 경로가 일반 파일이 아닙니다: {OUTPUT}")
    OUTPUT.resolve().relative_to(ROOT)


def main(argv=None) -> int:
    global OUTPUT, VIDEO
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "deliverables/ghcp-live",
                        help="Output directory inside deliverables/ghcp-live; MP4 must be in the same folder")
    parser.add_argument("--facts", type=Path, default=DEFAULT_FACTS,
                        help="Repository-local facts JSON (relative paths are rooted at the repository).")
    parser.add_argument("--font", default="Apple SD Gothic Neo" if sys.platform == "darwin" else "Malgun Gothic",
                        help="Installed Korean font; fonts are not embedded.")
    parser.add_argument("--with-intro", action="store_true", help="Prepend the four sourced Copilot introduction slides")
    parser.add_argument("--draft", action="store_true", help="Explicit incomplete-capture review draft with visible limitations")
    parser.add_argument("--require-screenshots", action="store_true",
                        help="Explicit strict mode; final live decks ALWAYS require every screenshot.")
    args = parser.parse_args(argv)
    try:
        output_dir = (args.output_dir if args.output_dir.is_absolute() else ROOT / args.output_dir).resolve()
        output_dir.relative_to((ROOT / "deliverables/ghcp-live").resolve())
        OUTPUT = output_dir / "ghcp-agentic-development.pptx"
        VIDEO = output_dir / "ghcp-cxo-demo-ko.mp4"
        # Evidence gate precedes optional dependencies and ALL filesystem writes.
        facts = load_facts(args.facts, draft=args.draft)
        font = string(args.font, "font")
        check_output()
        base = load_helpers()
        if base.pptx.__version__ != "1.0.2":
            raise ValueError(f"python-pptx==1.0.2가 필요합니다. 현재: {base.pptx.__version__}")
        presentation = make_deck(base, facts, font).build()
        if args.with_intro:
            spec = importlib.util.spec_from_file_location("_copilot_intro", ROOT / "scripts/add-copilot-intro.py")
            intro = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(intro)
            intro.make_intro(base, presentation, intro.load_content(), font)
            slide_ids = presentation.slides._sldIdLst
            for index, slide_id in enumerate(list(slide_ids)[-4:]):
                slide_ids.remove(slide_id)
                slide_ids.insert(index, slide_id)
            if len(presentation.slides) != 16:
                raise ValueError("소개 포함 슬라이드는 정확히 16장이어야 합니다.")
            for number, slide in enumerate(presentation.slides, 1):
                footers = 0
                for shape in intro.all_shapes(slide.shapes):
                    if shape.has_text_frame and intro.PAGE.fullmatch(shape.text.strip()) and shape.top > base.Inches(6.9):
                        nodes = shape._element.xpath(".//a:t")
                        nodes[0].text = f"{number:02d} / 16"
                        for node in nodes[1:]:
                            node.text = ""
                        footers += 1
                    if (shape.left < 0 or shape.top < 0 or
                            shape.left + shape.width > presentation.slide_width + 10 or
                            shape.top + shape.height > presentation.slide_height + 10):
                        raise ValueError(f"슬라이드 {number}: 범위를 벗어난 개체 {shape.name}")
                if footers != 1 or not slide.notes_slide.notes_text_frame.text.strip():
                    raise ValueError(f"슬라이드 {number}: 페이지 번호 또는 발표자 노트 누락")
        # Serialize in memory first; never leave intermediate files or write the old deck.
        buffer = io.BytesIO()
        presentation.save(buffer)
        check_output()
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT.write_bytes(buffer.getvalue())
        print(OUTPUT)
        print(f"{len(presentation.slides)} editable Korean slides; actual evidence required; relative MP4 link; no publication.")
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"DECK NOT BUILT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
