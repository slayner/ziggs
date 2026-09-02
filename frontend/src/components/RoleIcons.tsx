type Props = { size?: number; className?: string };

export function ShieldIcon({ size, className }: Props) {
  return (
    <svg viewBox="0 0 16 16" width={size ?? 16} height={size ?? 16}
      className={className} fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <path d="M8 1.5L13.5 4V9C13.5 12.2 11 14.2 8 15C5 14.2 2.5 12.2 2.5 9V4L8 1.5Z" />
    </svg>
  );
}

export function HealerIcon({ size, className }: Props) {
  return (
    <svg viewBox="0 0 16 16" width={size ?? 16} height={size ?? 16}
      className={className} fill="none" stroke="currentColor"
      strokeWidth="2" strokeLinecap="round">
      <path d="M8 3V13M3 8H13" />
    </svg>
  );
}

export function FlagIcon({ size, className }: Props) {
  return (
    <svg viewBox="0 0 16 16" width={size ?? 16} height={size ?? 16}
      className={className} fill="none" stroke="currentColor"
      strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round">
      <line x1="3.5" y1="1.5" x2="3.5" y2="14.5" />
      <path d="M3.5 3H13L9.5 6.5L13 10H3.5Z" fill="currentColor" stroke="none" />
    </svg>
  );
}

export function DpsIcon({ size, className }: Props) {
  return (
    <svg viewBox="0 0 32 32" width={size ?? 16} height={size ?? 16}
      className={className} fill="currentColor" stroke="none"
      xmlns="http://www.w3.org/2000/svg">
      <path d="M29.574 2.608c-3.002 6.689-7.804 6.471-12.624 4.056l1.179-1.69c-0.774-1.171-2.183-2.020-3.485-2.401l-1.313 1.923c-1.098-0.75-2.172-1.555-3.201-2.356l-0 0c-0.124 0.189-0.239 0.371-0.346 0.548-2.12-1.198-4.921-1.731-8.070-1.731 3.119 1.26 5.566 2.932 6.661 5.777-0.063 0.865-0.010 1.739-0.010 2.807 0.371 0.205 0.731 0.421 1.080 0.645l-8.161 11.95 0.050 6.904 11.299-16.19c3.362 3.722 3.784 8.533-2.155 12.281 3.241 0.943 6.263 0.992 8.965 0.364l0.547-3.462 2.313 2.41c1.916-1.098 4.74-3.122 5.994-4.926l-2.003-3.461 3.425 0.947c2.102-3.231 2.327-10.235-0.146-14.394z" />
    </svg>
  );
}

export function PierceIcon({ size, className }: Props) {
  return (
    <svg viewBox="0 0 512 512" width={size ?? 16} height={size ?? 16}
      className={className} fill="currentColor" stroke="none"
      xmlns="http://www.w3.org/2000/svg">
      <path d="M193.7 19.2l40.1 107.9-7.1-107.9h-33zm99.3 0l2.3 100.4 22.6-100.4H293zm159.1 0L306.3 170.5l36.2 11.4L492.6 26.15V19.2h-40.5zm40.5 111c-58.4 39.6-125.8 86.4-125.8 86.4l125.8-15.9v-70.5zm-372.8 3c-19.8 51.9-56.72 98-101.44 141.3C61.35 342.9 110.5 402 156.6 440.7c23.7 19.9 46.6 34.4 66.9 42.1 20.3 7.8 37.6 8.7 51.5 3.1 10.1-4.1 17.7-16.3 22.1-37.1 4.5-20.8 5.3-48.9 3.7-80.3-1.7-33-6.1-69.5-11.4-105.8l-16.4 17 .4 20.4-83.6 39.8 6 24.3 53.1 8.4-2.8 17.8-45.7-7.2 5.5 22.5-17.4 4.2-13.2-54-52.9 10.4-3.4-17.6 35.6-7 6.1-13.9-44-31.8-40.59-20 7.96-16.2 30.53 15 7.1-30.3 17.6 4.2-8.5 35.3 37.2 26.8 34.3-79.3 20.5-.4 52.9-54.9-.3-2.1c-45.9-7.9-99.5-15.4-155.6-40.9zM292.3 185l-13 13.5 36.2 11.4 13-13.5-36.2-11.4zm-27 28l-17 17.7 14.8-.3 9-.2.4 24.2 29-30-36.2-11.4zm227.3 27.6c-65.3 10.2-149.5 23.7-149.5 23.7l149.5 20.9v-44.6zm-238.1 8l-40.2.7-32.2 74.4 73.1-34.8-.7-40.3z" />
    </svg>
  );
}

export function BattlemountIcon({ size, className }: Props) {
  return (
    <svg viewBox="0 0 512 512" width={size ?? 16} height={size ?? 16}
      className={className} fill="currentColor" stroke="none"
      xmlns="http://www.w3.org/2000/svg">
      <path d="M509.8 332.5l-69.9-164.3c-14.9-41.2-50.4-71-93-79.2 18-10.6 46.3-35.9 34.2-82.3-1.3-5-7.1-7.9-12-6.1L166.9 76.3C35.9 123.4 0 238.9 0 398.8V480c0 17.7 14.3 32 32 32h236.2c23.8 0 39.3-25 28.6-46.3L256 384v-.7c-45.6-3.5-84.6-30.7-104.3-69.6-1.6-3.1-.9-6.9 1.6-9.3l12.1-12.1c3.9-3.9 10.6-2.7 12.9 2.4 14.8 33.7 48.2 57.4 87.4 57.4 17.2 0 33-5.1 46.8-13.2l46 63.9c6 8.4 15.7 13.3 26 13.3h50.3c8.5 0 16.6-3.4 22.6-9.4l45.3-39.8c8.9-9.1 11.7-22.6 7.1-34.4zM328 224c-13.3 0-24-10.7-24-24s10.7-24 24-24 24 10.7 24 24-10.7 24-24 24z" />
    </svg>
  );
}

const ROLE_ICONS: Record<string, (props: Props) => React.ReactNode> = {
  tank: ShieldIcon,
  healer: HealerIcon,
  support: FlagIcon,
  dps: DpsIcon,
  pierce: PierceIcon,
  battlemount: BattlemountIcon,
};

export function RoleIcon({ role, size, className }: Props & { role: string }) {
  const Icon = ROLE_ICONS[role];
  if (!Icon) return null;
  return <>{Icon({ size, className })}</>;
}
