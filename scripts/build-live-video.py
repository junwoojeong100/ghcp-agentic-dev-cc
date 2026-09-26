#!/usr/bin/env python3
"""Build an evidence-gated live film, never start Copilot or replay/redraw logs.

Usage (paths are relative to the repository, not the invoking directory):
    python3 -B scripts/build-live-video.py --validate-only
    python3 -B scripts/build-live-video.py --run-dir production/ghcp-live

Inputs: RUN/edit-list.json and RUN/facts.json. Output is always
    deliverables/ghcp-live/ghcp-cxo-demo-ko.{mp4,srt}
plus RUN/{timeline.json,narration-ko.txt}. Existing raw sources are read only.
RUN must be inside production/ghcp-live. Build intermediates are temporary.

Edit list: {title, scenes: [{id, title, narration, source, in, out, speed,
    kind: "terminal"|"app"|"card", approval: boolean, editLabel?}]}
Video times are seconds, with an exclusive out point; speed defaults to 1.
PNG scenes require duration instead of in/out; cards require duration and may
omit source to render an explicitly informational title card. There is no video
freeze extension. A PNG hold is always visibly labelled as a still. Each derived
duration must be a whole number of 30fps frames; invalid cuts are rejected, not
rounded or padded. 300s is an advisory target, never a trim/padding instruction.

Facts, schemaVersion 1:
    implementation: {status: "verified", evidence: path|[path,...]}
    tests: {passed: positive integer, failed: 0, evidence: ...}
    browserChecks: {passed: positive integer, failed: 0, evidence: ...}
    redTests: {failed: positive integer, evidence: ...}  # pre-fix execution
    delegation: {status: "completed", evidence: ...}
    review: {status: "completed", evidence: ...}
    approvals: [{kind, status, evidence, source?, in?, out?}, ...]
    limitations: [text, ...]
    sources: [{source, kind, sha256, evidence, captureManifest?, snapshotManifest?}]
At least one implementation-plan approval must be approved, and at least one
continuous approval video must be included. Optional approval source/in/out
triples protect a known request-through-follow-up interval against omissions,
cuts and speed changes. All source/evidence paths are nonempty, existing,
repository-relative files. Extra shared facts fields (including captures/video)
are not needed to build the film; any nested evidence fields are still checked.

Each source is SHA-256 checked. Terminal videos must be the original rawVideo
of a finalized, successful, nonfixture recorder capture.json, named by the
source record's captureManifest. Terminal PNGs additionally need their original
snapshotManifest. App footage/image and informational image authenticity is
producer-attested by source evidence; this cannot authenticate arbitrary prose
or independently infer human consent, test results, or approval boundaries.

--validate-only writes nothing and does not invoke say. It checks evidence,
media bounds, editing and text layout, NOT measured speech duration. A build
uses the installed macOS Yuna voice and measures every sentence before rendering;
if any scene is too short, it errors with the required time rather than dropping
audio. Audio is Korean synthetic narration only, not original capture audio.
Pillow, ffmpeg, ffprobe, macOS say and the historical builder's fonts must already
be installed. The historical main is never executed. No network/publication.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "production" / "ghcp-live"
OUT = ROOT / "deliverables" / "ghcp-live"
STEM = "ghcp-cxo-demo-ko"
W, H, FPS, SR = 1920, 1080, 30, 44100
HEADER, FOOTER = 70, 104
CONTENT_H = H - HEADER - FOOTER
LEAD, GAP, TAIL = 0.25, 0.18, 0.20
BG, INK, MUTED, ACCENT = "#101923", "#F4F7FA", "#C6D0DC", "#9DDCD1"
SCENE_KEYS = {"id", "title", "narration", "source", "in", "out", "speed",
              "kind", "approval", "editLabel", "duration"}
VIDEO_EXTENSIONS = {".webm", ".mp4", ".mov", ".mkv", ".m4v"}


def text(value, label, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()):
        raise ValueError(f"{label}: a {'possibly empty' if empty else 'nonempty'} string is required")
    if any(ord(c) < 32 and c not in "\n\r\t" for c in value):
        raise ValueError(f"{label}: control characters are not supported")
    return value.strip()


def number(value, label, *, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise ValueError(f"{label}: a finite {'positive' if positive else 'nonnegative'} number is required")
    return value


def object_value(value, label):
    if not isinstance(value, dict):
        raise ValueError(f"{label}: an object is required")
    return value


def inside(path, parent, label):
    resolved = path.resolve()
    if not resolved.is_relative_to(parent.resolve()):
        raise ValueError(f"{label}: path escapes {parent.relative_to(ROOT) if parent != ROOT else 'the repository'}")
    return resolved


def existing_file(value, label, *, manifest_path=False):
    raw = text(value, label)
    path = Path(raw)
    if path.is_absolute() and not manifest_path:
        raise ValueError(f"{label}: use a repository-relative path")
    if any(c in raw for c in "\n\r\x00"):
        raise ValueError(f"{label}: unsupported path characters")
    # Original recorder manifests contain absolute local paths. Only their
    # internal path fields may use those, and even those must remain in ROOT.
    path = inside(path if path.is_absolute() else ROOT / path, ROOT, label)
    if not path.is_file() or path.stat().st_size == 0:
        raise ValueError(f"{label}: missing or empty file: {path}")
    return path


def relative(path):
    return path.relative_to(ROOT).as_posix()


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path):
    def invalid(value):
        raise ValueError(f"{path}: nonfinite JSON number {value}")
    try:
        return object_value(json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid), str(path))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path}: invalid UTF-8 JSON: {exc}") from exc


def evidence_paths(value, label):
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or not values:
        raise ValueError(f"{label}: an evidence path or nonempty array of paths is required")
    return [existing_file(p, f"{label}[{i}]") for i, p in enumerate(values)]


def check_all_evidence(value, label="facts"):
    paths = []
    if isinstance(value, dict):
        for key, child in value.items():
            paths.extend(evidence_paths(child, f"{label}.evidence") if key == "evidence"
                         else check_all_evidence(child, f"{label}.{key}"))
    elif isinstance(value, list):
        for i, child in enumerate(value):
            paths.extend(check_all_evidence(child, f"{label}[{i}]"))
    return paths


DRAFT_NOTICE = "검토용 초안 · 승인 연속 영상 미확보"


def validate_review_draft(facts, draft=False):
    production = facts.get("production", {})
    findings = facts.get("openFindings", [])
    if not draft:
        if (production.get("status") == "review-draft" or production.get("captureComplete") is False
                or facts.get("requirementsSatisfied") is False or findings):
            raise ValueError("Incomplete capture or unresolved findings: final mode refused; use explicit --draft")
        return []
    if (production.get("status") != "review-draft" or production.get("captureComplete") is not False
            or facts.get("requirementsSatisfied") is not False or facts.get("finalAdoption") != "not-approved"
            or facts.get("implementation", {}).get("status") != "verified-with-open-finding"
            or not isinstance(findings, list) or not findings):
        raise ValueError("Draft requires explicit incomplete production, unresolved findings and no adoption")
    for item in findings:
        object_value(item, "openFinding")
        text(item.get("summary"), "openFinding.summary")
        if (item.get("status") != "unresolved" or item.get("verification") != "static-code-review"
                or item.get("executedReproduction") is not False or item.get("serverBlockingBypassed") is not False):
            raise ValueError("Draft finding must preserve its static-review, unexecuted scope")
        evidence_paths(item.get("evidence"), "openFinding.evidence")
    failures = evidence_paths(production.get("evidence"), "production.evidence")
    for path in failures:
        failure = read_json(path)
        if (failure.get("status") != "incomplete" or failure.get("captureComplete") is not False
                or failure.get("fixture") is not False or failure.get("approvalVideoRecovered") is not False
                or not failure.get("takeId")):
            raise ValueError("Draft requires authentic incomplete-capture diagnosis")
    return failures


def load_facts(path, draft=False):
    facts = read_json(path)
    if type(facts.get("schemaVersion")) is not int or facts["schemaVersion"] != 1:
        raise ValueError("facts.schemaVersion: integer 1 is required")
    validate_review_draft(facts, draft)
    implementation_status = "verified-with-open-finding" if draft else "verified"
    for key, status in (("implementation", implementation_status), ("delegation", "completed"), ("review", "completed")):
        item = object_value(facts.get(key), f"facts.{key}")
        if item.get("status") != status:
            raise ValueError(f"facts.{key}.status must be {status}; no final success film without actual evidence")
        evidence_paths(item.get("evidence"), f"facts.{key}.evidence")
    for key in ("tests", "browserChecks"):
        item = object_value(facts.get(key), f"facts.{key}")
        if type(item.get("passed")) is not int or item["passed"] <= 0:
            raise ValueError(f"facts.{key}.passed: a positive integer is required")
        if type(item.get("failed")) is not int or item["failed"] != 0:
            raise ValueError(f"facts.{key}.failed must be integer 0")
        evidence_paths(item.get("evidence"), f"facts.{key}.evidence")
    red_tests = object_value(facts.get("redTests"), "facts.redTests")
    if type(red_tests.get("failed")) is not int or red_tests["failed"] <= 0:
        raise ValueError("facts.redTests.failed: a positive integer from the actual pre-fix run is required")
    red_evidence = evidence_paths(red_tests.get("evidence"), "facts.redTests.evidence")
    green_evidence = evidence_paths(facts["tests"]["evidence"], "facts.tests.evidence")
    if set(red_evidence) & set(green_evidence):
        raise ValueError("facts.redTests/tests: pre-fix failure and final passing runs need distinct evidence files")
    approvals = facts.get("approvals")
    if not isinstance(approvals, list) or not approvals:
        raise ValueError("facts.approvals: actual human approval evidence is required")
    approved = False
    for i, item in enumerate(approvals):
        label = f"facts.approvals[{i}]"
        object_value(item, label)
        kind = text(item.get("kind"), f"{label}.kind")
        status = text(item.get("status"), f"{label}.status")
        evidence_paths(item.get("evidence"), f"{label}.evidence")
        approved |= kind == "implementation-plan" and status == "approved"
        if any(k in item for k in ("source", "in", "out")):
            existing_file(item.get("source"), f"{label}.source")
            start = number(item.get("in"), f"{label}.in")
            end = number(item.get("out"), f"{label}.out", positive=True)
            if end <= start:
                raise ValueError(f"{label}: protected out must exceed in")
    if not approved:
        raise ValueError("facts.approvals: implementation-plan with status approved and real evidence is required")
    limitations = facts.get("limitations")
    if not isinstance(limitations, list):
        raise ValueError("facts.limitations: an explicit array is required (may be empty)")
    for i, item in enumerate(limitations):
        text(item, f"facts.limitations[{i}]")
    records = facts.get("sources")
    if not isinstance(records, list) or not records:
        raise ValueError("facts.sources: actual source provenance records are required")
    sources = {}
    for i, item in enumerate(records):
        label = f"facts.sources[{i}]"
        object_value(item, label)
        source = existing_file(item.get("source"), f"{label}.source")
        if source in sources:
            raise ValueError(f"{label}: duplicate canonical source {source}")
        if item.get("kind") not in ("terminal", "app", "card"):
            raise ValueError(f"{label}.kind: terminal, app or card is required")
        digest = text(item.get("sha256"), f"{label}.sha256").lower()
        if not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError(f"{label}.sha256: expected SHA-256 hex digest")
        evidence_paths(item.get("evidence"), f"{label}.evidence")
        if item.get("fixture") is True:
            raise ValueError(f"{label}: fixture footage is not actual live evidence")
        sources[source] = {**item, "sha256": digest}
    return facts, sources, check_all_evidence(facts)


def execute(args):
    result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, check=False)
    if result.returncode:
        detail = result.stderr.strip()[-2400:]
        raise ValueError(f"{args[0]} exited {result.returncode}: {detail}")
    return result.stdout


def probe(path):
    payload = json.loads(execute(["ffprobe", "-v", "error", "-show_streams", "-show_format",
                                  "-of", "json", path]))
    streams = [s for s in payload.get("streams", []) if s.get("codec_type") == "video"]
    if not streams:
        raise ValueError(f"No video/image stream: {path}")
    stream = streams[0]
    if not stream.get("width") or not stream.get("height"):
        raise ValueError(f"Unknown source dimensions: {path}")
    raw_duration = stream.get("duration", payload.get("format", {}).get("duration"))
    duration = None if raw_duration in (None, "N/A") else float(raw_duration)
    if duration is not None and (not math.isfinite(duration) or duration <= 0):
        raise ValueError(f"Invalid media duration: {path}")
    return {"width": stream["width"], "height": stream["height"], "duration": duration,
            "frames": stream.get("nb_frames"), "stream": stream, "payload": payload}


def load_helpers(run_dir):
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("_live_video_helpers", ROOT / "scripts" / "build-video.py")
        if spec is None or spec.loader is None:
            raise ValueError("Cannot import historical font/speech/SRT helpers")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.P, module.OUT = run_dir, OUT
        if (module.W, module.H, module.FPS, module.SR) != (W, H, FPS, SR):
            raise ValueError("Historical helper media constants changed; inspect compatibility first")
        return module
    finally:
        sys.dont_write_bytecode = previous


def frame_count(duration, label):
    frames = duration * FPS
    nearest = round(frames)
    if nearest < 1 or abs(float(frames - nearest)) > 0.000001:
        closest = max(1, nearest) / FPS
        raise ValueError(f"{label}: duration {float(duration):.9f}s is not whole 30fps frames; "
                         f"choose an explicit cut/duration near {closest:.9f}s (no automatic rounding)")
    return nearest


def terminal_provenance(source, record, hashes, evidence, *, draft=False, failures=()):
    if draft:
        if source.suffix.lower() != ".png":
            raise ValueError("Review draft excludes terminal video; only original settled PNGs are permitted")
        failure_path = existing_file(record.get("captureFailureManifest"), "terminal.captureFailureManifest")
        if failure_path not in failures:
            raise ValueError("Terminal PNG must refer to the declared production failure")
        failure = read_json(failure_path)
        snapshot_path = existing_file(record.get("snapshotManifest"), "terminal.snapshotManifest")
        snapshot = read_json(snapshot_path)
        screenshot = existing_file(snapshot.get("screenshot"), "snapshot.screenshot", manifest_path=True)
        if (screenshot != source or snapshot.get("takeId") != failure.get("takeId")
                or type(snapshot.get("seq")) is not int or snapshot["seq"] != snapshot.get("renderedSeq")
                or snapshot.get("error") or snapshot.get("syncPending") or not snapshot.get("snapshotId")):
            raise ValueError("Draft terminal image is not its original settled recorder snapshot")
        evidence.update((failure_path, snapshot_path))
        return
    manifest_path = existing_file(record.get("captureManifest"), "terminal.captureManifest")
    evidence.add(manifest_path)
    manifest = read_json(manifest_path)
    if (manifest.get("fixture") is not False or manifest.get("status") != "recorded"
            or manifest.get("finalized") is not True or manifest.get("failure")
            or manifest.get("evidenceError")):
        raise ValueError(f"Terminal source requires a finalized nonfixture successful capture: {manifest_path}")
    child = object_value(manifest.get("childExit"), "capture.childExit")
    if type(child.get("exitCode")) is not int or child["exitCode"] != 0 or child.get("stopped"):
        raise ValueError(f"Terminal capture did not finish normally: {manifest_path}")
    if (manifest.get("rendererEnded") is not True or type(manifest.get("seq")) is not int
            or manifest["seq"] != manifest.get("renderedSeq") or manifest["seq"] != manifest.get("ack")):
        raise ValueError(f"Terminal capture final output is not verified as rendered: {manifest_path}")
    raw = existing_file(manifest.get("rawVideo"), "capture.rawVideo", manifest_path=True)
    if raw not in hashes:
        hashes[raw] = sha256(raw)
    if hashes[raw] != manifest.get("originalVideoSha256"):
        raise ValueError(f"Original capture hash mismatch: {raw}")
    if source.suffix.lower() != ".png":
        if source != raw or record["sha256"] != hashes[raw]:
            raise ValueError(f"Use original raw terminal video pixels, not a redraw/transcode: {source}")
        return
    snapshot_path = existing_file(record.get("snapshotManifest"), "terminal.snapshotManifest")
    evidence.add(snapshot_path)
    snapshot = read_json(snapshot_path)
    screenshot = existing_file(snapshot.get("screenshot"), "snapshot.screenshot", manifest_path=True)
    if (screenshot != source or snapshot.get("takeId") != manifest.get("takeId")
            or type(snapshot.get("seq")) is not int or snapshot["seq"] != snapshot.get("renderedSeq")
            or snapshot.get("error") or not snapshot.get("snapshotId")):
        raise ValueError(f"Terminal PNG must be its actual settled recorder snapshot: {snapshot_path}")


def caption_lines(helpers, value):
    f = helpers.font(30)
    lines = helpers.wrap(value, f, W - 100)
    measure = helpers.ImageDraw.Draw(helpers.Image.new("RGB", (1, 1)))
    if len(lines) > 2 or any(measure.textlength(line, font=f) > W - 100 for line in lines):
        raise ValueError(f"Narration sentence cannot fit two readable caption lines; split it: {value}")
    return lines


def heading_font(helpers, value, size, width):
    draw = helpers.ImageDraw.Draw(helpers.Image.new("RGB", (1, 1)))
    for n in range(size, size - 5, -1):
        f = helpers.font(n)
        if draw.textlength(value, font=f) <= width:
            return f
    raise ValueError(f"Heading/edit label is too long; shorten it: {value}")


def validate(run_dir, draft=False):
    facts_path = existing_file(relative(run_dir / "facts.json"), "facts")
    edit_path = existing_file(relative(run_dir / "edit-list.json"), "edit-list")
    facts, records, evidence = load_facts(facts_path, draft)
    failures = evidence_paths(facts["production"]["evidence"], "production.evidence") if draft else []
    edits = read_json(edit_path)
    title = text(edits.get("title"), "edit-list.title")
    scenes = edits.get("scenes")
    if not isinstance(scenes, list) or not scenes:
        raise ValueError("edit-list.scenes: a nonempty array is required")
    if not shutil.which("ffprobe"):
        raise ValueError("ffprobe must already be installed")
    helpers = load_helpers(run_dir)
    evidence = set(evidence) | {facts_path, edit_path}
    hashes, media, normalized, ids = {}, {}, [], set()
    cursor = 0
    for i, value in enumerate(scenes):
        label = f"scenes[{i}]"
        scene = dict(object_value(value, label))
        if set(scene) - SCENE_KEYS:
            raise ValueError(f"{label}: unsupported fields {sorted(set(scene) - SCENE_KEYS)}; edits may not be silently ignored")
        ident = text(scene.get("id"), f"{label}.id")
        if ident in ids:
            raise ValueError(f"{label}: duplicate id {ident}")
        ids.add(ident)
        scene["title"] = text(scene.get("title"), f"{label}.title")
        narration = text(scene.get("narration"), f"{label}.narration", empty=True)
        if "[[" in narration or "]]" in narration:
            raise ValueError(f"{label}.narration: macOS speech directives are not allowed")
        scene["narration"] = " ".join(narration.split())
        kind = scene.get("kind")
        if kind not in ("terminal", "app", "card") or type(scene.get("approval")) is not bool:
            raise ValueError(f"{label}: valid kind and explicit boolean approval are required")
        speed = number(scene.get("speed", 1), f"{label}.speed", positive=True)
        edit_label = text(scene.get("editLabel", ""), f"{label}.editLabel", empty=True)
        source = existing_file(scene["source"], f"{label}.source") if "source" in scene else None
        is_image = source is None or source.suffix.lower() == ".png"
        if source is None and kind != "card":
            raise ValueError(f"{label}: terminal/app scenes require real source media")
        if source:
            if source not in records or records[source]["kind"] != kind:
                raise ValueError(f"{label}: matching facts.sources provenance is required for {source}")
            if source.suffix.lower() not in VIDEO_EXTENSIONS | {".png"}:
                raise ValueError(f"{label}: supported sources are PNG or real WebM/MP4/MOV/MKV video")
            if source not in hashes:
                hashes[source] = sha256(source)
            if hashes[source] != records[source]["sha256"]:
                raise ValueError(f"{label}: source SHA-256 does not match facts: {source}")
            if source not in media:
                media[source] = probe(source)
                if is_image:
                    with helpers.Image.open(source) as image:
                        if image.format != "PNG":
                            raise ValueError(f"{label}: source must really be a PNG")
                        image.verify()
                if kind == "terminal":
                    terminal_provenance(source, records[source], hashes, evidence, draft=draft, failures=failures)
        if kind == "card" and not is_image:
            raise ValueError(f"{label}: informational cards use a PNG or a rendered title, not video")
        if is_image:
            if "in" in scene or "out" in scene or speed != 1:
                raise ValueError(f"{label}: stills/cards require duration, no in/out or speed changes")
            duration = Fraction(str(number(scene.get("duration"), f"{label}.duration", positive=True)))
            annotation = ("설명 카드 · 녹화 화면 아님" if kind == "card"
                          else f"실제 캡처 · 정지 화면 {float(duration):g}초")
        else:
            if "duration" in scene:
                raise ValueError(f"{label}: video duration is derived only from (out-in)/speed")
            start = number(scene.get("in"), f"{label}.in")
            end = number(scene.get("out"), f"{label}.out", positive=True)
            if end <= start:
                raise ValueError(f"{label}: out must be greater than in")
            source_duration = media[source]["duration"]
            if source_duration is None or end > source_duration + 0.000001:
                raise ValueError(f"{label}: out={end}s exceeds ffprobe video duration {source_duration}s")
            duration = (Fraction(str(end)) - Fraction(str(start))) / Fraction(str(speed))
            annotation = "연속 녹화 · 정속" if scene["approval"] else "실제 녹화"
            if not scene["approval"]:
                if start > 0 or end < source_duration - 0.000001:
                    annotation += " · 원본 구간 외 생략"
                annotation += f" · {speed:g}배속" if speed != 1 else " · 정속"
        if scene["approval"]:
            if is_image or speed != 1 or kind != "terminal":
                raise ValueError(f"{label}: approval must be continuous original terminal video at speed 1; no still/freeze")
            annotation = "승인 요청–응답–후속 실행 · 연속·정속"
        frames = frame_count(duration, label)
        displayed_label = annotation + (f" · {edit_label}" if edit_label else "")
        heading_font(helpers, " ".join(scene["title"].split()), 32, W - 200)
        heading_font(helpers, displayed_label, 20, W - 40)
        sentences = helpers.sentences(scene["narration"])
        for sentence in sentences:
            # The reused synth helper passes the final sentence as a say
            # argument. Do not let narration become a command-line option.
            if sentence.startswith("-"):
                raise ValueError(f"{label}.narration: rewrite sentences beginning with '-' as spoken prose")
            caption_lines(helpers, sentence)
        if kind == "card" and source is None:
            lines = helpers.wrap(scene["title"], helpers.font(72, bold=True), W - 180)
            draw = helpers.ImageDraw.Draw(helpers.Image.new("RGB", (1, 1)))
            if len(lines) > 7 or any(draw.textlength(line, font=helpers.font(72, bold=True)) > W - 180 for line in lines):
                raise ValueError(f"{label}: informational card title is too long")
        normalized.append({**scene, "speed": speed, "editLabel": edit_label, "source": relative(source) if source else None,
                           "displayedLabel": displayed_label, "duration": float(duration), "frames": frames,
                           "start": cursor / FPS, "end": (cursor + frames) / FPS,
                           "_source": source, "_image": is_image, "_sentences": sentences, "_draft": draft})
        cursor += frames
    if draft and any(s["approval"] for s in normalized):
        raise ValueError("Review draft cannot claim a continuous approval scene")
    if not draft and not any(s["approval"] for s in normalized):
        raise ValueError("At least one continuous, normal-speed actual approval video scene is required")
    for approval in facts["approvals"]:
        if "source" not in approval:
            continue
        path = existing_file(approval["source"], "approval.source")
        overlaps = [s for s in normalized if s["_source"] == path and not s["_image"]
                    and s["in"] < approval["out"] and s["out"] > approval["in"]]
        if not overlaps or any(not s["approval"] or s["in"] > approval["in"] or s["out"] < approval["out"] for s in overlaps):
            raise ValueError(f"Protected approval interval must remain whole in an approval scene: {approval['source']} "
                             f"[{approval['in']}, {approval['out']}]")
    reserved = {inside(OUT / f"{STEM}.{ext}", ROOT, "output") for ext in ("mp4", "srt")}
    reserved |= {inside(run_dir / name, ROOT, "output") for name in ("timeline.json", "narration-ko.txt")}
    if reserved & (set(hashes) | evidence):
        raise ValueError("An output path is also source/evidence; refusing to overwrite raw inputs")
    # Keep evidence as well as source hashes, so changed input records abort a
    # build rather than attaching an out-of-date approval or test attestation.
    for path in evidence:
        if path not in hashes:
            hashes[path] = sha256(path)
    return {"facts": facts, "factsPath": facts_path, "editPath": edit_path, "title": title,
            "scenes": normalized, "hashes": hashes, "records": records, "helpers": helpers,
            "frames": cursor, "duration": cursor / FPS}


def frame_image(helpers, scene, index, total, destination):
    image = helpers.Image.new("RGB", (W, H), BG)
    draw = helpers.ImageDraw.Draw(image)
    title = " ".join(scene["title"].split())
    draw.text((20, 2), title, fill=INK, font=heading_font(helpers, title, 32, W - 200))
    draw.text((W - 125, 10), f"{index + 1:02d} / {total:02d}", fill=MUTED, font=helpers.font(22))
    draw.text((20, 42), scene["displayedLabel"], fill=ACCENT,
              font=heading_font(helpers, scene["displayedLabel"], 20, W - 40))
    footer = (DRAFT_NOTICE + " · 보완 필요 1건 · 운영 적용 보류 · 한국어 합성 내레이션"
              if scene.get("_draft") else "사후 편집 · 한국어 합성 내레이션 · 영상 길이는 개발 소요 시간 아님")
    draw.text((24, H - 27), footer, fill=MUTED, font=helpers.font(18))
    if scene["_source"] is not None and scene["_image"]:
        with helpers.Image.open(scene["_source"]) as source:
            contained, xy = helpers.contain(source, (0, HEADER, W, H - FOOTER))
            image.paste(contained, xy)
    elif scene["_source"] is None:
        f = helpers.font(72, bold=True)
        lines = helpers.wrap(scene["title"], f, W - 180)
        y = HEADER + (CONTENT_H - len(lines) * 98) // 2
        for line in lines:
            draw.text(((W - draw.textlength(line, font=f)) / 2, y), line, fill=INK, font=f)
            y += 98
    image.save(destination)


def caption_image(helpers, value, destination):
    image = helpers.Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = helpers.ImageDraw.Draw(image)
    lines, f = caption_lines(helpers, value), helpers.font(30)
    y = H - FOOTER + (2 - len(lines)) * 17
    for line in lines:
        draw.text(((W - draw.textlength(line, font=f)) / 2, y), line, fill=INK, font=f)
        y += 36
    image.save(destination)


def prepare_audio(plan, work, rate):
    helpers = plan["helpers"]
    helpers.P = work
    (work / "audio").mkdir()
    segments, cues, total_pcm = [], [], bytearray()
    for scene in plan["scenes"]:
        pcm = bytearray(b"\0" * (round(LEAD * SR) * 2)) if scene["_sentences"] else bytearray()
        captions = []
        for i, sentence in enumerate(scene["_sentences"]):
            samples, _seconds = helpers.synth(sentence, rate)
            start = len(pcm) / (2 * SR)
            pcm.extend(samples)
            end = len(pcm) / (2 * SR)
            cues.append((scene["start"] + start, scene["start"] + end, sentence))
            captions.append((sentence, start, end))
            if i + 1 < len(scene["_sentences"]):
                pcm.extend(b"\0" * (round(GAP * SR) * 2))
        if scene["_sentences"]:
            pcm.extend(b"\0" * (round(TAIL * SR) * 2))
        needed = len(pcm) / (2 * SR)
        capacity = scene["frames"] * (SR // FPS) * 2
        if len(pcm) > capacity:
            minimum = math.ceil(needed * FPS) / FPS
            raise ValueError(f"Scene {scene['id']}: narration needs {needed:.3f}s (at least {minimum:.3f}s at 30fps), "
                             f"but source edit allows {scene['duration']:.3f}s; "
                             "shorten narration or explicitly select more real footage/card time. Audio was not truncated.")
        pcm.extend(b"\0" * (capacity - len(pcm)))
        total_pcm.extend(pcm)
        segments.append(captions)
    audio = work / "audio" / "narration-ko.wav"
    with wave.open(str(audio), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(SR)
        output.writeframes(total_pcm)
    return audio, segments, cues


def render_scene(helpers, scene, index, total, captions, work):
    base = work / f"frame-{index:03d}.png"
    frame_image(helpers, scene, index, total, base)
    output = work / f"segment-{index:03d}.mp4"
    command = ["ffmpeg", "-v", "error", "-nostdin", "-y", "-loop", "1", "-framerate", FPS, "-i", base]
    filters, label, next_input = [], "0:v", 1
    if not scene["_image"]:
        # Seek before decoding; a live take may include hours of approval waits.
        command.extend(["-ss", f"{scene['in']:.12f}", "-i", scene["_source"]])
        filters.append(f"[1:v:0]trim=start=0:end={scene['out'] - scene['in']:.12f},"
                       f"setpts=(PTS-STARTPTS)/{scene['speed']:.12f},"
                       "scale=w='trunc(iw*sar/2)*2':h=ih,setsar=1,"
                       f"scale={W}:{CONTENT_H}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
                       f"fps={FPS},format=yuv420p[raw]")
        filters.append(f"[0:v][raw]overlay=x=(W-w)/2:y={HEADER}+(H-{HEADER}-{FOOTER}-h)/2:"
                       "shortest=1:eof_action=endall[composite]")
        label, next_input = "composite", 2
    for i, (sentence, start, end) in enumerate(captions):
        caption = work / f"caption-{index:03d}-{i:03d}.png"
        caption_image(helpers, sentence, caption)
        command.extend(["-loop", "1", "-framerate", FPS, "-i", caption])
        out_label = f"caption{i}"
        filters.append(f"[{label}][{next_input}:v]overlay=0:0:shortest=1:"
                       f"enable='gte(t,{start:.9f})*lt(t,{end:.9f})'[{out_label}]")
        label, next_input = out_label, next_input + 1
    filters.append(f"[{label}]setsar=1,format=yuv420p[outv]")
    command.extend(["-filter_complex_threads", 1, "-filter_complex", ";".join(filters),
                    "-map", "[outv]", "-an", "-frames:v", scene["frames"], "-r", FPS,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", 18, "-threads", 4,
                    "-video_track_timescale", 90000, "-movflags", "+faststart", output])
    execute(command)
    info = probe(output)
    if info["frames"] is None or int(info["frames"]) != scene["frames"]:
        raise ValueError(f"Scene {scene['id']}: source did not supply the expected frames; refusing a frozen/blank tail")
    if abs(info["duration"] - scene["frames"] / FPS) > 0.0001:
        raise ValueError(f"Scene {scene['id']}: encoded duration does not match exact edit duration")
    return output


def check_final(path, plan):
    info = probe(path)
    if info["width"] != W or info["height"] != H or int(info["frames"] or 0) != plan["frames"]:
        raise ValueError("Final video dimensions/frame count differ from the audited edit")
    if abs(info["duration"] - plan["duration"]) > 0.0001:
        raise ValueError("Final video duration differs from the audited edit")
    audio = [s for s in info["payload"].get("streams", []) if s.get("codec_type") == "audio"]
    if len(audio) != 1 or "duration" not in audio[0]:
        raise ValueError("Final narration audio stream is missing")
    if abs(float(audio[0]["duration"]) - plan["duration"]) > 0.03:
        raise ValueError("Final audio/video duration mismatch; no audio truncation is allowed")
    # Decode to completion as well as inspecting container metadata.
    execute(["ffmpeg", "-v", "error", "-xerror", "-nostdin", "-i", path, "-f", "null", "-"])


def check_outputs(run_dir):
    files = [OUT / f"{STEM}.{ext}" for ext in ("mp4", "srt")]
    files += [run_dir / name for name in ("narration-ko.txt", "timeline.json")]
    for path in files:
        inside(path, ROOT, "output")
        for candidate in (path, *path.parents):
            if candidate == ROOT:
                break
            if candidate.is_symlink():
                raise ValueError(f"Output paths must not follow symlinks into other assets: {candidate}")
        if path.exists() and not path.is_file():
            raise ValueError(f"Output path is not a regular file: {path}")


def build(plan, run_dir, rate, target):
    check_outputs(run_dir)
    for tool in ("ffmpeg", "say"):
        if not shutil.which(tool):
            raise ValueError(f"{tool} must already be installed")
    if not re.search(r"^Yuna(?:\s+\([^\r\n]+\))?\s+ko[_-]", execute(["say", "-v", "?"]), re.MULTILINE):
        raise ValueError("Installed Korean say voice Yuna is required; no voice installation/substitution is performed")
    inside(OUT, ROOT / "deliverables", "output directory")
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".video-build-", dir=run_dir) as temporary:
        work = Path(temporary)
        audio, captions, cues = prepare_audio(plan, work, rate)
        segments = []
        for i, scene in enumerate(plan["scenes"]):
            segments.append(render_scene(plan["helpers"], scene, i, len(plan["scenes"]), captions[i], work))
            print(f"RENDERED {i + 1}/{len(plan['scenes'])} {scene['id']} {scene['duration']:.3f}s", flush=True)
        concat = work / "concat.txt"
        # Generated simple basenames avoid concat-demuxer path escaping issues.
        concat.write_text("".join(f"file '{p.name}'\n" for p in segments), encoding="utf-8")
        final = work / f"{STEM}.mp4"
        execute(["ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "concat", "-safe", "1", "-i", concat,
                 "-i", audio, "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy",
                 "-af", "loudnorm=I=-16:TP=-1.5:LRA=7", "-c:a", "aac", "-b:a", "160k", "-ar", SR,
                 "-video_track_timescale", 90000, "-movflags", "+faststart", final])
        check_final(final, plan)
        for path, digest in plan["hashes"].items():
            if sha256(path) != digest:
                raise ValueError(f"Source/evidence changed during rendering; final film not installed: {path}")
        srt = work / f"{STEM}.srt"
        stamp = plan["helpers"].stamp
        srt.write_text("\n".join(f"{i + 1}\n{stamp(a)} --> {stamp(b)}\n{value}\n"
                                 for i, (a, b, value) in enumerate(cues)), encoding="utf-8")
        narration = work / "narration-ko.txt"
        narration.write_text("\n\n".join(f"[{s['id']}] {s['title']}\n{s['narration']}" for s in plan["scenes"]) + "\n", encoding="utf-8")
        timeline = {"schemaVersion": 1, "title": plan["title"], "builtAt": datetime.now(timezone.utc).isoformat(),
                    "facts": relative(plan["factsPath"]), "factsSha256": plan["hashes"][plan["factsPath"]],
                    "editList": relative(plan["editPath"]), "editListSha256": plan["hashes"][plan["editPath"]],
                    "duration": plan["duration"], "targetSeconds": target, "targetDeltaSeconds": plan["duration"] - target,
                    "width": W, "height": H, "fps": FPS, "audio": {"voice": "Yuna", "rate": rate, "synthetic": True,
                    "originalSourceAudioIncluded": False}, "limitations": plan["facts"]["limitations"],
                    "production": plan["facts"].get("production"),
                    "requirementsSatisfied": plan["facts"].get("requirementsSatisfied"),
                    "finalAdoption": plan["facts"].get("finalAdoption"),
                    "provenanceLimits": "Producer-attested execution/consent and app authenticity; original snapshot metadata, capture or failure evidence and file hashes checked.",
                    "sourceEvidence": [{"path": relative(p), "sha256": h} for p, h in sorted(plan["hashes"].items())],
                    "video": {"path": relative(OUT / final.name), "sha256": sha256(final)}, "scenes": []}
        for i, scene in enumerate(plan["scenes"]):
            row = {key: value for key, value in scene.items() if not key.startswith("_")}
            row.update({"index": i, "in": scene.get("in"), "out": scene.get("out"),
                        "sourceSha256": plan["hashes"].get(scene["_source"])})
            timeline["scenes"].append(row)
        timeline_file = work / "timeline.json"
        timeline_file.write_text(json.dumps(timeline, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        # Each replacement is atomic. timeline.json is installed last as the
        # completed-build audit marker and contains the final video's digest.
        check_outputs(run_dir)
        for src, dest in ((final, OUT / final.name), (srt, OUT / srt.name),
                          (narration, run_dir / narration.name), (timeline_file, run_dir / timeline_file.name)):
            if dest.is_symlink():
                raise ValueError(f"Refusing to overwrite a symlink output: {dest}")
            os.replace(src, dest)
    print(f"FILM READY {OUT / (STEM + '.mp4')}; {plan['duration']:.3f}s; "
          f"target {target:g}s is advisory, not a development-time claim", flush=True)


def main(argv=None):
    global OUT
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run-dir", default="production/ghcp-live")
    parser.add_argument("--output-dir", default="deliverables/ghcp-live",
                        help="Output directory inside deliverables/ghcp-live; use a new folder for a new take")
    parser.add_argument("--draft", action="store_true", help="Explicit incomplete-capture draft with visible limitations; never a final film")
    parser.add_argument("--validate-only", action="store_true", help="read-only input/media/text validation; no speech or film")
    parser.add_argument("--target-seconds", type=float, default=300, help="advisory duration only (default 300)")
    parser.add_argument("--rate", type=int, default=185, help="installed Yuna narration words/minute (80–300)")
    args = parser.parse_args(argv)
    try:
        output_dir = Path(args.output_dir)
        OUT = inside(output_dir if output_dir.is_absolute() else ROOT / output_dir,
                     ROOT / "deliverables/ghcp-live", "--output-dir")
        number(args.target_seconds, "--target-seconds", positive=True)
        if not 80 <= args.rate <= 300:
            raise ValueError("--rate must be between 80 and 300")
        raw_dir = Path(args.run_dir)
        run_dir = inside(raw_dir if raw_dir.is_absolute() else ROOT / raw_dir, LIVE, "--run-dir")
        if not run_dir.is_dir():
            raise ValueError(f"Missing run directory: {run_dir}")
        plan = validate(run_dir, draft=args.draft)
        if args.validate_only:
            print(f"INPUTS VALID: {len(plan['scenes'])} scenes, {plan['duration']:.3f}s; "
                  "no outputs written; narration duration still requires measured synthesis")
        else:
            build(plan, run_dir, args.rate, args.target_seconds)
        return 0
    except (ValueError, OSError, ImportError, subprocess.SubprocessError, wave.Error) as exc:
        print(f"build-live-video: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
