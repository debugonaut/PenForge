import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import fs from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const root = fileURLToPath(new URL("../", import.meta.url));

test("contract generation ignores temporary path rules and host formatting config", async () => {
  const folder = await fs.mkdtemp(
    path.join(os.tmpdir(), "penforge-contract-test-"),
  );
  try {
    // An unanchored tmp/ ignore rule skipped the formatter on Linux CI.
    const temporary = path.join(folder, "tmp");
    await fs.mkdir(temporary);
    await fs.writeFile(
      path.join(folder, ".prettierrc.json"),
      JSON.stringify({ tabWidth: 8, useTabs: true, semi: false }),
    );
    const result = spawnSync(
      process.execPath,
      ["packages/contracts/scripts/generate.mjs", "--check"],
      {
        cwd: root,
        env: {
          ...process.env,
          TMPDIR: temporary,
          TMP: temporary,
          TEMP: temporary,
        },
        encoding: "utf8",
        timeout: 30000,
      },
    );
    assert.ifError(result.error);
    assert.equal(result.status, 0, result.stderr || result.stdout);
    assert.match(result.stdout, /Generated TypeScript matches\./);
  } finally {
    await fs.rm(folder, { recursive: true, force: true });
  }
});
