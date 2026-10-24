import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement>;

function Icon({ children, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...props}
    >
      {children}
    </svg>
  );
}

export function UploadIcon({ className, ...props }: IconProps) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="currentColor"
      aria-hidden="true"
      className={className}
      {...props}
    >
      <path d="M12 2.8a1 1 0 0 1 .7.29l5.2 5.2a1 1 0 0 1-1.41 1.42l-3.49-3.5v9.03a1 1 0 1 1-2 0V6.21L7.5 9.71A1 1 0 0 1 6.1 8.29l5.2-5.2a1 1 0 0 1 .7-.29Z" />
      <rect x="3.4" y="17.2" width="17.2" height="3.4" rx="1.7" />
    </svg>
  );
}

export function DownloadIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4v12" />
      <path d="m7.5 11.5 4.5 4.5 4.5-4.5" />
      <path d="M5 19.5h14" />
    </Icon>
  );
}

export function PenIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m14.5 4.5 5 5L9 20H4v-5L14.5 4.5Z" />
      <path d="m12.5 6.5 5 5" />
      <path d="m4 20 4.2-1.2" />
    </Icon>
  );
}

export function CheckGridIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="3" width="7.5" height="7.5" rx="1.2" />
      <rect x="13.5" y="3" width="7.5" height="7.5" rx="1.2" />
      <rect x="3" y="13.5" width="7.5" height="7.5" rx="1.2" />
      <path d="m13.5 17.2 2.2 2.2 4.3-4.8" />
    </Icon>
  );
}

export function SlidersIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M4 7h8" />
      <path d="M16 7h4" />
      <path d="M4 17h3" />
      <path d="M11 17h9" />
      <circle cx="14" cy="7" r="2" />
      <circle cx="9" cy="17" r="2" />
      <path d="M4 12h16" opacity=".4" />
    </Icon>
  );
}

export function SparklesIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m12 3.5 1.8 4.9 4.9 1.8-4.9 1.8L12 16.9l-1.8-4.9L5.3 10.2l4.9-1.8L12 3.5Z" />
      <path d="m18.3 15.5.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7.7-2Z" />
      <path d="m5.2 16.3.5 1.4 1.4.5-1.4.5-.5 1.4-.5-1.4-1.4-.5 1.4-.5.5-1.4Z" />
    </Icon>
  );
}

export function EyeIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M2.8 12S6.2 6.5 12 6.5 21.2 12 21.2 12 17.8 17.5 12 17.5 2.8 12 2.8 12Z" />
      <circle cx="12" cy="12" r="2.8" />
    </Icon>
  );
}

export function CheckIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m4.5 12.5 5 5 10-11" />
    </Icon>
  );
}

export function AlertIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M12 4.5 21 20H3L12 4.5Z" />
      <path d="M12 10v4.5" />
      <path d="M12 17.5h.01" />
    </Icon>
  );
}

export function LetterIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="5" y="3" width="14" height="18" rx="1.5" />
      <path d="M8 7.5h8" />
      <path d="M8 11h5" />
      <path d="M8 16.5h8" />
    </Icon>
  );
}

export function JournalIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M7 3h10.5A1.5 1.5 0 0 1 19 4.5v15a1.5 1.5 0 0 1-1.5 1.5H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z" />
      <path d="M7 3v18" />
      <path d="M10 8h5" />
      <path d="M10 12h4" />
    </Icon>
  );
}

export function CardIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="3" y="6" width="18" height="12" rx="2" />
      <path d="M3 10h18" />
      <path d="M7 14.5h4" />
    </Icon>
  );
}

export function FileIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3h8l4 4v14H6V3Z" />
      <path d="M14 3v5h4" />
      <path d="M9 13h6" />
      <path d="M9 16.5h4" />
    </Icon>
  );
}

export function UndoIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m8 4-5 5 5 5" />
      <path d="M3 9h11a6 6 0 0 1 0 12h-3" />
    </Icon>
  );
}

export function RedoIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m16 4 5 5-5 5" />
      <path d="M21 9H10a6 6 0 0 0 0 12h3" />
    </Icon>
  );
}

export function EraserIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="m14 3 7 7-10 10H6l-4-4L14 3Z" />
      <path d="m8 9 7 7M11 20h10" />
    </Icon>
  );
}

export function TrashIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 6h18M9 6V3h6v3M6 6l1 15h10l1-15M10 10v7M14 10v7" />
    </Icon>
  );
}

export function RotateLeftIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 4v6h6M3 10a9 9 0 1 1 1 7" />
    </Icon>
  );
}

export function RotateRightIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M21 4v6h-6M21 10a9 9 0 1 0-1 7" />
    </Icon>
  );
}

export function ResetIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M3 4v6h6M3 10a9 9 0 1 1 1 7M12 7v5l3 2" />
    </Icon>
  );
}

export function ArrowUpRightIcon(props: IconProps) {
  const { className = "", ...rest } = props;
  return (
    <svg
      className={`wizard-icon ${className}`.trim()}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d="M7 17 17 7" />
      <path d="M8 7h9v9" />
    </svg>
  );
}

export function PencilIcon(props: IconProps) {
  const { className = "", ...rest } = props;
  return (
    <svg
      className={`wizard-icon ${className}`.trim()}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d="m4 20 4.2-1 10.3-10.3a2.1 2.1 0 0 0-3-3L5.2 16 4 20Z" />
      <path d="m13.8 6.2 3 3" />
    </svg>
  );
}

export function CropIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <path d="M6 3v15h15M3 6h15v15M21 3 3 21" />
    </Icon>
  );
}

export function MissingIcon(props: IconProps) {
  return (
    <Icon {...props}>
      <rect x="4" y="4" width="16" height="16" rx="3" strokeDasharray="3 3" />
      <path d="M8 12h8" />
    </Icon>
  );
}
