import { Routes, Route } from 'react-router'
import MaterialCenterPage from './pages/MaterialCenterPage'
import DesignPackageUploadPage from './pages/DesignPackageUploadPage'
import DistributionDetailPage from './pages/DistributionDetailPage'
import DistributionListPage from './pages/DistributionListPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<MaterialCenterPage />} />
      <Route path="/materials" element={<MaterialCenterPage />} />
      <Route path="/materials/upload" element={<DesignPackageUploadPage />} />
      <Route path="/materials/distributions" element={<DistributionListPage />} />
      <Route path="/materials/distributions/:taskId" element={<DistributionDetailPage />} />
      <Route path="*" element={<MaterialCenterPage />} />
    </Routes>
  )
}
