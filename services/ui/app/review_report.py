from __future__ import annotations

import html
import re

import markdown


_RELATIVE_IMAGE = re.compile(
    r"\]\((?:\./)?((?:slides|pages|worksheets)/[^)]+\.png)\)"
)
_ALIGNMENT_DETAILS = re.compile(
    r"\n#### Alignment details\n.*?(?=\n### |\n## |\Z)", re.DOTALL
)


def render_review_report(version_id: str, report_markdown: str) -> str:
    """Render a stored document report without trusting report-authored HTML."""
    human_report = _ALIGNMENT_DETAILS.sub("", report_markdown)
    escaped = html.escape(human_report, quote=False)
    image_root = f"/api/ingestion/versions/{version_id}/review-artifacts/"
    linked = _RELATIVE_IMAGE.sub(
        lambda match: f"]({image_root}{match.group(1)})",
        escaped,
    )
    body = markdown.markdown(linked, extensions=["tables", "fenced_code"])
    safe_version_id = html.escape(version_id, quote=True)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Document review report</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{ margin: 0; background: #fff; color: #172033;
      font: 15px/1.55 system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 32px 28px 64px; }}
    h1, h2, h3 {{ color: #101828; line-height: 1.25; }}
    h2 {{ margin-top: 44px; padding-top: 20px; border-top: 2px solid #d0d5dd; }}
    img {{ display: block; max-width: 100%; height: auto; margin: 18px auto;
      border: 1px solid #d0d5dd; border-radius: 8px; }}
    table {{ width: 100%; border-collapse: collapse; margin: 16px 0 28px; }}
    th, td {{ padding: 9px 11px; border: 1px solid #d0d5dd; text-align: left;
      vertical-align: top; }}
    th {{ background: #f2f4f7; }}
    code, pre {{ white-space: pre-wrap; overflow-wrap: anywhere; }}
    blockquote {{ margin-left: 0; padding-left: 16px; border-left: 4px solid #98a2b3; }}
    hr {{ margin: 40px 0; border: 0; border-top: 2px solid #d0d5dd; }}
    .review-intro {{ margin-bottom: 22px; }}
    .review-progress {{ padding: 14px 16px; background: #f2f4f7; border-radius: 8px; }}
    .review-slide {{ margin: 32px 0; padding: 24px; border: 3px solid #344054;
      border-radius: 12px; background: #fff; box-shadow: 0 3px 12px rgba(16,24,40,.08); }}
    .review-slide > h2 {{ margin: 0 0 18px; padding: 0 0 12px; border-top: 0;
      border-bottom: 2px solid #d0d5dd; }}
    .review-question {{ margin-top: 20px; padding: 18px; border: 1px solid #98a2b3;
      border-left: 6px solid #d92d20; border-radius: 8px; background: #fffbfa; }}
    .review-question.applied {{ border-left-color: #039855; background: #f6fef9; }}
    .severity {{ font-weight: 700; color: #b42318; }}
    .evidence {{ color: #475467; }}
    textarea {{ box-sizing: border-box; width: 100%; min-height: 100px; padding: 12px;
      border: 1px solid #98a2b3; border-radius: 7px; font: inherit; background: #fff; }}
    button {{ margin: 12px 8px 0 0; padding: 10px 15px; border: 1px solid #344054;
      border-radius: 7px; background: #fff; color: #101828; font-weight: 650; cursor: pointer; }}
    button.primary {{ border-color: #175cd3; background: #175cd3; color: #fff; }}
    button:disabled {{ cursor: wait; opacity: .55; }}
    .proposal {{ margin-top: 16px; padding: 14px; background: #f9fafb;
      border: 1px solid #d0d5dd; border-radius: 7px; }}
    .message {{ margin-top: 10px; font-weight: 650; }}
    .error {{ color: #b42318; }}
    .success {{ color: #027a48; }}
    details.technical-report {{ margin-top: 48px; padding-top: 20px;
      border-top: 2px solid #d0d5dd; }}
    details.technical-report > summary {{ cursor: pointer; font-size: 18px;
      font-weight: 700; }}
  </style>
</head>
<body data-version-id="{safe_version_id}"><main>
  <section class="review-intro">
    <h1>Document discrepancy review</h1>
    <p>Answer each flagged inconsistency, select <strong>Resolve with AI</strong>,
    inspect the proposed correction, and then select <strong>Apply resolution</strong>.</p>
  </section>
  <div id="review-workflow"><p>Loading review questions…</p></div>
  <details class="technical-report">
    <summary>Show technical extraction report</summary>
    <p>Element-level alignment metrics are intentionally hidden from this human
    review view. They remain available in normalized JSON for technical audit.</p>
    {body}
  </details>
</main><script src="/static/review-report.js" defer></script></body>
</html>"""
