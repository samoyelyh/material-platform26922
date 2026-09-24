import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { usersApi, type UserDto } from '@/services/apiClient';
import { setLoggedOut, useAuth } from '@/store/authStore';

const ROLE_LABEL: Record<string, string> = {
  ADMIN: '管理员',
  DESIGN_MANAGER: '美工组长',
  DESIGNER: '美工',
  OPERATOR: '运营',
};

const ROLE_TONE: Record<string, string> = {
  ADMIN: 'bg-[#3d3192] text-white',
  DESIGN_MANAGER: 'bg-[#f0eef9] text-[#3d3192]',
  DESIGNER: 'bg-gray-100 text-gray-600',
  OPERATOR: 'bg-emerald-50 text-emerald-600',
};

function formatTime(value?: string | null): string {
  if (!value) return '—';
  return value.replace('T', ' ').slice(0, 16);
}

/** 用户管理（仅 ADMIN）：列表 / 新增 / 禁用启用 / 改角色 / 重置密码。不做组织架构。 */
export default function UserManagePage() {
  const navigate = useNavigate();
  const { user: current } = useAuth();
  const [users, setUsers] = useState<UserDto[]>([]);
  const [loading, setLoading] = useState(true);

  // 新增表单
  const [showCreate, setShowCreate] = useState(false);
  const [username, setUsername] = useState('');
  const [displayName, setDisplayName] = useState('');
  const [role, setRole] = useState<UserDto['role']>('OPERATOR');
  const [password, setPassword] = useState('');
  const [creating, setCreating] = useState(false);

  const load = useCallback(() => {
    setLoading(true);
    void usersApi
      .listUsers()
      .then(setUsers)
      .catch(() => setUsers([]))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    load();
  }, [load]);

  const handleCreate = async () => {
    if (!username.trim() || !displayName.trim() || password.length < 6 || creating) return;
    setCreating(true);
    try {
      await usersApi.createUser({
        username: username.trim(),
        displayName: displayName.trim(),
        role,
        password,
      });
      toast.success(`已创建用户 ${username.trim()}`);
      setShowCreate(false);
      setUsername('');
      setDisplayName('');
      setPassword('');
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '创建失败');
    } finally {
      setCreating(false);
    }
  };

  const toggleActive = async (target: UserDto) => {
    try {
      await usersApi.patchUser(target.id, { isActive: !target.isActive });
      toast.success(`${target.displayName} ${target.isActive ? '已禁用' : '已启用'}`);
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '操作失败');
    }
  };

  const changeRole = async (target: UserDto, nextRole: UserDto['role']) => {
    try {
      await usersApi.patchUser(target.id, { role: nextRole });
      toast.success(`${target.displayName} 角色已改为 ${ROLE_LABEL[nextRole]}`);
      load();
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '操作失败');
    }
  };

  const resetPassword = async (target: UserDto) => {
    const next = window.prompt(`为 ${target.displayName} 设置新密码（至少 6 位）`, '');
    if (!next || next.length < 6) return;
    try {
      await usersApi.patchUser(target.id, { password: next });
      toast.success(`${target.displayName} 密码已重置`);
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '重置失败');
    }
  };

  const handleLogout = () => {
    setLoggedOut();
    navigate('/login', { replace: true });
  };

  return (
    <div className="flex min-h-screen flex-col bg-[#f5f6f8] text-gray-800">
      <header className="flex h-12 shrink-0 items-center justify-between border-b bg-white px-6 text-xs">
        <div className="flex items-center gap-1.5 text-gray-400">
          <span className="font-medium text-gray-600">素材中心</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span>用户管理</span>
        </div>
        <div className="flex items-center gap-2">
          <Button size="sm" variant="outline" onClick={() => navigate('/materials')}>
            素材中心
          </Button>
          <Button size="sm" variant="outline" onClick={handleLogout}>
            退出登录
          </Button>
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1100px] space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">用户管理</h1>
            <p className="mt-1 text-xs text-gray-400">
              内部账号：新增 / 禁用启用 / 改角色 / 重置密码。离职用户禁用（不物理删除，保留历史关联）。
            </p>
          </div>
          <Button className="bg-[#3d3192] hover:bg-[#32277a]" onClick={() => setShowCreate(true)}>
            新增用户
          </Button>
        </div>

        {showCreate && (
          <Card className="space-y-3 p-4">
            <h2 className="text-sm font-semibold text-gray-800">新增用户</h2>
            <div className="grid gap-3 md:grid-cols-4">
              <input
                placeholder="账号（唯一）"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                className="rounded border border-gray-300 px-2.5 py-1.5 text-sm"
              />
              <input
                placeholder="显示姓名"
                value={displayName}
                onChange={(e) => setDisplayName(e.target.value)}
                className="rounded border border-gray-300 px-2.5 py-1.5 text-sm"
              />
              <select
                value={role}
                onChange={(e) => setRole(e.target.value as UserDto['role'])}
                className="rounded border border-gray-300 px-2.5 py-1.5 text-sm"
              >
                <option value="OPERATOR">运营</option>
                <option value="DESIGNER">美工</option>
                <option value="DESIGN_MANAGER">美工组长</option>
                <option value="ADMIN">管理员</option>
              </select>
              <input
                type="password"
                placeholder="密码（至少 6 位）"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                className="rounded border border-gray-300 px-2.5 py-1.5 text-sm"
              />
            </div>
            <div className="flex justify-end gap-2">
              <Button variant="outline" onClick={() => setShowCreate(false)}>
                取消
              </Button>
              <Button
                className="bg-[#3d3192] hover:bg-[#32277a]"
                disabled={!username.trim() || !displayName.trim() || password.length < 6}
                onClick={() => void handleCreate()}
              >
                {creating ? '创建中…' : '创建'}
              </Button>
            </div>
          </Card>
        )}

        {loading ? (
          <Card className="py-16 text-center text-sm text-gray-400">正在加载…</Card>
        ) : (
          <Card>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="px-4 py-2.5 font-normal">账号</th>
                  <th className="px-4 py-2.5 font-normal">姓名</th>
                  <th className="px-4 py-2.5 font-normal">角色</th>
                  <th className="px-4 py-2.5 font-normal">状态</th>
                  <th className="px-4 py-2.5 font-normal">最后登录</th>
                  <th className="px-4 py-2.5 text-right font-normal">操作</th>
                </tr>
              </thead>
              <tbody>
                {users.map((u) => (
                  <tr key={u.id} className="border-b border-gray-50 text-gray-700 last:border-0">
                    <td className="px-4 py-3 font-mono">{u.username}</td>
                    <td className="px-4 py-3">
                      {u.displayName}
                      {u.id === current?.id && <span className="ml-1 text-[10px] text-gray-400">（我）</span>}
                    </td>
                    <td className="px-4 py-3">
                      <span className={`rounded px-1.5 py-0.5 text-[11px] ${ROLE_TONE[u.role]}`}>
                        {ROLE_LABEL[u.role] ?? u.role}
                      </span>
                    </td>
                    <td className="px-4 py-3">
                      {u.isActive ? (
                        <span className="text-emerald-600">启用</span>
                      ) : (
                        <span className="text-red-500">已禁用</span>
                      )}
                    </td>
                    <td className="px-4 py-3 text-gray-500">{formatTime(u.lastLoginAt)}</td>
                    <td className="px-4 py-3 text-right">
                      <select
                        value={u.role}
                        onChange={(e) => void changeRole(u, e.target.value as UserDto['role'])}
                        className="mr-2 rounded border border-gray-200 px-1.5 py-0.5 text-[11px]"
                      >
                        <option value="OPERATOR">运营</option>
                        <option value="DESIGNER">美工</option>
                        <option value="DESIGN_MANAGER">美工组长</option>
                        <option value="ADMIN">管理员</option>
                      </select>
                      <button
                        onClick={() => void toggleActive(u)}
                        className="mr-2 text-[#3d3192] hover:underline"
                      >
                        {u.isActive ? '禁用' : '启用'}
                      </button>
                      <button onClick={() => void resetPassword(u)} className="text-[#3d3192] hover:underline">
                        重置密码
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </Card>
        )}
      </main>
    </div>
  );
}
