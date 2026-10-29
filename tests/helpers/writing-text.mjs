import { createTypeScriptLoader } from "./typescript.mjs";
import { unicodeModuleUrl } from "./unicode.mjs";
const moduleUrl = createTypeScriptLoader(import.meta.url);
export const writingTextModuleUrl = await moduleUrl(
  "../../src/lib/writing-text.ts",
  [["../../packages/contracts/src/unicode", unicodeModuleUrl]],
);
