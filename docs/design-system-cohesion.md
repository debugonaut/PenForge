# Penforge — design system cohesion proposals

Status: proposal, not an accepted requirement.
Date: 2026-10-20.
Read with: [design-language.md](design-language.md), [motion-design.md](motion-design.md), [document.md](../document.md).

This document records the cohesion problems measured in the current stylesheet set, the
fixes proposed for each, and — most importantly — an honest verdict on whether each fix
would change, preserve, or worsen the designs already accepted. Where a proposal
conflicts with an accepted decision, this document says so and does not recommend it.

**Decision rule for everything below:** the accepted design language and motion contract
win. Any proposal marked *Deliberate change* requires an explicit user decision before it
is implemented, and every change to stage, card, or toolbar layout requires rechecking
1440 / 768 / 390 / 320 plus a short viewport, per the design language.

---

## 1. Evidence: what is actually inconsistent

Measured over `src/**/*.css` (14 stylesheets) on 2026-10-20:

| Signal                              | Count                                                                                     |
| ----------------------------------- | ----------------------------------------------------------------------------------------- |
| Raw hex colour occurrences          | 697                                                                                        |
| Named colour variables              | ~35, most used once or twice                                                               |
| Distinct `border-radius` values     | ~20 declarations: 2, 3, 4, 5, 6, 7, 8, 9, 10, 12, 13, 15px, 50%, 99px, 999px, blob curves |
| Distinct `box-shadow` values        | ~20, of which ~12 hard-offset paper shadows in 4×5 / 5×6 / 6×7 / 7×8 / 8×9 variants      |
| Distinct `font-size` values         | ~20 between 6px and 30px                                                                   |
| Distinct transition durations       | 90, 100, 110, 120, 140, 160, 180, 200, 250, 280, 300, 450ms plus `0.18s`…`0.45s`        |
| Stylesheets defining their own ring | 5 define `:focus-visible`, 7 define none                                                   |

Most frequent colours: `#192e35` (45), `#fffaf1` (26), `#fffcf4` (16), `#fff` (11),
`#f4d16e` (11), `#fffaf0` (10), `#fffdf7` (9), `#36556a` (9), `#263f55` (8).

### 1.1 The two-palette problem (the single largest cause)

There are **two complete, deliberate palettes**:

| Role          | Studio/global (`globals.css`) | Landing (`LandingPage.module.css`) |
| ------------- | ----------------------------- | ---------------------------------- |
| Page paper    | `--paper: #f8f3e8`            | `--forge-paper: #f8f1e3`           |
| Raised paper  | `--paper-raised: #fffaf0`     | `--forge-white: #fffcf4`           |
| Ink           | `--ink: #263746`              | `--forge-ink: #192e35`             |
| Muted ink     | `--muted: #6b6a64`            | `--forge-muted: #4c6264`           |
| Coral         | `--coral: #e99485`            | `--forge-coral: #f0a18c`           |
| Yellow        | `--yellow: #f2ca70`           | `--forge-yellow: #f4d16e`          |
| Mint / green  | `--green: #a8bfa7`            | `--forge-mint: #a3c9b4`            |

The design language states that the landing values are authoritative for the landing
and that *"the older provisional palette in `document.md` and global studio tokens
differ intentionally"*. So this is not an accident to be cleaned up by deleting one
palette. It is a documented decision. The cohesion problem is not that two palettes
exist — it is that neither is namespaced to its surface, so `studio.css` and the
component modules reach for raw hex instead of either one.

### 1.2 Shadow and radius drift

Hard-offset shadows are the accepted "cut paper" language ("crisp dark outlines and
small offset shadows suggest cut paper"). The drift is in geometry and base colour:
offsets of 4×5, 5×6, 6×6, 6×7, 7×8, 8×9, coloured with `rgba(25,46,53,…)`,
`#e8ddcf`, `#c47e6e`, `#e3d9c9`, `#e3b69b`, `#26374612`. There is also one soft
blur shadow in the global tokens (`--shadow: 0 22px 46px rgba(48,36,25,.13)`), which
belongs to a different material language than the cut-paper offsets.

Radii drift in the same way: 5px, 6px, 7px, 8px, 9px, 10px, 12px, 13px, 15px, plus
the asymmetric corner values (`13px 0 0 13px`, `10px 10px 0 0`, `0 13px 13px 0`,
`0 0 10px 10px`) that the footer's "asymmetric paper corners" and the stationery
folded corner depend on. Those asymmetric values are not drift — they are authored.

---

## 2. Proposals, in detail, with impact verdicts

Legend for the verdict column:

- **Preserves** — mechanical change, no intended visual difference.
- **Minor change** — small pixel differences; re-capture required; low risk.
- **Deliberate change** — alters an accepted design decision; needs user approval.
- **Not recommended** — would worsen or contradict the accepted design; listed for
  completeness, not proposed for implementation.

### 2.1 Token file and colour roles

**Problem.** 697 raw hex occurrences. A designer changing the coral has to edit ~6
files and will miss some.

**Proposal.** A single `src/app/tokens.css` holding three layers:

1. *Palette* — the existing values, unchanged: `--forge-ink: #192e35`,
   `--forge-paper: #f8f1e3`, `--forge-white: #fffcf4`, `--forge-muted: #4c6264`,
   `--forge-coral: #f0a18c`, `--forge-yellow: #f4d16e`, `--forge-mint: #a3c9b4`,
   plus the studio palette under a `--studio-*` namespace (`--studio-ink: #263746`,
   `--studio-paper: #f8f3e8`, etc.), with the current `:root` aliases in `globals.css`
   kept as aliases so nothing breaks mid-migration.
2. *Roles* — `--surface-page`, `--surface-card`, `--text-body`, `--text-muted`,
   `--border-ink`, `--border-rule`, `--accent-*`, resolved per surface.
3. *Component tokens* — only where a component genuinely diverges.

**Impact.** **Preserves** if implemented as pure renaming with identical values. The
two palettes survive; only their namespacing becomes explicit. No screenshot changes.

**Do not** collapse the palettes into one. That is proposal 2.3.

### 2.2 Cream consolidation

**Problem.** Nine near-identical creams: `#fffaf1` (26), `#fffcf4` (16), `#fffaf0` (10),
`#fffdf7` (9), `#fffcf5` (6), `#f8f1e3`, `#f8f3e8`, `#f6eee0`, `#fbf7ef`.

**Proposal.** Name them, do not delete them. Define three named paper tones
(`--paper-page`, `--paper-card`, `--paper-sheet`) and map each existing value onto the
nearest name. Where two values differ by less than ~2% luminance and are not separately
documented, merge them into the closest documented one.

**Impact.** **Minor change.** The merges are the risk: several creams are the documented
landing palette (`#f8f1e3`, `#fffcf4`) and must never move. Merging only the
undocumented duplicates (`#fffaf1` vs `#fffcf5` vs `#fffdf7`) changes the loading sheet,
some card fills and the account panel by a hair. Worth doing; re-capture the affected
screens and confirm nothing looks dirty against the parchment texture.

### 2.3 One palette instead of two

**Proposal seen in many audits; deliberately not proposed.** Unifying landing and studio
onto one ink and one paper would make the product feel more uniform.

**Impact.** **Not recommended.** It directly contradicts an accepted decision, it would
change every studio screen's ink from `#263746` to `#192e35` (a visibly warmer, darker
navy) and every page paper tone, and the design language records the difference as
intentional. The landing's warmth against the studio's cooler navy is a real part of
how the two surfaces read. Only revisit this if the user explicitly decides the split
was a mistake.

### 2.4 Radius scale

**Problem.** ~20 distinct radius declarations, most between 2px and 15px.

**Proposal.** Three named steps plus authored exceptions:

| Token                    | Value                        | Used by                                        |
| ------------------------ | ---------------------------- | ---------------------------------------------- |
| `--radius-sm`            | 6px                          | inputs, chips, toolbar buttons                 |
| `--radius-md`            | 12px                         | cards, panels                                  |
| `--radius-lg`            | 22px                         | step stages, modals                            |
| `--radius-full`          | 999px                        | pills, avatars                                 |
| `--radius-corner-*`      | keep authored values         | footer asymmetric corners, note folded corner  |

**Impact.** **Minor change.** Moving 5px/7px/9px inputs and buttons to 6px is
sub-pixel-adjacent and safe. The asymmetric corner values and the 22px stages are
already consistent and must be kept as-is. Nothing in the accepted wizard changes.

### 2.5 Shadow scale

**Problem.** ~12 hard-offset variants with three base colours, plus one unrelated blur.

**Proposal.** Three elevations, all cut-paper:

| Token             | Value                              | Used by                       |
| ----------------- | ---------------------------------- | ----------------------------- |
| `--shadow-flat`   | none                               | inset panels, table rows      |
| `--shadow-sheet`  | `5px 6px 0 rgba(25,46,53,.12)`     | cards, chips, stationery      |
| `--shadow-lift`   | `8px 9px 0 rgba(25,46,53,.13)`     | stages, modals, hero film     |

The coloured shadows (`#c47e6e`, `#e3d9c9`, `#e3b69b`, `#e8ddcf`) move to the
matching ink-tinted token so the shadow colour tracks the surface, rather than being
a one-off literal.

**Impact.** **Minor change.** Cards currently at 4×5 or 6×7 gain or lose a pixel of
offset. The soft `--shadow: 0 22px 46px` should be inventoried before removal — if it
is the studio shell's ground shadow it is part of a different (accepted) material
language and stays, documented as `--shadow-soft` and reserved for the shell only.

### 2.6 Type scale

**Problem.** ~20 body sizes; 10px appears 76 times, 9px 50 times, 8px 22 times, 7px 14
times, 6px 10 times. Very small type is both a cohesion and an accessibility problem.

**Proposal.** Snap to **11 / 13 / 15 / 20 / display**, with one shared eyebrow class
(11px, 700, `0.12em` tracking, small caps) replacing the ad-hoc 8–10px uppercase
labels. Display sizes stay exactly as the design language records them
(`clamp(66px, min(8.6vw, 14svh), 144px)` etc.).

**Impact.** **Minor change, with one deliberate part.** Snapping 10→11 and 12/13→13 is
invisible in most layouts. Snapping 6/7/8/9px labels up to 11px **is** visible and can
break tight layouts at 320px — it must be done one surface at a time with the four
viewport checks. It also aligns with the design language's instruction to keep labels
"at practical readable sizes", so it improves on the accepted baseline rather than
contradicting it, but it does change accepted screenshots.

### 2.7 Spacing grid

**Problem.** Gaps of 9, 11, 13, 15, 18, 22, 26px across sibling components.

**Proposal.** A 4px base: 4 / 8 / 12 / 16 / 24 / 32 / 48, plus one `--section-gap`
shared by landing chapters, wizard steps and the desk.

**Impact.** **Minor change.** Cumulative and easy to get wrong: a card that loses 2px
of gap in three places loses 6px. Only apply where the value is not compensating for a
specific optical alignment (the wizard's stage padding and the desk's stationery
margins have been hand-tuned and rechecked; leave them).

### 2.8 Buttons

**Problem.** `.button`, `.button-dark`, `.button-light`, `.button-primary`,
`.button-destructive`, `.text-button`, `.small-link`, `.motion-button`, `.replay-button`
plus per-surface overrides. Inconsistent arrow treatment (`<span>→</span>` vs SVG vs
none), inconsistent hover (some translate, some tint, some nothing).

**Proposal.** Three emphases × two sizes, one hover model each (dark: translate 2px +
shadow collapse; paper: tint shift), one arrow convention, one focus treatment. Keep
`.text-button` as the tertiary and `.button-destructive` as the one semantic variant.

**Impact.** **Minor change.** Paddings and hover behaviour will visibly shift on the
landing CTAs and the wizard's action bars, which are accepted and screenshot-documented.
Implement surface by surface and re-capture. The accepted wizard rule is unaffected:
clearing, resetting and approval keep visible labels, tools keep icon + tooltip.

### 2.9 Forms

**Problem.** The Writing Desk inputs, the account panel and the wizard's style controls
disagree on height, border colour, placeholder shade and label placement.

**Proposal.** One input spec: 1px border, `--radius-sm`, paper fill, 40px min height
(44px on coarse pointers), labels above, helper text below at 11px muted, errors in
coral with a border-weight change so colour is not the only signal.

**Impact.** **Minor change.** Writing Desk stationery previews and real PNG/SVG export
are untouched; only the surrounding chrome changes.

### 2.10 Icon language

**Problem.** `WizardIcons` and the authored illustration set differ in stroke weight and
corner treatment.

**Proposal.** One 24×24 box, 1.75 stroke, one cap/join for all *functional UI* icons.

**Impact.** **Preserves for illustrations, minor for UI icons.** The characters and the
upload glyph are documented authored assets ("Do not use a bare text arrow or a thin
16px glyph for upload") and must not be restroked. Functional icons only.

### 2.11 Shared state components

**Problem.** Empty, loading, error and success are designed per screen: the workbench
empty state, the account "sign-in unavailable" box and the 404 are three unrelated
designs for the same message.

**Proposal.** `EmptyState`, `ErrorState`, `SuccessNote` and one skeleton recipe, all
built from the paper grammar (ink border, sheet shadow, cream fill, eyebrow + heading +
one line + one action).

**Impact.** **Minor change** for empty/error/success. **Deliberate change — and not
recommended — for loading.** The loading experience is not free to redesign: the
design language and motion contract fully specify `LoadingScene` (cream sheet, ink
outline, ruled lines, margin rule, self-writing line, pen behind the edge, 2.8s / 4.4s
gentle / finished-line reduced motion, `role="status"`) and `RouteTransition` (140ms
arm, 420ms minimum hold, 6s self-clear). Any shared loading state must **reuse**
`LoadingScene`, never replace it with a generic skeleton.

### 2.12 Step anatomy and action bars

**Problem.** The six wizard steps do not share a fixed skeleton: capture has no stage
card while review/edit/style/preview/export do; export places its actions inside the
stage; the action bar arrangement differs per step.

**Proposal.** Every step: eyebrow → display heading → one-line promise → stage card →
action bar, with tertiary left and primary right.

**Impact.** **Deliberate change.** The wizard is explicitly frozen: *"The user's later
wizard revision uses distinct colored paper stages, focused work areas and step
mascots; preserve the current implementation in `studio.css`, `StepStage.tsx` and the
creation panels."* Capture's chooser and desk mat are equally specified. Changing step
anatomy changes an accepted design and requires a user decision plus full recheck at
1440 / 1280×600 short laptop / 768 / 390 / 320. **Not proposed without that decision.**

### 2.13 Numbered step badges (the coral card motif)

**Problem/proposal.** Replace the current "ghosted step numeral" with a coral
numbered card — pencil in a circle, dashed ring, translucent numeral — across the six
steps.

**Impact.** **Deliberate change, and currently not recommended.** The ghosted numeral
is part of the accepted stage composition, and the design language already records the
mascot/numeral treatment as a later user-approved revision. Adding badge cards would
compete with the mascots that already stand beside each intro. If the motif is wanted,
it should be a small rail chip (progress indicator) rather than a card on the stage,
so the two accepted ideas do not collide.

### 2.14 Motion constants

**Problem.** 12+ durations hard-coded next to the three documented motion tokens.

**Proposal.** Map micro-feedback durations onto `--motion-quick` (140ms),
`--motion-standard` (280ms) and `--motion-reveal` (520ms) with the shared
`--motion-ease`.

**Impact.** **Minor change — with hard limits.** Several hard-coded numbers are
*documented choreography* and must never be retokenized: the 13s hero film with its ~3s
hold, the 2.8s / 4.4s loading line, the 320ms / 180ms Writing Desk paper settle, the
veil's 140ms arm / 160ms fade / 420ms hold. Anything else that moves to a token changes
timing by a few tens of milliseconds — imperceptible individually, and it must be
verified in both full and gentle variants plus OS reduced motion.

### 2.15 Focus rings

**Problem.** Five stylesheets define their own `:focus-visible`; seven define none.

**Fact.** There is already a global spec: `outline: 3px solid var(--focus);
outline-offset: 4px`. The overrides are mostly deliberate — the footer is documented as
having a *yellow focus ring* on ink.

**Proposal.** Do not force one ring. Document the base ring as the default and list the
approved exceptions (footer, ink surfaces, canvas, sliders) in the design system doc.

**Impact.** **Preserves.** Purely documentary; nothing visual changes.

### 2.16 Microcopy and voice

**Problem.** "local storage" vs "this device", inconsistent capitalisation of "demo",
buttons in mixed case.

**Proposal.** One voice rule-set: sentence case buttons, periods on helper lines,
"this device" not "local storage", one capitalisation for "demo", no exclamation marks.

**Impact.** **Minor change.** Copy appears in accepted screenshots and in the honest
demo/real boundary language, so edits must not soften the truthfulness rules (demo
extraction stays labelled demo; project previews stay labelled Caveat, never "your
generated font"). Wording changes only, no layout changes.

### 2.17 Governance

**Proposal.** Two mechanisms so the drift does not return:

1. `docs/design-system.md` — the token inventory with a one-line "when to use" each.
   The existing design-language doc describes mood and composition; it is not a
   mechanics reference.
2. A `/styleguide` route rendering every token, button, input, state and card.
3. A lint rule failing the build on raw hex outside `tokens.css`.

**Impact.** **Preserves** for the app. The styleguide is a new route (additive), and it
needs review at the standard four viewports before it ships.

---

## 3. Direct answer: what would change, and what would worsen

### 3.1 Would not change anything visible (safe)

- Token file with both palettes namespaced and aliased (2.1)
- Naming paper tones without merging (2.2, first half)
- Documented focus-ring exceptions (2.15)
- Design-system doc and styleguide route (2.17)
- Icon stroke unification for functional UI icons only (2.10)

### 3.2 Would change accepted designs slightly (do one at a time, re-capture)

- Cream merges between undocumented duplicates (2.2)
- Radius snapping of small inputs/buttons (2.4)
- Shadow geometry snapping to three elevations (2.5)
- Type snapping of 10–13px labels; more visible for the 6–9px labels (2.6)
- Spacing snapping where not hand-tuned (2.7)
- Button matrix consolidation (2.8)
- Form spec unification (2.9)
- Empty/error/success state components (2.11)
- Micro-duration retokenizing (2.14)
- Microcopy normalisation (2.16)

Each of these changes accepted screenshots. None of them changes a decision the user
made; they change the *execution* of those decisions. Expect to refresh the baseline
captures listed in the design language.

### 3.3 Would contradict an accepted decision (not proposed)

- Collapsing the landing and studio palettes into one (2.3)
- Replacing `LoadingScene` / `RouteTransition` with generic skeletons (2.11)
- Restructuring wizard step anatomy or action bars (2.12)
- Adding coral numbered badge cards over the accepted stage composition (2.13)
- Retokenizing the documented film, loading, settle or veil timings (2.14, the
  documented subset)

These are the items most likely to make the product feel *worse* rather than more
uniform, because the accepted decisions were deliberate revisions, not accidents.

---

## 4. Suggested sequencing, if approved

**Phase A — foundations (no decision needed).** 2.1, 2.15, 2.17. Pure mechanics; the
app's pixels stay identical while the vocabulary becomes single-sourced.

**Phase B — surface consolidation (small visible change).** 2.2, 2.4, 2.5, 2.8, 2.9.
One surface at a time: landing → wizard → desk → workbench → account/legal. Re-capture
after each surface.

**Phase C — typography and states.** 2.6, 2.11, 2.16. Most visible; do last, after the
card and shadow language is settled.

**Phase D — only with an explicit user decision.** 2.12, 2.13, and any palette merge.

---

## 5. Verification required before any of this ships

Per the project instructions and the accepted design language:

- `npm run typecheck`, `npm run lint`, `npm test`, `npm run build`.
- Running-page review at 1440px, 1280×600 short laptop, 768px, 390px and 320px,
  including keyboard focus, ordinary and gentle motion, and section re-entry.
- Motion changes additionally need the full verification list from the motion contract.
- Fresh captures saved to `artifacts/` after actual visual changes, replacing the
  baseline images referenced in the design language.
- No claim of improved accessibility without checking the real contrast of the merged
  muted inks and the enlarged labels.
