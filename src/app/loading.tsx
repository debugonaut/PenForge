import { LoadingScene } from "@/components/shared/LoadingScene";

/** Any route that does not define its own loading state falls back to this. */
export default function Loading() {
  return <LoadingScene message="Turning the page…" />;
}
