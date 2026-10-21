// The RWA Legal Passport mark: a document with a committed record (the teal node). Used by the public
// website and the application shell.
export function BrandMark({ className = 'h-7 w-7' }: { className?: string }) {
  return (
    <svg viewBox="0 0 28 28" className={className} aria-hidden fill="none">
      <path d="M5.5 3.5h11l6 6v15h-17z" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
      <path d="M16.5 3.5v6h6" stroke="currentColor" strokeWidth={1.5} strokeLinejoin="round" />
      <path d="M9 13.5h6.5M9 17h4" stroke="currentColor" strokeWidth={1.5} strokeLinecap="round" />
      <circle cx={19.5} cy={21} r={3.25} fill="#168A82" />
    </svg>
  )
}
