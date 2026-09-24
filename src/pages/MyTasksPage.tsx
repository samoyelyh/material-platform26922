import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight, Inbox } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { StatusBadge } from '@/components/material/WorkflowPrimitives';
import { formatDateTime } from '@/lib/workflow';
import { materialApi, type DistributionTaskDto } from '@/services/apiClient';
import { canViewAllTasks, useAuth } from '@/store/authStore';

const STATUS_FILTERS: { key: string; label: string; value?: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'ACTIVE', label: '待接收', value: 'ACTIVE' },
  { key: 'RECEIVED', label: '已接收', value: 'RECEIVED' },
  { key: 'COMPLETED', label: '已完成', value: 'COMPLETED' },
  { key: 'CANCELLED', label: '已取消', value: 'CANCELLED' },
];

const STATUS_LABEL: Record<string, string> = {
  ACTIVE: '待接收',
  RECEIVED: '已接收',
  COMPLETED: '已回填ASIN',
  CANCELLED: '已取消',
};

const STATUS_TONE: Record<string, 'green' | 'purple' | 'neutral' | 'amber'> = {
  ACTIVE: 'amber',
  RECEIVED: 'purple',
  COMPLETED: 'green',
  CANCELLED: 'neutral',
};

/** 我的派发任务（OPERATOR 默认首页；ADMIN/DESIGN_MANAGER 看全部）。后端按当前用户过滤。 */
export default function MyTasksPage() {
  const navigate = useNavigate();
  const { user } = useAuth();
  const [status, setStatus] = useState('all');
  const [tasks, setTasks] = useState<DistributionTaskDto[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    setLoading(true);
    // 后端过滤：OPERATOR 只返回自己的任务（/distributions/my）；管理角色返回全部
    void materialApi
      .listMyDistributions(status === 'all' ? undefined : status)
      .then(setTasks)
      .catch(() => setTasks([]))
      .finally(() => setLoading(false));
  }, [status]);

  useEffect(() => {
    load();
  }, [load]);

  return (
    <div className="flex h-screen flex-col bg-[#f5f6f8] text-gray-800">
      <header className="flex h-12 shrink-0 items-center justify-between border-b bg-white px-6 text-xs">
        <div className="flex items-center gap-1.5 text-gray-400">
          <span className="font-medium text-gray-600">素材中心</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span>我的派发任务</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-gray-500">{user?.displayName ?? ''}</span>
          {canViewAllTasks(user) && (
            <Button size="sm" variant="outline" onClick={() => navigate('/materials')}>
              素材中心
            </Button>
          )}
        </div>
      </header>

      <main className="mx-auto w-full max-w-[1400px] space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">我的任务</h1>
            <p className="mt-1 text-xs text-gray-400">
              点击任意任务进入详情（接收 / 查看副素材 / 回填 ASIN / 下载素材包）。
            </p>
          </div>
          <button onClick={load} className="text-xs text-[#3d3192] hover:underline">
            刷新
          </button>
        </div>

        <div className="flex items-center gap-2">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              onClick={() => setStatus(f.key)}
              className={`rounded-full px-3 py-1 text-xs ${
                status === f.key
                  ? 'bg-[#3d3192] text-white'
                  : 'border border-gray-200 bg-white text-gray-500 hover:border-[#3d3192]/40'
              }`}
            >
              {f.label}
            </button>
          ))}
        </div>

        {loading ? (
          <Card className="py-16 text-center text-sm text-gray-400">正在加载…</Card>
        ) : tasks.length === 0 ? (
          <Card className="py-16 text-center text-sm text-gray-400">
            <Inbox className="mx-auto mb-2 h-8 w-8 text-gray-300" />
            还没有派发给你的任务
          </Card>
        ) : (
          <Card>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="px-4 py-2.5 font-normal">设计包</th>
                  <th className="px-4 py-2.5 font-normal">Batch</th>
                  <th className="px-4 py-2.5 font-normal">副素材</th>
                  <th className="px-4 py-2.5 font-normal">Parent ASIN</th>
                  <th className="px-4 py-2.5 font-normal">Child ASIN</th>
                  <th className="px-4 py-2.5 font-normal">状态</th>
                  <th className="px-4 py-2.5 font-normal">创建时间</th>
                  <th className="px-4 py-2.5 font-normal">接收时间</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr
                    key={task.id}
                    onClick={() => navigate(`/materials/distributions/${task.id}`)}
                    className="cursor-pointer border-b border-gray-50 text-gray-700 transition-colors last:border-0 hover:bg-[#f8f7fd]"
                  >
                    <td className="px-4 py-3">
                      <div className="font-medium text-gray-800">{task.packageName}</div>
                      <div className="text-[11px] text-gray-400">{task.packageCode}</div>
                    </td>
                    <td className="px-4 py-3">
                      <span className="rounded bg-gray-100 px-1.5 py-0.5 text-[11px] text-gray-600">{task.versionCode}</span>
                    </td>
                    <td className="px-4 py-3 font-mono text-[11px]">{task.variantCount}</td>
                    <td className="px-4 py-3 font-mono text-[11px]">{task.parentAsin?.asin ?? '—'}</td>
                    <td className="px-4 py-3 font-mono text-[11px]">{task.children.length}</td>
                    <td className="px-4 py-3">
                      <StatusBadge tone={STATUS_TONE[task.status] ?? 'neutral'}>
                        {STATUS_LABEL[task.status] ?? task.status}
                      </StatusBadge>
                    </td>
                    <td className="px-4 py-3 text-gray-500">{formatDateTime(task.assignedAt)}</td>
                    <td className="px-4 py-3 text-gray-500">{task.receivedAt ? formatDateTime(task.receivedAt) : '—'}</td>
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
