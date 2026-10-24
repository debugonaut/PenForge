import styles from "./TypographyEndpoints.module.css";

export type TypographyControlKind =
  "weight" | "spacing" | "slant" | "size" | "lineHeight";

export default function TypographyEndpoints({
  kind,
  className = "",
}: {
  kind: TypographyControlKind;
  className?: string;
}) {
  return (
    <span
      className={`${styles.endpoints} ${styles[kind]} ${className}`}
      aria-hidden="true"
    >
      <span>
        {kind === "lineHeight" ? (
          <>
            a little
            <br />
            more you
          </>
        ) : kind === "spacing" ? (
          "ab"
        ) : (
          "Aa"
        )}
      </span>
      <span>
        {kind === "lineHeight" ? (
          <>
            a little
            <br />
            more you
          </>
        ) : kind === "spacing" ? (
          "ab"
        ) : (
          "Aa"
        )}
      </span>
    </span>
  );
}
