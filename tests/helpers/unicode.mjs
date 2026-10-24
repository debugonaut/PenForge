import { readFile } from "node:fs/promises";
import { typeScriptModuleUrl as url } from "./typescript.mjs";
const base = new URL("../../packages/contracts/", import.meta.url);
const canonical = url(
  await readFile(new URL("src/canonical.ts", base), "utf8"),
);
const data = await readFile(
  new URL("unicode/17.0.0/tables.json", base),
  "utf8",
);
const source = (await readFile(new URL("src/unicode.ts", base), "utf8"))
  .replace(
    'import data from "../unicode/17.0.0/tables.json";',
    `const data=${data};`,
  )
  .replace('"./canonical"', JSON.stringify(canonical));
export const unicodeModuleUrl = url(source);
