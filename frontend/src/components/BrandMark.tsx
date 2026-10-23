import mark from '../assets/brand/rwa-legal-passport-mark.png'

// The RWA Legal Passport mark: the emblem of the official logo (assets/brand/rwa-legal-passport-logo.jpg),
// cropped square on its own navy field and otherwise unaltered. Used by the public website, the demo and
// the application shell, always beside the name in text, which stays readable at this size.
export function BrandMark({ className = 'h-7 w-7' }: { className?: string }) {
  return <img src={mark} alt="" width={28} height={28} draggable={false} className={`${className} rounded-md`} />
}
