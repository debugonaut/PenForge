import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { test } from "node:test";
import ts from "typescript";

const source = await readFile(
  new URL("../src/components/shared/MotionPreference.tsx", import.meta.url),
  "utf8",
);
const compiled = ts.transpileModule(source, {
  compilerOptions: {
    module: ts.ModuleKind.CommonJS,
    target: ts.ScriptTarget.ES2022,
    jsx: ts.JsxEmit.ReactJSX,
  },
}).outputText;
const compiledModule = { exports: {} };
new Function("require", "exports", "module", compiled)(
  createRequire(import.meta.url),
  compiledModule.exports,
  compiledModule,
);
const { readSystemMotionPreference } = compiledModule.exports;

test("an old saved off flag cannot freeze motion when the OS uses full motion", () => {
  let storageReads = 0;
  globalThis.window = {
    matchMedia: () => ({ matches: false }),
    localStorage: {
      getItem: () => {
        storageReads++;
        return "off";
      },
    },
  };
  assert.equal(readSystemMotionPreference(), false);
  assert.equal(storageReads, 0);
});

test("the OS can select gentle choreography even when an old saved flag says on", () => {
  globalThis.window = {
    matchMedia: () => ({ matches: true }),
    localStorage: { getItem: () => "on" },
  };
  assert.equal(readSystemMotionPreference(), true);
});

test("blocked storage has no bearing on the motion variant", () => {
  globalThis.window = {
    matchMedia: () => ({ matches: false }),
    localStorage: {
      getItem: () => {
        throw new Error("Storage blocked");
      },
    },
  };
  assert.equal(readSystemMotionPreference(), false);
});
