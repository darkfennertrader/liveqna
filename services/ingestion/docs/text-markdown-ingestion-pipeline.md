# TXT and Markdown ingestion pipeline

![TXT and Markdown ingestion pipeline](text-markdown-ingestion-pipeline.png)

This process covers `.txt`, `.md`, and `.markdown` ingestion through
`normalized.json`. It deliberately stops before chunking, embedding, Qdrant
indexing, and retrieval. It makes no LLM calls and therefore has no human
discrepancy-review branch.

## Processing

1. The registry stores the immutable source and its SHA-256 content hash.
2. `text.py` checks the byte count before reading the file, performs strict
   UTF-8 decoding with optional BOM support, normalizes line endings, and
   rejects invalid UTF-8, NUL bytes, empty content, excessive lines, and
   pathological line lengths.
3. Plain text becomes paragraph blocks with exact one-based line ranges.
   Whitespace inside non-empty paragraphs is preserved rather than rewritten.
4. When the registered content profile is `transcript`, supplied timestamps
   and Unicode speaker labels become transcript source metadata. Missing
   speakers or timestamps are not invented.
5. Markdown uses a CommonMark token stream and token line maps. It recognizes
   ATX and Setext headings, paragraphs, ordered and unordered lists,
   blockquotes, fenced and indented code, valid pipe tables, links, image
   references, and document-start front matter.
6. Code remains non-executable. Front matter is retained for audit with
   `retrieval_eligible=false`. Link and image targets are recorded as block
   attributes; referenced image contents are not fetched or interpreted.
7. Resource limits also cap normalized block count and characters in any
   single block. A limit violation fails with `TEXT_LIMIT_EXCEEDED`.
8. `DocumentBuilder` assigns stable block IDs, heading-derived section paths,
   line locators, quality, and common normalized fields before storage writes
   `normalized.json` atomically.

## Configurable limits

- `INGESTION_TEXT_MAX_BYTES`
- `INGESTION_TEXT_MAX_LINES`
- `INGESTION_TEXT_MAX_LINE_CHARACTERS`
- `INGESTION_TEXT_MAX_BLOCKS`
- `INGESTION_TEXT_MAX_BLOCK_CHARACTERS`

All values must be positive. The checked-in defaults are documented in
`.env.example`.

## Result

Successful deterministic parsing is immediately eligible for the normal
document approval lifecycle. A Markdown image reference produces a quality
warning because the reference is preserved but its visual contents are not
part of this non-LLM pipeline.
