import { createTypeScriptLoader } from "./helpers/typescript.mjs";
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

const moduleUrl = createTypeScriptLoader(import.meta.url);
const { projectService, handwritingService, fontService } = await import(await moduleUrl("../src/lib/mock-services.ts"));
const key = "penforge-projects-v1";
const recoveryKey = "penforge-projects-recovery-v1";

class MemoryStorage {
  data = new Map();
  failWrites = false;
  getItem(key) {
    return this.data.get(key) ?? null;
  }
  setItem(key, value) {
    if (this.failWrites)
      throw new DOMException("Quota exceeded", "QuotaExceededError");
    this.data.set(key, String(value));
  }
  removeItem(key) {
    this.data.delete(key);
  }
}
let local;
beforeEach(() => {
  local = new MemoryStorage();
  globalThis.window = { localStorage: local };
});

const stored = () => JSON.parse(local.getItem(key));
const glyph = (character = "A") => ({
  id: "glyph-0",
  character,
  sourceCharacter: "A",
  status: "approved",
  revision: 0,
  rotation: 0,
  scale: 100,
  crop: 0,
  strokes: [],
});

test("versioned writes persist before promise settlement and field patches preserve other changes", async () => {
  const project = await projectService.create("  My letters  ");
  assert.equal(project.name, "My letters");
  assert.equal(stored().schemaVersion, 1);
  const first = projectService.update(project.id, { previewText: "Hello" });
  assert.equal(stored().projects[0].previewText, "Hello");
  const second = projectService.update(project.id, { step: "style" });
  await Promise.all([first, second]);
  const fresh = await projectService.get(project.id);
  assert.equal(fresh.previewText, "Hello");
  assert.equal(fresh.step, "style");
});

test("legacy projects survive and gain the schema envelope on the next write", async () => {
  const project = await projectService.create("Legacy");
  local.setItem(key, JSON.stringify([project]));
  assert.equal((await projectService.list())[0].name, "Legacy");
  await projectService.update(project.id, { name: "Migrated" });
  assert.equal(stored().schemaVersion, 1);
  assert.equal(stored().projects[0].name, "Migrated");
});

test("corrupt JSON is kept unchanged until explicit recovery backs it up", async () => {
  const raw = '{"interrupted-write":';
  local.setItem(key, raw);
  await assert.rejects(projectService.create("New"), { code: "invalid-data" });
  assert.equal(local.getItem(key), raw);
  assert.equal((await projectService.storageInfo()).state, "needs-recovery");
  assert.equal(await projectService.recoveryBackup(), raw);
  assert.deepEqual(await projectService.recoverLocalData(), []);
  assert.equal(local.getItem(recoveryKey), raw);
  assert.equal(await projectService.recoveryBackup(), raw);
  assert.deepEqual(await projectService.list(), []);
  assert.equal((await projectService.storageInfo()).recoveryAvailable, true);
});

test("schema checks find malformed and duplicate projects while recovery preserves valid drafts", async () => {
  const project = await projectService.create("Valid");
  const invalid = {
    ...project,
    id: "broken",
    style: { ...project.style, smoothness: "NaN" },
  };
  const raw = JSON.stringify({
    schemaVersion: 1,
    projects: [project, invalid, project],
  });
  local.setItem(key, raw);
  const info = await projectService.storageInfo();
  assert.equal(info.recoverableProjects, 1);
  assert.equal(info.invalidProjects, 2);
  await assert.rejects(projectService.list(), { code: "invalid-data" });
  assert.equal(local.getItem(key), raw);
  assert.equal((await projectService.recoverLocalData())[0].id, project.id);
  assert.equal(local.getItem(recoveryKey), raw);
});

test("unknown versions are protected and reset affects only project storage", async () => {
  const raw = JSON.stringify({ schemaVersion: 42, projects: [] });
  local.setItem(key, raw);
  local.setItem("unrelated-preference", "keep");
  local.setItem(recoveryKey, "backup");
  await assert.rejects(projectService.create("New"), {
    code: "unsupported-version",
  });
  await assert.rejects(projectService.recoverLocalData(), {
    code: "unsupported-version",
  });
  assert.equal(local.getItem(key), raw);
  assert.equal(await projectService.recoveryBackup(), raw);
  await projectService.resetLocalData();
  assert.deepEqual(await projectService.list(), []);
  assert.equal(local.getItem(recoveryKey), null);
  assert.equal(local.getItem("unrelated-preference"), "keep");
});

test("invalid runtime patches are rejected without damaging the saved draft", async () => {
  const project = await projectService.create("Safe");
  const before = local.getItem(key);
  await assert.rejects(
    projectService.update(project.id, {
      style: { ...project.style, thickness: Infinity },
    }),
    { code: "invalid-project" },
  );
  assert.equal(local.getItem(key), before);
  await projectService.update(project.id, {
    id: "injected",
    isDemo: false,
    updatedAt: "invalid",
  });
  const saved = await projectService.get(project.id);
  assert.equal(saved.id, project.id);
  assert.equal(saved.origin.kind, "demo");
  assert.equal(saved.isDemo, undefined);
  assert.ok(Number.isFinite(Date.parse(saved.updatedAt)));
});

test("late saves and updates cannot resurrect a deleted project", async () => {
  const project = await projectService.create("Deleted");
  await projectService.remove(project.id);
  await assert.rejects(projectService.save(project), { code: "not-found" });
  await assert.rejects(projectService.update(project.id, { name: "Back" }), {
    code: "not-found",
  });
  assert.deepEqual(await projectService.list(), []);
});

test("extraction requires a sample, keeps edited glyphs and never claims actual processing", async () => {
  const project = await projectService.create("Letters");
  const unavailable = await handwritingService.extract(project.id);
  assert.equal(unavailable.status, "needs-input");
  await assert.rejects(
    handwritingService.submitSample(project.id, { kind: "draw", strokes: [] }),
    { code: "invalid-project" },
  );
  await handwritingService.submitSample(project.id, { kind: "demo" });
  const result = await handwritingService.extract(project.id);
  assert.equal(result.status, "completed");
  assert.match(result.message, /fixture, not extracted/);
  const ready = await projectService.get(project.id);
  assert.equal(ready.glyphs.length, 66);
  const edited = {
    ...ready.glyphs[0],
    status: "edited",
    rotation: 15,
    revision: 1,
  };
  await handwritingService.reviewGlyph(project.id, edited);
  await handwritingService.extract(project.id);
  assert.equal((await projectService.get(project.id)).glyphs[0].rotation, 15);
});

test("in-flight extraction does not recreate a draft deleted during the delay", async () => {
  const project = await projectService.create("Remove during extraction");
  await handwritingService.submitSample(project.id, { kind: "demo" });
  const extraction = handwritingService.extract(project.id);
  await projectService.remove(project.id);
  await assert.rejects(extraction, { code: "not-found" });
  assert.deepEqual(await projectService.list(), []);
});

test("coverage includes absent alphabet and arbitrary preview characters, with duplicate warnings", async () => {
  const project = await projectService.create("Coverage");
  const empty = await fontService.validate(project.id);
  assert.ok(empty.missing.includes("A"));
  assert.ok(empty.warnings.some((message) => message.includes("Prepare")));
  await projectService.update(project.id, {
    glyphs: [glyph(), { ...glyph(), id: "second" }],
    previewText: "Aé",
  });
  const result = await fontService.validate(project.id);
  assert.ok(result.missing.includes("é"));
  assert.ok(!result.missing.includes("A"));
  assert.ok(
    result.warnings.some((message) => message.includes("same character")),
  );
  assert.ok(
    result.warnings.some((message) => message.includes("Real font outlines")),
  );
});

test("local style suggestions recognize common wording and report unmatched requests honestly", async () => {
  const project = await projectService.create("Style");
  const match = await fontService.suggestStyle(
    project.id,
    "Make it bolder and a little slanted",
  );
  assert.equal(match.style.thickness, 75);
  assert.equal(match.style.slant, 18);
  assert.match(match.job.message, /No model was called/);
  const unmatched = await fontService.suggestStyle(
    project.id,
    "I like my alphabet",
  );
  assert.deepEqual(unmatched.style, project.style);
  assert.match(unmatched.job.message, /No matching change/);
});

test("export always reports demo unavailability and verifies the project and formats", async () => {
  const project = await projectService.create("Export");
  const noFormat = await fontService.requestExport(project.id, []);
  assert.equal(noFormat.errorCode, "FORMAT_REQUIRED");
  const result = await fontService.requestExport(project.id, ["ttf"]);
  assert.equal(result.status, "needs-input");
  assert.equal(result.errorCode, "FONT_GENERATION_UNAVAILABLE");
  assert.match(result.message, /No file was created/);
  await assert.rejects(fontService.requestExport("missing", ["ttf"]), {
    code: "not-found",
  });
});

test("failed backup writes leave corrupt source data intact", async () => {
  const raw = "broken-data";
  local.setItem(key, raw);
  local.failWrites = true;
  await assert.rejects(projectService.recoverLocalData(), {
    name: "QuotaExceededError",
  });
  assert.equal(local.getItem(key), raw);
  assert.equal(local.getItem(recoveryKey), null);
});

test("unavailable browser storage is reported without pretending drafts were saved", async () => {
  Object.defineProperty(globalThis.window, "localStorage", {
    get() {
      throw new DOMException("Blocked", "SecurityError");
    },
  });
  assert.equal((await projectService.storageInfo()).state, "unavailable");
  await assert.rejects(projectService.create("Not saved"), {
    code: "unavailable",
  });
});

function observedSample() {
  const strokes = [
    [
      { x: 0.2, y: 0.3 },
      { x: 0.4, y: 0.5 },
    ],
  ];
  return {
    kind: "draw",
    strokes,
    captureStartedAtMs: 1700000000000,
    strokeDocument: {
      schema_version: 1,
      coordinate_system: {
        origin: "top_left",
        x_positive: "right",
        y_positive: "down",
        normalized: true,
      },
      capture_size_px: { width: 700, height: 290 },
      timing: "observed",
      operations: [],
      parent_source_hash: null,
      strokes: [
        {
          stroke_id: "stable-capture-id",
          pointer_type: "mouse",
          points: [
            { ...strokes[0][0], t_ms: 0 },
            { ...strokes[0][1], t_ms: 20 },
          ],
        },
      ],
    },
  };
}

test("observed capture metadata and legacy absence survive local save, reload and duplication", async () => {
  const project = await projectService.create("Observed source");
  const sample = observedSample();
  await projectService.update(project.id, { sample });
  assert.deepEqual((await projectService.get(project.id)).sample, sample);
  const copy = await projectService.duplicate(project.id);
  assert.deepEqual(copy.sample, sample);
  const legacy = { kind: "draw", strokes: [[{ x: 0.1, y: 0.2 }]] };
  await projectService.update(copy.id, { sample: legacy });
  assert.deepEqual((await projectService.get(copy.id)).sample, legacy);
});

test("malformed or mismatched capture metadata cannot replace a valid local draft", async () => {
  const project = await projectService.create("Protected source");
  const valid = observedSample();
  await projectService.update(project.id, { sample: valid });
  const bytes = local.getItem(key);
  const mutations = [
    (sample) => {
      sample.strokeDocument.strokes[0].points[0].x = 0.9;
    },
    (sample) => {
      sample.strokeDocument.strokes[0].points[1].t_ms = -1;
    },
    (sample) => {
      sample.strokeDocument.strokes[0].points[0].pressure = 0.5;
    },
    (sample) => {
      sample.strokeDocument.capture_size_px.width = 700.2;
    },
    (sample) => {
      sample.captureStartedAtMs = Infinity;
    },
    (sample) => {
      delete sample.captureStartedAtMs;
    },
    (sample) => {
      sample.strokeDocument.schema_version = 2;
    },
    (sample) => {
      sample.strokeDocument.private_extra = "unvalidated source";
    },
    (sample) => {
      sample.strokeDocument.strokes.push(sample.strokeDocument.strokes[0]);
      sample.strokes.push(sample.strokes[0]);
    },
    (sample) => {
      sample.strokeDocument.operations = [
        {
          type: "translate",
          stroke_ids: ["stable-capture-id"],
          dx: 0.1,
          dy: 0,
        },
      ];
    },
    (sample) => {
      sample.originalCaptureId = "image-source-in-drawing";
    },
  ];
  for (const mutate of mutations) {
    const malformed = structuredClone(valid);
    mutate(malformed);
    await assert.rejects(
      projectService.update(project.id, { sample: malformed }),
      { code: "invalid-project" },
    );
    assert.equal(local.getItem(key), bytes);
  }
});

test("corrupt persisted capture metadata follows byte-preserving draft recovery", async () => {
  const project = await projectService.create("Recoverable draft");
  const badSample = observedSample();
  badSample.strokeDocument.strokes[0].points[0].x = 0.9;
  const badProject = { ...project, id: "corrupt-capture", sample: badSample };
  const raw = JSON.stringify({
    schemaVersion: 1,
    projects: [project, badProject],
  });
  local.setItem(key, raw);
  await assert.rejects(projectService.list(), { code: "invalid-data" });
  assert.equal(local.getItem(key), raw);
  const info = await projectService.storageInfo();
  assert.equal(info.invalidProjects, 1);
  assert.equal(info.recoverableProjects, 1);
  await projectService.recoverLocalData();
  assert.equal(local.getItem(recoveryKey), raw);
  assert.deepEqual(
    (await projectService.list()).map((item) => item.id),
    [project.id],
  );
});

test("duplicating an uploaded draft retains its preview but requires explicit original reselection", async () => {
  const source = await projectService.create("Original image draft");
  const sample = {
    kind: "upload",
    dataUrl: "data:image/jpeg;base64,YWJj",
    fileName: "Original.png",
    originalCaptureId: "source-vault-original",
  };
  await projectService.update(source.id, { sample });
  const copy = await projectService.duplicate(source.id);
  assert.deepEqual(copy.sample, {
    kind: "upload",
    dataUrl: sample.dataUrl,
    fileName: sample.fileName,
  });
  assert.deepEqual((await projectService.get(source.id)).sample, sample);
  assert.equal(
    copy.sample.originalCaptureId,
    undefined,
    "a copied draft cannot refer to another draft's scoped original vault record",
  );
  assert.notEqual(source.id, copy.id);
});
