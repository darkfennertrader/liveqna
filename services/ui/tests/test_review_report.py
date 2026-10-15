from pathlib import Path

from app.review_report import render_review_report


STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_review_report_renders_markdown_and_local_slide_images() -> None:
    rendered = render_review_report(
        "version-123",
        "# Review\n\n![Slide 6](slides/slide-0006.png)\n\n| A | B |\n|---|---|\n| 1 | 2 |",
    )

    assert "<h1>Review</h1>" in rendered
    assert '<table>' in rendered
    assert (
        '/api/ingestion/versions/version-123/review-artifacts/'
        'slides/slide-0006.png'
    ) in rendered
    assert 'id="review-workflow"' in rendered
    assert '/static/review-report.js' in rendered
    assert "Show technical extraction report" in rendered


def test_review_report_escapes_embedded_html() -> None:
    rendered = render_review_report("version-123", "<script>alert(1)</script>")

    assert "<script>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in rendered


def test_review_report_hides_element_alignment_metrics() -> None:
    rendered = render_review_report(
        "version-123",
        "## Slide 1\n\n#### Alignment details\n\n- score=0.912\n\n"
        "### Independent review decision\n\n- Status: REVIEW_REQUIRED",
    )

    assert "score=0.912" not in rendered
    assert "Independent review decision" in rendered


def test_completed_review_returns_to_app_for_explicit_acceptance() -> None:
    javascript = (STATIC / "review-report.js").read_text(encoding="utf-8")

    assert 'element("button", "Return to app", "primary")' in javascript
    assert 'window.location.assign("/")' in javascript
    assert "select Accept for final approval" in javascript
