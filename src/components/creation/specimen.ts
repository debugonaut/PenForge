import type { CSSProperties } from "react";
import type { FontStyle } from "@/lib/contracts";

export const presets: {
  name: string;
  description: string;
  style: FontStyle;
}[] = [
  {
    name: "Natural",
    description: "The original rhythm",
    style: {
      thickness: 50,
      slant: 0,
      spacing: 50,
      smoothness: 50,
      proportion: 50,
    },
  },
  {
    name: "Neat",
    description: "A little more polished",
    style: {
      thickness: 44,
      slant: 0,
      spacing: 55,
      smoothness: 80,
      proportion: 50,
    },
  },
  {
    name: "Casual",
    description: "Loose and familiar",
    style: {
      thickness: 53,
      slant: 10,
      spacing: 60,
      smoothness: 38,
      proportion: 53,
    },
  },
  {
    name: "Bold",
    description: "Stronger on the page",
    style: {
      thickness: 78,
      slant: 1,
      spacing: 48,
      smoothness: 55,
      proportion: 57,
    },
  },
  {
    name: "Expressive",
    description: "A playful lean",
    style: {
      thickness: 60,
      slant: 19,
      spacing: 62,
      smoothness: 34,
      proportion: 58,
    },
  },
];

export function sampleStyle(style: FontStyle): CSSProperties {
  return {
    fontWeight: Math.round(400 + style.thickness * 3),
    letterSpacing: `${(style.spacing - 50) / 160}em`,
    transform: `skewX(${-style.slant}deg) scaleX(${0.78 + style.proportion / 220})`,
    textShadow: `0 0 ${Math.max(0, (90 - style.smoothness) / 45)}px currentColor`,
  };
}
