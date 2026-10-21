import { Routes, Route } from 'react-router-dom'
import { Layout } from './components/Layout'
import { Dashboard } from './pages/Dashboard'
import { AssetList } from './pages/AssetList'
import { AssetDetail } from './pages/AssetDetail'
import { ExtractionDetail } from './pages/ExtractionDetail'
import { PassportPreview } from './pages/PassportPreview'
import { ApiDocs } from './pages/ApiDocs'
import { NotFound } from './pages/NotFound'
import { Landing } from './pages/Landing'

export function App() {
  return (
    <Routes>
      {/* The public website; the application starts at /dashboard */}
      <Route path="/" element={<Landing />} />
      <Route element={<Layout />}>
        <Route path="dashboard" element={<Dashboard />} />
        <Route path="assets" element={<AssetList />} />
        <Route path="assets/:id" element={<AssetDetail />} />
        <Route path="assets/:id/documents/:docId/extractions/:extractionId" element={<ExtractionDetail />} />
        <Route path="passport" element={<PassportPreview />} />
        <Route path="api-docs" element={<ApiDocs />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  )
}
