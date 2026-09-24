import { useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { authApi } from '@/services/apiClient'
import { homePathFor, setLoggedIn } from '@/store/authStore'

/** 登录页（V1.1 内部账号：账号 + 密码，不做注册/找回/验证码/SSO） */
export default function LoginPage() {
  const navigate = useNavigate()
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [submitting, setSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!username.trim() || !password || submitting) return
    setSubmitting(true)
    try {
      const { token, user } = await authApi.login(username.trim(), password)
      setLoggedIn(user, token)
      toast.success(`欢迎，${user.displayName}`)
      navigate(homePathFor(user), { replace: true })
    } catch (error) {
      const message = error instanceof Error ? error.message : '登录失败'
      toast.error(message)
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[#f5f6f8] px-4">
      <form
        onSubmit={handleSubmit}
        className="w-full max-w-sm rounded-lg border border-gray-100 bg-white p-6 shadow-sm"
      >
        <h1 className="text-lg font-semibold text-gray-900">素材中心</h1>
        <p className="mt-1 text-xs text-gray-400">内部账号登录</p>

        <label className="mt-5 block text-xs text-gray-600">
          账号
          <input
            autoFocus
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mt-1 w-full rounded border border-gray-300 px-2.5 py-1.5 text-sm outline-none focus:border-[#3d3192]"
            placeholder="请输入账号"
            autoComplete="username"
          />
        </label>

        <label className="mt-3 block text-xs text-gray-600">
          密码
          <input
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1 w-full rounded border border-gray-300 px-2.5 py-1.5 text-sm outline-none focus:border-[#3d3192]"
            placeholder="请输入密码"
            autoComplete="current-password"
          />
        </label>

        <Button
          type="submit"
          disabled={submitting || !username.trim() || !password}
          className="mt-5 w-full bg-[#3d3192] hover:bg-[#32277a]"
        >
          {submitting ? '登录中…' : '登录'}
        </Button>

        <p className="mt-3 text-center text-[11px] text-gray-400">
          账号由管理员分配；忘记密码请联系管理员重置
        </p>
      </form>
    </div>
  )
}
