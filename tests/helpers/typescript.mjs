import { readFile } from "node:fs/promises";
import ts from "typescript";

export function javascriptModuleUrl(source) {
  return `data:text/javascript;base64,${Buffer.from(source).toString("base64")}`;
}

export function typeScriptModuleUrl(source) {
  return javascriptModuleUrl(
    ts.transpileModule(source, {
      compilerOptions: {
        module: ts.ModuleKind.ESNext,
        target: ts.ScriptTarget.ES2022,
      },
    }).outputText,
  );
}

/** Keep dependency substitutes explicit and resolve paths beside the calling test. */
export function createTypeScriptLoader(baseUrl) {
  return async function moduleUrl(path, replacements = []) {
    let source = await readFile(new URL(path, baseUrl), "utf8");
    for (const [from, to] of replacements) source = source.replaceAll(from, to);
    return typeScriptModuleUrl(source);
  };
}
