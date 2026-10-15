from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import NormalizedDocument


class ExtractionFailure(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        partial_document: NormalizedDocument | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.partial_document = partial_document
