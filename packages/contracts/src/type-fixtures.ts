/** Compile-only proofs for the current explicit frontend boundary. */
import type { FontProject, ProcessingJob } from "../../../src/lib/contracts";
import type {
  CloudJobView,
  CloudProjectView,
} from "../../../src/lib/api-boundary";
import type { paths } from "./generated";

type Assert<Condition extends true> = Condition;
export type CloudIsSeparateFromDemo = Assert<
  CloudProjectView extends FontProject ? false : true
>;
export type CloudCancellationCannotEnterDemoJob = Assert<
  CloudJobView extends ProcessingJob ? false : true
>;
export type HealthIsImplemented = Assert<
  "/api/v1/health" extends keyof paths ? true : false
>;
export type RegistryIsImplemented = Assert<
  "/api/v1/contracts" extends keyof paths ? true : false
>;
