# Shared UI components

## Accordion

`accordion.tsx` is adapted from the official [shadcn/ui Radix accordion](https://ui.shadcn.com/docs/components/radix/accordion),
using the [new-york-v4 registry source](https://ui.shadcn.com/r/styles/new-york-v4/accordion.json)
retrieved on 2026-10-24. Retain [shadcn's MIT notice](SHADCN-LICENSE.txt).
The pinned `@radix-ui/react-accordion` dependency supplies keyboard, ARIA, focus,
measured content height and exit-presence behavior.

PenForge adaptations use CSS modules, an original inline chevron and native class
composition. No Tailwind, utility-class merge package or icon package is needed.
The landing supplies its paper/ink appearance through item, trigger and content
class names. The content class styles the inner answer wrapper, as in shadcn.

The FAQ uses a collapsible single-selection accordion. Opening one answer closes
the preceding answer with matching 100ms height animations, using Radix's
measured height instead of a fixed `max-height`. Closing content stays mounted for
its exit animation. Normal mode keeps text fully opaque with no fade or delay.
OS reduced motion uses 80ms/60ms opacity-only feedback; height and
chevron rotation change immediately. The existing gentle variant follows the same
rule. No global motion setting is added.
