// ============================================================================
// 登录态 store（V1.1）
//
//   - token 存 localStorage（authSession）
//   - /auth/me 恢复当前用户（页面刷新后保持登录）
//   - 401 时 apiClient 自动清 token 并跳 /login
//
// 角色常量与权限判断也集中在这里，页面只问 can(user, action)。
// ============================================================================

import { useSyncExternalStore } from 'react'
import { authApi, authSession, type UserDto } from '@/services/apiClient'

export type Role = 'ADMIN' | 'DESIGN_MANAGER' | 'DESIGNER' | 'OPERATOR'

export interface AuthState {
  user: UserDto | null
  loading: boolean
}

let state: AuthState = { user: null, loading: true }
const listeners = new Set<() => void>()

function commit(next: Partial<AuthState>) {
  state = { ...state, ...next }
  listeners.forEach((fn) => fn())
}

function subscribe(fn: () => void) {
  listeners.add(fn)
  return () => listeners.delete(fn)
}

/** React hook：订阅登录态 */
export function useAuth(): AuthState {
  return useSyncExternalStore(subscribe, () => state)
}

/** 供非组件代码读取（不带订阅） */
export function getAuthState(): AuthState {
  return state
}

/** 登录成功后调用 */
export function setLoggedIn(user: UserDto, token: string) {
  authSession.setToken(token)
  commit({ user, loading: false })
}

/** 登出：清 token + 清用户（页面跳转由调用方决定） */
export function setLoggedOut() {
  authSession.clear()
  commit({ user: null, loading: false })
}

/** 页面加载时恢复登录态：有 token 才调 /auth/me；失败（401 已由 apiClient 处理）静默置空 */
export async function restoreAuth(): Promise<void> {
  if (!authSession.getToken()) {
    commit({ user: null, loading: false })
    return
  }
  try {
    const user = await authApi.me()
    commit({ user, loading: false })
  } catch {
    // 401 已由 apiClient 统一处理（清 token + 跳登录）；这里只把 loading 关掉
    commit({ user: null, loading: false })
  }
}

// ---------------------------------------------------------------- 角色权限

export function isAdmin(user: UserDto | null): boolean {
  return user?.role === 'ADMIN'
}

export function isManager(user: UserDto | null): boolean {
  return user?.role === 'ADMIN' || user?.role === 'DESIGN_MANAGER'
}

/** 能看到素材中心管理入口（上传 / 素材 / 派发列表） */
export function canManageMaterials(user: UserDto | null): boolean {
  return (
    user?.role === 'ADMIN' || user?.role === 'DESIGN_MANAGER' || user?.role === 'DESIGNER'
  )
}

/** 能创建 / 取消派发 */
export function canDispatch(user: UserDto | null): boolean {
  return isManager(user)
}

/** 能看全部派发任务列表 */
export function canViewAllTasks(user: UserDto | null): boolean {
  return isManager(user)
}

/** 能管理用户 */
export function canManageUsers(user: UserDto | null): boolean {
  return isAdmin(user)
}

/** 登录后默认首页：运营 → 我的任务；其它 → 素材中心 */
export function homePathFor(user: UserDto | null): string {
  return user?.role === 'OPERATOR' ? '/materials/distributions/my' : '/materials'
}
