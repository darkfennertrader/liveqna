from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "app" / "static"


def test_source_selection_requires_a_real_project() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    backend = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert 'id="ingestion-select-btn" disabled' in html
    assert "ingestionSelectBtn.disabled = !ready" in javascript
    assert "const project = selectedIngestionProject();" in javascript
    assert 'form.append("project_id", selection.projectId)' in javascript
    assert "project_id: str = Form(...)" in backend
    assert "project_name: str = Form" not in backend


def test_projects_can_be_created_without_uploading_a_document() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    backend = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert 'id="ingestion-create-project-btn"' in html
    assert 'requestJson("/api/ingestion/projects"' in javascript
    assert '@app.post("/api/ingestion/projects", status_code=201)' in backend


def test_project_delete_warns_about_every_linked_data_store() -> None:
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    javascript = (STATIC / "app.js").read_text(encoding="utf-8")
    backend = (ROOT / "app" / "main.py").read_text(encoding="utf-8")

    assert 'id="ingestion-delete-project-btn"' in html
    assert "every linked document and version" in javascript
    assert "ingestion database records, and Qdrant points" in javascript
    assert '@app.delete("/api/ingestion/projects/{project_id}", status_code=202)' in backend
