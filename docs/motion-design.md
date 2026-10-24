# Penforge — motion design contract

Recorded 2026-10-19. Read with the [design language](design-language.md) and [original frontend specification](../document.md). This document captures the later approved motion behavior and replaces conflicting earlier toggle/static-story proposals.

## Intent and preference behavior

Motion explains handwriting becoming a font and that font becoming a personal document. Related paper, letterforms and handwriting continue across states. Keep the landing expressive and the creation tools calm, with immediate control feedback and no animation gate before a usable action.

The user clarified that “remove the motion button” means **remove the global toggle**, with motion active by default. Do not restore a Motion on/off menu or persisted off preference. [MotionPreference.tsx](../src/components/shared/MotionPreference.tsx) reads the OS `prefers-reduced-motion` setting only:

- `data-motion="on"` stays set.
- `data-motion-variant="full"` uses the normal choreography.
- `data-motion-variant="gentle"` reduces travel/rotation/parallax and uses brief fades or subtle feedback. It remains animated and understandable.
- Old `penforge-motion` local-storage values are ignored. Existing regression tests cover this behavior.

Local **Pause/Replay** controls belong to the hero film and remain available. Offscreen and hidden-tab suspension conserve resources; they are not global motion preferences. Essential content and controls remain usable before animation code/assets are ready. No sound is currently implemented; any future sound follows explicit opt-in, muted defaults and the original no-music/no-voices rule.

## Motion tokens and ownership

Shared tokens in [globals.css](../src/app/globals.css):

| Token               | Full                     | Gentle |
| ------------------- | ------------------------ | ------ |
| `--motion-quick`    | 140ms                    | 100ms  |
| `--motion-standard` | 280ms                    | 180ms  |
| `--motion-reveal`   | 520ms                    | 240ms  |
| `--motion-ease`     | `cubic-bezier(.2,0,0,1)` | Same   |

Use GSAP for coordinated timelines and CSS for ordinary component feedback. Writing Desk uses the native Web Animations API for its paper settle. Native SVG supplies the film/illustration structure. These are the current tools; Motion for React, Rive and Lottie are options from the original brief, not required dependencies. One animation system should own a given property on an element. Prefer transform/opacity and small bounded effects over repeated layout work.

## Current choreography

| Surface                | Behavior to retain                                                                                                                             |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------- |
| Hero film              | Write → Capture → Digitize → Type, about 13 seconds with a roughly 3-second readable finished-result hold and a soft crossfade into repetition |
| Writing hand           | Nib follows measured text advances; the typing caret follows each visible glyph bound (and preserves actual word spaces); the same handwriting and visual source carry through capture and typing |
| Hero cutouts           | Brief entrance and restrained event-timed quill reaction; stay anchored behind the film throughout                                             |
| Chapter entrances      | Coordinated paper/glyph/type entrances when entering a chapter; repeat correctly on revisiting                                                 |
| How it works           | Paper → glyph → typed proof relationship, interactive glyph choice, review/approval feedback and a readable stamp                              |
| Style controls         | Immediate visible specimen changes and small tactile feedback; user input remains responsive                                                   |
| FAQ accordion          | One answer at a time; opening and closing heights plus chevrons use 100ms ease-out with no normal answer fade or delay. Gentle mode changes height immediately and uses 80ms opening / 60ms closing opacity feedback. The FAQ is ready on chapter entry without an extra wrapper animation. |
| Writing Desk           | A 320ms paper settle when format, paper or accent changes; gentle mode uses a 180ms opacity transition                                         |
| Loading scenes    | A handwritten line writes itself across the sheet in 2.8s and loops with a short hold; the paper floats 7px and the pen sways 3° beside it. Gentle mode slows the line to 4.4s, holds the paper still and stops the sway; OS reduced motion shows the finished line with no animation |
| Navigation veil   | App-wide: after 140ms of a pending internal navigation the scene fades in over the page (160ms) and leaves once the destination has painted, holding a 420ms minimum so it never flickers. Reduced motion swaps the fade for an instant swap; the line is already drawn |
| Focused creation tools | One quiet entrance per step: eyebrow, headline and copy rise, the mascot peeks in, then the tinted desk stage and its paper cards follow. Review chips and glyph tiles cascade once, the editor canvas settles when a letter is selected, style changes interpolate in the specimen, and preview paper rotates between surfaces. No perpetual animation competes with drawing or editing |

Studio implementation: one-shot CSS keyframes in [studio.css](../src/app/studio.css) are scoped under `.studio-app` and travel through `--reveal-y`, `--reveal-tilt`, `--reveal-scale`, `--pop-scale`, `--stagger` and `--glyph-step`. Gentle and reduced-motion modes shrink those to 4px/0/1 with the shorter tokens; no reveal depends on JavaScript. The editor canvas is keyed to the selected glyph, so its settle replays on an actual letter change rather than on every keystroke.

The loading scene's `loadingWrite`, `loadingBob` and `loadingSway` keyframes live in [globals.css](../src/app/globals.css) because route fallbacks render outside `.studio-app`; its stroke uses `pathLength="1"` so the dash timing is independent of the path's real length. Nothing in it depends on JavaScript, and gentle/reduced handling uses the same `data-motion-variant` and `prefers-reduced-motion` contract as the rest of the app.

Film implementation: [HandwritingFilm.tsx](../src/components/landing/HandwritingFilm.tsx) and [its CSS](../src/components/landing/HandwritingFilm.module.css). It waits for font readiness and the hand to decode, then composes the reveal against measured advances. Preserve nib coordinates, relative hand scale and the actual approved hand asset. Pause when its chapter is inactive or the document hidden; resume appropriately on return. Replay must reset the complete story without leaving hidden layers, masks or transforms behind.

Chapter/character timelines are scoped to [LandingPage](../src/components/landing/LandingPage.tsx). Process feedback is scoped to [HowItWorksDemo](../src/components/landing/HowItWorksDemo.tsx). Paper feedback belongs to [WritingDesk](../src/components/writing/WritingDesk.tsx). The unused earlier landing story and motion helper were removed during v1 preparation; the active timelines are unchanged.

## Viewport chapters and scrolling

The user requested a separate viewport chapter for each section, with a deliberate scroll advancing between them. Preserve the existing [useSectionNavigation](../src/components/landing/useSectionNavigation.ts) behavior and `penforge-section-scroll` lifecycle:

- A deliberate desktop wheel gesture advances to the adjacent chapter when that chapter fits and the current gesture is eligible.
- Tall chapters and expanded disclosures allow ordinary content scrolling before advancing. Nested scrollable editors consume their own gestures.
- Touch, keyboard, scrollbar and browser navigation remain usable. Explicit nav actions can move focus predictably without stealing focus while typing.
- Gentle mode avoids animated scroll travel while retaining chapter navigation and gentle element feedback.
- On scene re-entry, transitions complete and content is visible. Fast scrolling or crossing a viewport threshold must not strand a reveal halfway.

Do not enforce a fixed viewport height that clips forms, FAQs, controls or stationery. Check natural content growth after every substantial UI change. The earlier brief's native-scroll-only proposal is superseded by this accepted chapter behavior, while its accessibility requirements remain binding.

## Implementation discipline

Scope GSAP selectors and timelines to component roots. Revert contexts and clean up observers, media queries, visibility listeners and deferred imports. Guard async setup against unmount; React Strict Mode and route revisits must not duplicate timelines. Avoid leaving inline styles from an old full/gentle variant when the preference changes.

Animation must react to real UI states. A character theatrically moving a letter is an overlay, not permission to modify source data. Real asynchronous processing must use server states/work units; the film is clearly a concept demo, and its pace must never become a fabricated backend ETA.

Keep active sequences bounded to their chapter, and pause expensive work offscreen. Avoid autoplay audio, unbounded idle movement or repeated heavy blur/filter painting. The shared source-to-result motif is more useful than adding independent decorative animations. Preserve the final readable result long enough for the visitor to understand it.

## References and provenance

The previous implementation consulted these references for technique and direction. Review relevant current guidance when making motion changes; they do not override user decisions or require installing external skills/scripts.

| Reference                                                                                                 | Application to Penforge                                                                               |
| --------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| [LottieFiles motion design](https://github.com/LottieFiles/motion-design-skill)                           | Coordinated entrances, quick feedback and restrained paper settling; explicitly requested by the user |
| [GSAP skills](https://github.com/greensock/gsap-skills)                                                   | Scoped timeline ownership, cleanup and authored choreography                                          |
| [Breathtaking](https://dribbble.com/shots/13961411-Breathtaking)                                          | Layered depth and continuity between scenes                                                           |
| [RIFT](https://www.tryrift.fun/)                                                                          | Direct visual communication, playful timing and clear grid organization                               |
| [Zero-Jank Scroll](https://github.com/harshavarma02/zero-jank-scroll-agent-skill)                         | Scroll responsiveness and performance considerations                                                  |
| [mthines animations](https://github.com/mthines/agent-skills/blob/main/skills/design/animations/SKILL.md) | Timing, easing and purposeful feedback                                                                |
| [motion-design-agent](https://github.com/soilmass/motion-design-agent)                                    | Consistent narrative direction                                                                        |

The complete original reference lists remain in [document.md](../document.md) and the [quality update](specifications/design-quality-update.md). Repository-native SVG/CSS/GSAP motion is the current implementation; do not describe it as an embedded Veo video or a Lottie animation.

## Verification before completing a motion change

Review the running app in full and gentle variants, including desktop, tablet, mobile and short viewports. Verify first paint; the complete film; nib contact; final hold; Pause/Replay; leave/return; hidden-tab return; fast chapter crossing; expanded tall content; nested textarea scrolling; keyboard controls; focus; and route unmount/remount. Confirm both character attachment and heading clearance at each breakpoint.

Check that no reveal remains partially hidden after the viewport is crossed, and that local input is responsive throughout. Existing tests in [motion-preference.test.mjs](../tests/motion-preference.test.mjs) cover preference selection and stale flags, not visual choreography. Save screenshots or a short capture for actual visual changes and record only the behavior and devices genuinely inspected. Do not claim a universal frame rate without measurements.
