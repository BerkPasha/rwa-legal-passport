import { Navigate, Route, Routes } from 'react-router-dom'
import { Landing } from './pages/Landing'

// The public website build (site.ts): the landing page only. Any other address leads back to it.
export function PublicSite() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
