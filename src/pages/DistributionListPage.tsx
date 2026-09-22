import { useCallback, useEffect, useState } from 'react';
import { useNavigate } from 'react-router';
import { ChevronRight } from 'lucide-react';
import { Button } from '@/components/ui/button';
import { Card } from '@/components/ui/card';
import { StatusBadge } from '@/components/material/WorkflowPrimitives';
import { formatDateTime } from '@/lib/workflow';
import { API_ENABLED, materialApi, type DistributionTaskDto } from '@/services/apiClient';

const STATUS_FILTERS: { key: string; label: string; value?: string }[] = [
  { key: 'all', label: '全部' },
  { key: 'ACTIVE', label: '待接收', value: 'ACTIVE' },
  { key: 'RECEIVED', label: '已接收', value: 'RECEIVED' },
  { key: 'COMPLETED', label: '已绑定ASIN', value: 'COMPLETED' },
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

/** 分发任务列表：设计包 / Batch / 运营 / Parent ASIN / Child 数 / 状态 / 时间，点击进入详情 */
export default function DistributionListPage() {
  const navigate = useNavigate();
  const [status, setStatus] = useState<string>('all');
  const [tasks, setTasks] = useState<DistributionTaskDto[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(() => {
    if (!API_ENABLED) {
      setTasks([]);
      setLoading(false);
      return;
    }
    setLoading(true);
    void materialApi
      .listAllDistributions(status === 'all' ? undefined : status)
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
          <span>运营派发</span>
        </div>
        <Button size="sm" variant="outline" onClick={() => navigate('/materials')}>
          返回素材中心
        </Button>
      </header>

      <main className="mx-auto w-full max-w-[1400px] space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">分发任务</h1>
            <p className="mt-1 text-xs text-gray-400">点击任意任务进入详情（接收 / 查看副素材 / 回填 ASIN）。</p>
          </div>
        </div>

        {/* 状态筛选 */}
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
              {f.key !== 'all' && (
                <span className="ml-1 opacity-70">
                  {tasks.filter((t) => t.status === f.key).length}
                </span>
              )}
            </button>
          ))}
          {API_ENABLED && (
            <button onClick={load} className="ml-auto text-xs text-[#3d3192] hover:underline">
              刷新
            </button>
          )}
        </div>

        {!API_ENABLED ? (
          <Card className="py-16 text-center text-sm text-gray-400">
            当前为本地 Mock 模式（VITE_MATERIAL_API=0），不展示真实分发任务。请连接后端后查看。
          </Card>
        ) : loading ? (
          <Card className="py-16 text-center text-sm text-gray-400">正在加载分发任务…</Card>
        ) : tasks.length === 0 ? (
          <Card className="py-16 text-center text-sm text-gray-400">
            {status === 'all' ? '还没有任何分发任务' : `没有「${STATUS_LABEL[status] ?? status}」状态的任务`}
            <div className="mt-1 text-[11px] text-gray-400">设计包生成版本后，可在上传页「提交并派发运营」。</div>
          </Card>
        ) : (
          <Card>
            <table className="w-full text-xs">
              <thead>
                <tr className="border-b border-gray-100 text-left text-gray-400">
                  <th className="px-4 py-2.5 font-normal">设计包</th>
                  <th className="px-4 py-2.5 font-normal">Batch</th>
                  <th className="px-4 py-2.5 font-normal">运营</th>
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
                    <td className="px-4 py-3">{task.operatorName}</td>
                    <td className="px-4 py-3 font-mono text-[11px]">{task.parentAsin?.asin ?? '—'}</td>
                    <td className="px-4 py-3">
                      <span className="font-mono text-[11px]">{task.children.length}</span>
                      {task.children.length > 0 && (
                        <span className="ml-1 text-[11px] text-gray-400">
                          {task.children.slice(0, 3).map((c) => c.asin).join('、')}
                          {task.children.length > 3 ? ' …' : ''}
                        </span>
                      )}
                    </td>
                    <td className="px-4 py-3">
                      <StatusBadge tone={STATUS_TONE[task.status]}>{STATUS_LABEL[task.status] ?? task.status}</StatusBadge>
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
