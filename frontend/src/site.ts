// The public website build (`npm run build:public`, i.e. `vite build --mode public`; deployed on Vercel):
// only the marketing site at "/", with no application route, no backend request and no credential
// handling. This is not access control: the application and its API are simply not part of that
// build, and the backend is never deployed with it. Every other build is the full application.
export const PUBLIC_SITE = import.meta.env.MODE === 'public'
