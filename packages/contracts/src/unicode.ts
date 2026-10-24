/** Unicode 17 / UAX29 rev47. Offsets and identities are scalars, never UTF-16. */
import data from "../unicode/17.0.0/tables.json";
import { contentHash } from "./canonical";
export const unicodeVersion = "17.0.0";
type Range = (number | string)[];
function prop(
  cp: number,
  rows: Range[],
  fallback: string | boolean = "Other",
): string | boolean {
  let lo = 0,
    hi = rows.length;
  while (lo < hi) {
    const m = (lo + hi) >>> 1;
    if (Number(rows[m][1]) < cp) lo = m + 1;
    else hi = m;
  }
  return lo < rows.length && Number(rows[lo][0]) <= cp
    ? ((rows[lo][2] as string) ?? true)
    : fallback;
}
export function codepoints(text: string): number[] {
  const cps = Array.from(text, (c) => c.codePointAt(0)!);
  if (cps.some((cp) => cp >= 0xd800 && cp <= 0xdfff))
    throw new Error("invalid_unicode_scalar");
  return cps;
}
export function graphemes(text: string): number[][] {
  const cps = codepoints(text);
  if (!cps.length) return [];
  const p = cps.map((cp) => prop(cp, data.gcb)),
    ic = cps.map((cp) => prop(cp, data.incb));
  const starts = [0];
  for (let i = 1; i < cps.length; i++) {
    const a = p[i - 1],
      b = p[i];
    let join = false;
    if (a === "CR" && b === "LF") join = true;
    else if (
      ["Control", "CR", "LF"].includes(String(a)) ||
      ["Control", "CR", "LF"].includes(String(b))
    )
      join = false;
    else if (
      (a === "L" && ["L", "V", "LV", "LVT"].includes(String(b))) ||
      (["LV", "V"].includes(String(a)) && ["V", "T"].includes(String(b))) ||
      (["LVT", "T"].includes(String(a)) && b === "T")
    )
      join = true;
    else if (
      ["Extend", "ZWJ", "SpacingMark"].includes(String(b)) ||
      a === "Prepend"
    )
      join = true;
    else if (ic[i] === "Consonant") {
      let j = i - 1,
        linker = false;
      while (j >= 0 && ["Extend", "Linker"].includes(String(ic[j]))) {
        linker ||= ic[j] === "Linker";
        j--;
      }
      join = linker && j >= 0 && ic[j] === "Consonant";
    }
    if (!join && a === "ZWJ" && prop(cps[i], data.emoji, false)) {
      let j = i - 2;
      while (j >= 0 && p[j] === "Extend") j--;
      join = j >= 0 && Boolean(prop(cps[j], data.emoji, false));
    }
    if (!join && a === "Regional_Indicator" && b === a) {
      let j = i - 1;
      while (j >= 0 && p[j] === a) j--;
      join = (i - 1 - j) % 2 === 1;
    }
    if (!join) starts.push(i);
  }
  return starts.map((s, i) => cps.slice(s, starts[i + 1] ?? cps.length));
}
export function normalizeCodepoints(
  cps: number[],
  mode: "NFC" | "NFD",
): number[] {
  if (!cps.every((cp) => Number.isInteger(cp) && cp >= 0 && cp <= 0x10ffff))
    throw new Error("invalid_unicode_scalar");
  codepoints(String.fromCodePoint(...cps));
  const dc = data.decompositions as Record<string, number[]>,
    cc = data.ccc as Record<string, number>,
    pairs = data.compositions as Record<string, number>;
  const ccc = (cp: number) => cc[cp] ?? 0;
  const decompose = (cp: number): number[] => {
    if (cp >= 0xac00 && cp <= 0xd7a3) {
      const s = cp - 0xac00;
      return [
        0x1100 + Math.floor(s / 588),
        0x1161 + Math.floor((s % 588) / 28),
        ...(s % 28 ? [0x11a7 + (s % 28)] : []),
      ];
    }
    return dc[cp] ? dc[cp].flatMap(decompose) : [cp];
  };
  const result: number[] = [];
  for (const cp of cps)
    for (const c of decompose(cp)) {
      result.push(c);
      let j = result.length - 1;
      while (j > 0 && ccc(c) && ccc(result[j - 1]) > ccc(c)) {
        [result[j], result[j - 1]] = [result[j - 1], result[j]];
        j--;
      }
    }
  if (mode === "NFD" || !result.length) return result;
  const out = [result[0]];
  let starter = 0,
    last = ccc(result[0]);
  for (const cp of result.slice(1)) {
    const a = out[starter],
      c = ccc(cp);
    let combined = pairs[`${a},${cp}`];
    if (a >= 0x1100 && a <= 0x1112 && cp >= 0x1161 && cp <= 0x1175)
      combined = 0xac00 + (a - 0x1100) * 588 + (cp - 0x1161) * 28;
    else if (
      a >= 0xac00 &&
      a <= 0xd7a3 &&
      (a - 0xac00) % 28 === 0 &&
      cp >= 0x11a8 &&
      cp <= 0x11c2
    )
      combined = a + cp - 0x11a7;
    if (combined !== undefined && (last === 0 || last < c))
      out[starter] = combined;
    else {
      if (!c) starter = out.length;
      out.push(cp);
      last = c;
    }
  }
  return out;
}
export async function sequenceEvidence(text: string) {
  const cps = codepoints(text);
  return {
    schema_version: 1,
    unicode_version: unicodeVersion,
    source_codepoints: cps,
    source_hash: await contentHash(cps),
    graphemes: graphemes(text),
    derived: await Promise.all(
      (["NFC", "NFD"] as const).map(async (mode) => {
        const codepoints = normalizeCodepoints(cps, mode);
        return { mode, codepoints, sha256: await contentHash(codepoints) };
      }),
    ),
  };
}

export function graphemeStrings(text: string): string[] {
  return graphemes(text).map((cps) => String.fromCodePoint(...cps));
}
export function captureCodepoints(text: string, script: string): number[] {
  const cps = codepoints(text);
  if (script === "Latn") {
    if (cps.length !== 1 || cps[0] < 33 || cps[0] > 126)
      throw new Error("one_character_required");
    return cps;
  }
  if (!cps.length || cps.length > 16 || graphemes(text).length !== 1)
    throw new Error("one_cluster_required");
  if (
    cps.some(
      (cp, i) =>
        !(
          cp >= 0x900 &&
          cp <= 0x97f &&
          ![0x950, 0x951, 0x952, 0x953, 0x954, 0x970, 0x971].includes(cp)
        ) &&
        !(cp >= 33 && cp <= 126 && cps.length === 1) &&
        !([0x200c, 0x200d].includes(cp) && i > 0 && cps[i - 1] === 0x94d),
    )
  )
    throw new Error("unsupported_sequence_or_control");
  return cps;
}
