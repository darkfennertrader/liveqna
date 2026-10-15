from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from html import escape
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdfcanvas
from reportlab.platypus import (
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


class _NumberedCanvas(pdfcanvas.Canvas):
    """Delay page output so each footer can include the final page count."""

    def __init__(self, *args: Any, footer_font: str = "Helvetica", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._saved_page_states: list[dict[str, Any]] = []
        self._footer_font = footer_font

    def showPage(self) -> None:  # noqa: N802 - ReportLab API
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        page_count = len(self._saved_page_states)
        for page_number, page_state in enumerate(self._saved_page_states, start=1):
            self.__dict__.update(page_state)
            self._draw_page_number(page_number, page_count)
            super().showPage()
        super().save()

    def _draw_page_number(self, page_number: int, page_count: int) -> None:
        self.saveState()
        self.setFillColor(colors.HexColor("#667085"))
        self.setFont(self._footer_font, 8)
        page_width, _ = self._pagesize
        self.drawRightString(
            page_width - 18 * mm,
            9 * mm,
            f"Page {page_number} / {page_count}",
        )
        self.restoreState()


class LiveLogNotFoundError(FileNotFoundError):
    pass


class LiveSessionLogStore:
    """Durable, per-session decision logs with human-readable PDF exports."""

    def __init__(self, root: Path, timezone_name: str = "Europe/Rome") -> None:
        self.root = root.resolve()
        if self.root.name != "live-session-logs":
            raise ValueError("live session log directory must end with 'live-session-logs'")
        self.root.mkdir(parents=True, exist_ok=True)
        self.timezone = ZoneInfo(timezone_name)
        self._lock = threading.RLock()
        self._session_paths: dict[str, Path] = {}
        self._register_font()

    @staticmethod
    def _register_font() -> None:
        font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
        if font_path.is_file() and "LiveQnA-DejaVu" not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(TTFont("LiveQnA-DejaVu", str(font_path)))

    @property
    def font_name(self) -> str:
        return (
            "LiveQnA-DejaVu"
            if "LiveQnA-DejaVu" in pdfmetrics.getRegisteredFontNames()
            else "Helvetica"
        )

    def start_session(
        self, session_id: str, *, project_id: str, project_name: str
    ) -> dict[str, Any]:
        with self._lock:
            existing = self._load_session(session_id)
            if existing is not None:
                return existing
            now = self._now()
            filename_time = now.astimezone(self.timezone).strftime(
                "%Y-%m-%d_%H-%M-%S%z"
            )
            path = self.root / f"{filename_time}_{session_id}.json"
            payload = {
                "schema_version": 1,
                "session_id": session_id,
                "project_id": project_id,
                "project_name": project_name,
                "started_at": now.isoformat(),
                "ended_at": None,
                "entries": [],
            }
            self._session_paths[session_id] = path
            self._persist(path, payload)
            return payload

    def record_event(self, event: dict[str, Any]) -> None:
        kind = event.get("kind")
        session_id = str(event.get("session_id") or "")
        if not session_id:
            return
        with self._lock:
            payload = self._load_session(session_id)
            if payload is None:
                return
            path = self._session_paths[session_id]
            if kind == "session_candidate":
                self._record_candidate(payload, event)
            elif kind == "session_candidate_failed":
                self._record_failure(payload, event)
            elif kind == "live_session_status" and event.get("status") == "ended":
                payload["ended_at"] = self._now().isoformat()
            else:
                return
            self._persist(path, payload)

    def attach_questions(self, run: dict[str, Any]) -> None:
        run_id = str(run.get("run_id") or "")
        if not run_id:
            return
        questions = [
            str(item.get("question") or "").strip()
            for item in run.get("questions", [])
            if str(item.get("question") or "").strip()
        ]
        with self._lock:
            for path in self.root.glob("*.json"):
                payload = self._read(path)
                changed = False
                for entry in payload.get("entries", []):
                    if entry.get("run_id") == run_id:
                        entry["questions"] = questions
                        entry["question_run_status"] = run.get("status")
                        entry["question_run_error"] = run.get("error") or ""
                        entry["question_generation_ms"] = run.get(
                            "question_generation_ms"
                        )
                        entry["updated_at"] = self._now().isoformat()
                        changed = True
                if changed:
                    self._session_paths[str(payload["session_id"])] = path
                    self._persist(path, payload)
                    return

    def combined_pdf(self) -> Path:
        with self._lock:
            sessions = [self._read(path) for path in sorted(self.root.glob("*.json"))]
            sessions = [session for session in sessions if session.get("entries")]
            if not sessions:
                raise LiveLogNotFoundError("no Live Q&A logs are available")
            output = self.root / "LIVE-QA-LOGS.pdf"
            self._build_pdf(output, sessions, title="Live Q&A logs")
            return output

    def download_filename(self) -> str:
        local_now = self._now().astimezone(self.timezone)
        timestamp = local_now.strftime("%Y-%m-%d_%H-%M-%S_%Z")
        return f"LIVE-QA-LOGS_{timestamp}.pdf"

    def clear(self) -> int:
        with self._lock:
            removed = 0
            for entry in self.root.iterdir():
                if entry.is_file() or entry.is_symlink():
                    entry.unlink()
                    removed += 1
            self._session_paths.clear()
            return removed

    def _record_candidate(
        self, payload: dict[str, Any], event: dict[str, Any]
    ) -> None:
        decision = str(event.get("decision") or "ignore").lower()
        now = self._now()
        entry = {
            "id": f"{payload['session_id']}:{event.get('sequence')}",
            "sequence": event.get("sequence"),
            "datetime": now.isoformat(),
            "datetime_local": self._human_datetime(now),
            "source_text": str(event.get("source_text") or "").strip(),
            "decision": decision,
            "trigger": event.get("trigger"),
            "reason": event.get("reason") or "",
            "instruction": event.get("instruction") or "",
            "relation": event.get("relation") or "none",
            "confidence": event.get("confidence"),
            "assessment_ms": event.get("assessment_ms"),
            "run_id": event.get("run_id"),
            "questions": [],
            "superseded_waits": [],
        }
        entries = payload.setdefault("entries", [])
        if decision != "wait":
            waits = [item for item in entries if item.get("decision") == "wait"]
            if waits:
                entry["superseded_waits"] = waits
                payload["entries"] = [
                    item for item in entries if item.get("decision") != "wait"
                ]
                entries = payload["entries"]
        entries.append(entry)

    def _record_failure(self, payload: dict[str, Any], event: dict[str, Any]) -> None:
        now = self._now()
        payload.setdefault("entries", []).append(
            {
                "id": f"{payload['session_id']}:{event.get('sequence')}:failed",
                "sequence": event.get("sequence"),
                "datetime": now.isoformat(),
                "datetime_local": self._human_datetime(now),
                "source_text": str(event.get("source_text") or "").strip(),
                "decision": "error",
                "error": str(event.get("error") or "assessment failed"),
                "questions": [],
                "superseded_waits": [],
            }
        )

    def _persist(self, path: Path, payload: dict[str, Any]) -> None:
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
        self._build_pdf(path.with_suffix(".pdf"), [payload], title="Live Q&A session log")

    def _build_pdf(
        self, output: Path, sessions: list[dict[str, Any]], *, title: str
    ) -> None:
        styles = getSampleStyleSheet()
        for style in styles.byName.values():
            style.fontName = self.font_name
        title_style = ParagraphStyle(
            "LogTitle",
            parent=styles["Title"],
            fontName=self.font_name,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#172033"),
        )
        stage_style = ParagraphStyle(
            "DecisionStage",
            parent=styles["BodyText"],
            fontName=self.font_name,
            fontSize=9,
            leading=11,
            alignment=TA_CENTER,
            textColor=colors.black,
        )
        body_style = ParagraphStyle(
            "LogBody",
            parent=styles["BodyText"],
            fontName=self.font_name,
            fontSize=9,
            leading=13,
        )
        story: list[Any] = [Paragraph(escape(title), title_style), Spacer(1, 7 * mm)]
        for session_index, session in enumerate(sessions):
            if session_index:
                story.append(PageBreak())
            story.extend(
                [
                    Paragraph(
                        f"Project: {escape(str(session.get('project_name') or 'Unknown'))}",
                        styles["Heading2"],
                    ),
                    Paragraph(
                        f"Session: {escape(str(session.get('session_id') or ''))}",
                        body_style,
                    ),
                    Paragraph(
                        f"Started: {escape(self._display_iso(session.get('started_at')))}",
                        body_style,
                    ),
                    Spacer(1, 5 * mm),
                ]
            )
            for entry in session.get("entries", []):
                questions = entry.get("questions") or []
                intro_lines = [
                    f"<b>Date and time:</b> {escape(str(entry.get('datetime_local') or ''))}",
                    f"<b>Transcript text:</b><br/>{escape(str(entry.get('source_text') or '')).replace(chr(10), '<br/>')}",
                ]
                detail_lines = []
                if questions:
                    question_lines = "<br/>".join(
                        f"{index}. {escape(str(question))}"
                        for index, question in enumerate(questions, start=1)
                    )
                    detail_lines.append(f"<b>Questions generated:</b><br/>{question_lines}")
                elif entry.get("decision") == "ready":
                    run_status = entry.get("question_run_status")
                    if run_status in {"failed", "ignored"}:
                        detail_lines.append(
                            f"<b>Questions generated:</b> none ({escape(str(run_status))})"
                        )
                    else:
                        detail_lines.append("<b>Questions generated:</b> pending")
                if entry.get("question_run_error"):
                    detail_lines.append(
                        f"<b>Question generation error:</b> {escape(str(entry['question_run_error']))}"
                    )
                if entry.get("reason"):
                    detail_lines.append(
                        f"<b>Reason:</b> {escape(str(entry['reason']))}"
                    )
                technical = []
                for key, label in (
                    ("trigger", "Trigger"),
                    ("relation", "Relation"),
                    ("confidence", "Confidence"),
                    ("assessment_ms", "Assessment time (ms)"),
                    ("question_generation_ms", "Question generation (ms)"),
                    ("run_id", "Run ID"),
                ):
                    value = entry.get(key)
                    if value is not None and value != "":
                        technical.append(f"{label}: {escape(str(value))}")
                if entry.get("superseded_waits"):
                    technical.append(
                        f"Superseded WAIT assessments: {len(entry['superseded_waits'])}"
                    )
                if technical:
                    detail_lines.append("<b>Details:</b> " + " | ".join(technical))
                decision = str(entry.get("decision") or "unknown").upper()
                superseded_wait_count = len(entry.get("superseded_waits") or [])
                stage_labels: list[str] = []
                stage_backgrounds: list[colors.Color] = []
                if superseded_wait_count:
                    wait_suffix = (
                        ""
                        if superseded_wait_count == 1
                        else f" ({superseded_wait_count} assessments)"
                    )
                    stage_labels.append(f"WAIT{wait_suffix}")
                    stage_backgrounds.append(colors.HexColor("#FDE68A"))
                if not (decision == "WAIT" and superseded_wait_count):
                    stage_labels.append(decision)
                    stage_backgrounds.append(
                        {
                            "READY": colors.HexColor("#86EFAC"),
                            "WAIT": colors.HexColor("#FDE68A"),
                            "IGNORE": colors.HexColor("#E5E7EB"),
                            "ERROR": colors.HexColor("#FCA5A5"),
                        }.get(decision, colors.HexColor("#E5E7EB"))
                    )
                if entry.get("question_run_status") == "failed":
                    stage_labels.append("QUESTION GENERATION FAILED")
                    stage_backgrounds.append(colors.HexColor("#FCA5A5"))
                stage_table = Table(
                    [[Paragraph(escape(label), stage_style) for label in stage_labels]]
                )
                stage_commands: list[tuple[Any, ...]] = [
                    ("BOX", (0, 0), (-1, -1), 0.8, colors.black),
                    ("INNERGRID", (0, 0), (-1, -1), 0.8, colors.black),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.black),
                    ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ]
                for column, background in enumerate(stage_backgrounds):
                    stage_commands.append(
                        ("BACKGROUND", (column, 0), (column, 0), background)
                    )
                stage_table.setStyle(TableStyle(stage_commands))
                content = [
                    Paragraph("<br/><br/>".join(intro_lines), body_style),
                    Spacer(1, 3 * mm),
                    stage_table,
                ]
                if detail_lines:
                    content.append(Spacer(1, 3 * mm))
                    content.append(
                        Paragraph("<br/><br/>".join(detail_lines), body_style)
                    )
                box = Table([[content]], colWidths=[174 * mm])
                box.setStyle(
                    TableStyle(
                        [
                            ("BOX", (0, 0), (-1, -1), 1.2, colors.HexColor("#344054")),
                            ("BACKGROUND", (0, 0), (-1, -1), colors.white),
                            ("LEFTPADDING", (0, 0), (-1, -1), 12),
                            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                            ("TOPPADDING", (0, 0), (-1, -1), 10),
                            ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                        ]
                    )
                )
                story.extend([box, Spacer(1, 5 * mm)])
        document = SimpleDocTemplate(
            str(output),
            pagesize=A4,
            rightMargin=18 * mm,
            leftMargin=18 * mm,
            topMargin=18 * mm,
            bottomMargin=18 * mm,
            title=title,
        )
        document.build(
            story,
            canvasmaker=lambda *args, **kwargs: _NumberedCanvas(
                *args, footer_font=self.font_name, **kwargs
            ),
        )

    def _load_session(self, session_id: str) -> dict[str, Any] | None:
        known = self._session_paths.get(session_id)
        if known is not None and known.is_file():
            return self._read(known)
        for path in self.root.glob(f"*_{session_id}.json"):
            self._session_paths[session_id] = path
            return self._read(path)
        return None

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _human_datetime(self, value: datetime) -> str:
        return value.astimezone(self.timezone).strftime("%d %B %Y, %H:%M:%S %Z")

    def _display_iso(self, value: Any) -> str:
        if not value:
            return ""
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return str(value)
        return self._human_datetime(parsed)
