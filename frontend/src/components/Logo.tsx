/**
 * FibTrader View mark (F·V), geometric mark: block "F" with two bars, a coral dot in the empty
 * lower corner of the F, a slanted bar for "V". Letters use currentColor, the dot uses the brand colour.
 */
export function Logo({ className }: { className?: string }) {
  return (
    <svg viewBox="0 0 64 64" className={className} aria-hidden="true" fill="currentColor">
      <rect x="2" y="10" width="12" height="44" rx="1.5" />
      <rect x="2" y="10" width="28" height="12" rx="1.5" />
      <rect x="2" y="28" width="21" height="10" rx="1.5" />
      <circle cx="23" cy="47.5" r="6.5" fill="var(--brand)" />
      <path d="M44 10 H60 L46 54 H30 Z" />
    </svg>
  )
}
