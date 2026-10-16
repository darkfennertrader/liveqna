# DOCX ingestion pipeline

![DOCX ingestion pipeline](docx-ingestion-pipeline.png)

This process covers DOCX ingestion through `normalized.json`. It deliberately
stops before chunking, embedding, Qdrant indexing, and retrieval.

## Authority rule

Native OOXML evidence has precedence for exact wording, numbers, dates, table
cells, links, revision state, and document structure. The Writer render and
multimodal descriptions are independent supporting evidence. They cannot
silently overwrite native facts.

## Processing

1. `docx_validation.py` validates required OOXML parts, ZIP paths, CRCs,
   compression limits, expanded size, paragraph count, table count, external
   links, and embedded or active content before parsing or rendering.
2. `docx.py` traverses Word stories directly. It unwraps content controls and
   extracts the main body, headers, footers, footnotes, endnotes, review
   comments, nested tables, merged-cell audit data, hyperlinks, image anchors,
   headings, core properties, and custom properties.
3. `INGESTION_DOCX_REVISION_POLICY=final` keeps inserted text and excludes
   deleted text. Set it to `original` for the inverse. The presence and counts
   of tracked revisions remain auditable and enter the review decision.
4. Review comments are retained in `normalized.json` but marked
   `retrieval_eligible=false`. Embedded or active objects create a deterministic
   high-severity finding rather than being silently executed or ignored.
5. `docx_renderer.py` runs an isolated LibreOffice Writer process, exports a
   stable PDF, and uses PyMuPDF to create `pages/page-NNNN.png` for every page.
6. Native blocks are mapped to rendered pages using deterministic normalized
   text matching. Weak mappings become explicit review findings.
7. The first multimodal call sees only the rendered page and independently
   extracts page-visible text, tables, diagrams, images, and layout meaning.
8. Element fusion suppresses duplicate visual text, retains genuine visual-only
   meaning, and quarantines conflicts. Native facts always keep precedence.
9. A second multimodal call judges the native, rendered, visual, and reconciled
   evidence. Deterministic findings and judge findings at MEDIUM or above place
   the document in `NEEDS_REVIEW`.
10. Material findings appear in `DOCX-WORD-PAGE-REVIEW-REPORT.md` and the common
    UI review workflow. Human answers are resolved into proposed page Markdown
    and must be explicitly applied before the document can be accepted.

## Main normalized DOCX records

- `docx_metadata`: revision policy, story inventory, sections, paragraphs,
  tables, images, content controls, text boxes, comments, notes, links,
  embeddings, properties, and package metrics.
- `blocks`: common cross-format text units with story, OOXML part, page,
  paragraph/table locator, retrieval eligibility, and structural attributes.
- `pages`: rendered image, page-mapped native evidence, visual extraction,
  reconciliation, independent review, human resolutions, and provenance.
- `quality`: warnings, counts, and extraction status.
