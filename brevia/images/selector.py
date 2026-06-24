"""Image selection — Strategy A (default, structural) per ROADMAP §7.1.

Strategy A keeps every image whose anchoring content survived condensation and drops the
rest. Concretely, after condensation/synthesis an image survives iff a ``ImageBlock`` still
references it. This pass reconciles references and assets so the renderers never emit a broken
link or embed an unused file:

- keep an asset iff some surviving ``ImageBlock`` references it (its section survived);
- drop assets with no surviving reference (their whole section was cut);
- strip ``ImageBlock``s whose asset is missing (dangling references).

Strategy B (vision ranking, ``--rank-images``) is Phase 11 and lives in ``vision_ranker``.
"""

from __future__ import annotations

from dataclasses import dataclass

from brevia.ir.models import Chapter, Document, ImageBlock


@dataclass
class SelectionResult:
    """The reconciled document plus a report of what was kept/dropped."""

    document: Document
    kept_image_ids: list[str]
    dropped_image_ids: list[str]


class ImageSelector:
    """Strategy A image selector (structural, no LLM)."""

    def select(self, doc: Document) -> SelectionResult:
        referenced = {
            block.image_id for _, block in doc.iter_blocks() if isinstance(block, ImageBlock)
        }
        kept = sorted(iid for iid in doc.images if iid in referenced)
        dropped = sorted(iid for iid in doc.images if iid not in referenced)

        # Strip dangling ImageBlocks (referenced id with no asset) to avoid broken links.
        new_chapters = [
            Chapter(
                title=chapter.title,
                blocks=[
                    block
                    for block in chapter.blocks
                    if not (isinstance(block, ImageBlock) and block.image_id not in doc.images)
                ],
            )
            for chapter in doc.chapters
        ]
        new_images = {iid: asset for iid, asset in doc.images.items() if iid in referenced}
        cleaned = Document(metadata=doc.metadata, images=new_images, chapters=new_chapters)
        return SelectionResult(document=cleaned, kept_image_ids=kept, dropped_image_ids=dropped)
