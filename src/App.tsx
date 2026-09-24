import { useEffect } from 'react'
import { Routes, Route, Navigate, useLocation } from 'react-router'
import LoginPage from './pages/LoginPage'
import MaterialCenterPage from './pages/MaterialCenterPage'
import DesignPackageUploadPage from './pages/DesignPackageUploadPage'
import DistributionDetailPage from './pages/DistributionDetailPage'
import DistributionListPage from './pages/DistributionListPage'
import MyTasksPage from './pages/MyTasksPage'
import UserManagePage from './pages/UserManagePage'
import { canManageMaterials, canManageUsers, canViewAllTasks, restoreAuth, useAuth } from '@/store/authStore'

/** 认证 + 角色守卫：未登录跳 /login；无权角色回默认首页 */
function RequireAuth({ roles, children }: { roles?: (user: import('@/services/apiClient').UserDto | null) => boolean; children: React.ReactNode }) {
  const { user, loading } = useAuth()
  const location = useLocation()

  useEffect(() => {
    void restoreAuth()
  }, [])

  if (loading) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[#f5f6f8] text-sm text-gray-400">
        正在恢复登录态…
      </div>
    )
  }
  if (!user) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />
  }
  if (roles && !roles(user)) {
    return <Navigate to={user.role === 'OPERATOR' ? '/materials/distributions/my' : '/materials'} replace />
  }
  return <>{children}</>
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MaterialCenterPage />
          </RequireAuth>
        }
      />
      <Route
        path="/materials"
        element={
          <RequireAuth roles={canManageMaterials}>
            <MaterialCenterPage />
          </RequireAuth>
        }
      />
      <Route
        path="/materials/upload"
        element={
          <RequireAuth roles={canManageMaterials}>
            <DesignPackageUploadPage />
          </RequireAuth>
        }
      />
      {/* 全部派发任务列表（ADMIN / DESIGN_MANAGER） */}
      <Route
        path="/materials/distributions"
        element={
          <RequireAuth roles={canViewAllTasks}>
            <DistributionListPage />
          </RequireAuth>
        }
      />
      {/* 我的任务（OPERATOR 默认首页；管理角色看全部） */}
      <Route
        path="/materials/distributions/my"
        element={
          <RequireAuth>
            <MyTasksPage />
          </RequireAuth>
        }
      />
      {/* 任务详情：后端按用户过滤（运营只能看自己的） */}
      <Route
        path="/materials/distributions/:taskId"
        element={
          <RequireAuth>
            <DistributionDetailPage />
          </RequireAuth>
        }
      />
      {/* 用户管理（ADMIN） */}
      <Route
        path="/admin/users"
        element={
          <RequireAuth roles={canManageUsers}>
            <UserManagePage />
          </RequireAuth>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
