# Writing Desk selection editing

Status: implementation authorized by the owner's “Ok sure, let's go.” on 2026-10-25.

Keep the three-column desk. Native left-drag or keyboard text selection in the words
editor or paper preview identifies a field and a grapheme-safe UTF-16 range. Right-click
offers “Fine-tune selection”; an ordinary visible action supports touch and keyboard.
Appearance switches between Whole page and Selection. Selected text remains marked
while the person adjusts size, weight, spacing and vertical position. Spaces support
Tighter/Wider. Reset affects the selection; existing Undo/Redo covers every edit.

Plain text remains canonical. Store up to 128 sorted, non-overlapping style ranges,
with only overridden properties. Split/merge ranges for overlapping edits and rebase
them when words change. Preserve grapheme clusters. New fields are optional so existing
drafts and create-idempotency hashes retain their meaning. Extend the strict account
contract and generated client; no database migration or font/glyph mutation is needed.

The shared layout and SVG renderer measures styled runs with the actual font,
reflows lines and expands the page for larger/raised/lowered text. PNG, SVG and print
retain identical styling; selection marks and guides are editor-only.

Sequential packages: range operations and layout; strict persistence contract; native
selection and Appearance controls; regression checks and responsive browser acceptance.
Check overlap/reset, edit rebasing, Unicode offsets, spaces, export parity, Undo/Redo,
refresh, account mapping and rejection of malformed ranges. Preserve all earlier
hosted, gentle-motion and physical-touch gates unless separately exercised. No commit,
push, account upload, model execution or publication is part of this task.

Implementation complete locally. Range helpers, the shared renderer, strict saved-page
contract and generated client, native selection hook and Appearance controls are in
place. Verification passed: 188 frontend, 19 schema and 5 PostgreSQL tests; type/lint,
build, contracts and compatibility, static boundaries and links. Browser checks covered
all four widths, a short viewport, selection/space/keyboard/history/reload flows, and
actual SVG/PNG downloads. Account UI auth, physical touch, OS-gentle and print-dialog
acceptance remain unexecuted. No commit or publication.
