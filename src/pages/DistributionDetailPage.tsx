import { useState, useEffect, useCallback } from 'react'
import { useNavigate, useParams } from 'react-router'
import { CheckCircle2, ChevronRight, Download, ExternalLink, Link2, PackageCheck, Plus } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ActivityTimeline, StatusBadge } from '@/components/material/WorkflowPrimitives'
import { AMAZON_SITES, resolveListingUrl } from '@/lib/asin'
import { UPLOAD_TYPE_LABEL, formatDateTime } from '@/lib/workflow'
import { API_BASE, API_ENABLED, materialApi, type ListingDto } from '@/services/apiClient'
import {
  getTaskOverview,
  loadAllPackagesFromApi,
  loadDistributionTaskFromApi,
  receiveTask,
  useWorkflowState,
} from '@/store/workflowStore'

interface Props {
  /** 由路由传入，默认取当前登录运营 */
  actorName?: string
}

/** 运营端：接收素材 → 查看/下载副素材 → 新增上架链接（Listing URL 先行，Parent ASIN 后补） */
export default function DistributionDetailPage({ actorName }: Props) {
  const navigate = useNavigate()
  const { taskId = '' } = useParams()
  useWorkflowState()

  const overview = getTaskOverview(taskId)
  const task = overview?.task

  // 真实后端模式下：先 hydrate 设计包（副素材/资产），再拉取派发任务与上架链接
  const [loaded, setLoaded] = useState(!API_ENABLED)
  const [listings, setListings] = useState<ListingDto[]>([])
  const [downloaded, setDownloaded] = useState(false)
  // 新增上架链接表单
  const [listingUrl, setListingUrl] = useState('')
  const [listingParent, setListingParent] = useState('')
  const [listingStore, setListingStore] = useState('')
  const [listingSite, setListingSite] = useState('US')
  const [saving, setSaving] = useState(false)
  // 后补 Parent ASIN（行内编辑）
  const [parentDraft, setParentDraft] = useState<Record<string, string>>({})

  const loadListings = useCallback(async () => {
    if (!API_ENABLED || !taskId) return
    try {
      setListings(await materialApi.listTaskListings(taskId))
    } catch {
      setListings([])
    }
  }, [taskId])

  useEffect(() => {
    if (!API_ENABLED) return
    let cancelled = false
    void (async () => {
      try {
        await loadAllPackagesFromApi()
        await loadDistributionTaskFromApi(taskId)
        const rows = await materialApi.listTaskListings(taskId)
        if (!cancelled) setListings(rows)
      } catch {
        // 拉取失败保留 not-found 渲染；不静默回退 Mock
      }
      if (!cancelled) setLoaded(true)
    })()
    return () => {
      cancelled = true
    }
  }, [taskId])

  const variants = overview?.variants ?? []
  const logs = overview?.logs ?? []
  const actor = actorName || task?.operatorName || '运营'

  if (API_ENABLED && !loaded) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#f5f6f8] text-sm text-gray-500">
        <p>正在加载派发任务…</p>
        <Button variant="outline" onClick={() => navigate('/materials')}>返回素材中心</Button>
      </div>
    )
  }

  if (!overview || !task) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-3 bg-[#f5f6f8] text-sm text-gray-500">
        <p>派发任务不存在或已失效：{taskId || '(缺少 taskId)'}</p>
        <Button variant="outline" onClick={() => navigate('/materials')}>返回素材中心</Button>
      </div>
    )
  }

  const urlReady = /^https?:\/\/.+/i.test(listingUrl.trim())
  const parentReady = !listingParent.trim() || /^B0[A-Z0-9]{8}$/.test(listingParent.trim().toUpperCase())
  const canAddListing = task.status !== 'ACTIVE' && task.status !== 'CANCELLED' && urlReady && parentReady && !saving

  const handleReceive = async () => {
    try {
      await receiveTask(task.id, actor)
      toast.success('已接收素材')
    } catch (error) {
      const message = error instanceof Error ? error.message : '接收素材失败'
      toast.error(message)
    }
  }

  const handleDownload = () => {
    if (!API_ENABLED) {
      setDownloaded(true)
      toast.success(`已生成素材包 ${task.packageName}_${task.versionCode}.zip（演示环境未接入真实文件服务）`)
      return
    }
    // 真实下载：按 DistributionTask 快照打包的 ZIP（后端 GET /distributions/{id}/download）
    const url = `${API_BASE}/distributions/${task.id}/download`
    const anchor = document.createElement('a')
    anchor.href = url
    anchor.download = ''
    document.body.appendChild(anchor)
    anchor.click()
    anchor.remove()
    setDownloaded(true)
    toast.success('已开始下载素材包')
  }

  const handleAddListing = async () => {
    if (!canAddListing) return
    setSaving(true)
    try {
      const resp = await materialApi.createTaskListing(task.id, {
        listingUrl: listingUrl.trim(),
        parentAsin: listingParent.trim() || undefined,
        store: listingStore.trim() || undefined,
        site: listingSite || undefined,
      })
      if (resp.existed) {
        toast.success('该链接已存在，已把当前任务的素材关联到现有上架链接（未重复创建）')
      } else {
        toast.success('上架链接已保存' + (listingParent.trim() ? '' : '，Parent ASIN 待补充'))
      }
      setListingUrl('')
      setListingParent('')
      setListingStore('')
      await loadListings()
      await loadDistributionTaskFromApi(task.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '保存上架链接失败')
    } finally {
      setSaving(false)
    }
  }

  /** 后补 / 更新 Parent ASIN（更新原行，不新建） */
  const handleBindParent = async (listingId: string) => {
    const value = (parentDraft[listingId] ?? '').trim().toUpperCase()
    if (!/^B0[A-Z0-9]{8}$/.test(value)) {
      toast.error('Parent ASIN 格式应为 B0 + 8 位字母或数字')
      return
    }
    try {
      await materialApi.patchListing(listingId, { parentAsin: value })
      toast.success('Parent ASIN 已补充')
      setParentDraft((prev) => ({ ...prev, [listingId]: '' }))
      await loadListings()
    } catch (error) {
      toast.error(error instanceof Error ? error.message : '补充 Parent ASIN 失败')
    }
  }

  return (
    <div className="min-h-screen bg-[#f5f6f8] text-gray-800">
      <header className="flex h-12 items-center justify-between border-b bg-white px-6 text-xs">
        <div className="flex items-center gap-1.5 text-gray-400">
          <span className="font-medium text-gray-600">素材中心</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span>运营派发</span>
          <ChevronRight className="h-3.5 w-3.5" />
          <span className="truncate">{task.packageName}</span>
        </div>
        <Button size="sm" variant="outline" onClick={() => navigate('/materials')}>返回素材中心</Button>
      </header>

      <main className="mx-auto max-w-[1400px] space-y-4 p-5">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-xl font-semibold text-gray-900">运营接收素材</h1>
            <p className="mt-1 text-xs text-gray-400">确认接收后查看副素材、下载素材包，并登记上架链接（Listing URL 先行，Parent ASIN 后补）。</p>
          </div>
          <StatusBadge tone={task.status === 'COMPLETED' ? 'green' : task.status === 'RECEIVED' ? 'purple' : task.status === 'CANCELLED' ? 'neutral' : 'amber'}>
            {DISTRIBUTION_STATUS_LABEL[task.status]}
          </StatusBadge>
        </div>

        {/* ------------------------------------------------------ 设计包信息 */}
        <Card>
          <CardContent className="grid gap-4 py-5 md:grid-cols-6">
            <div className="md:col-span-2">
              <p className="text-xs text-gray-400">设计包</p>
              <p className="mt-1 font-medium">{task.packageName}</p>
              <p className="text-xs text-gray-400">{task.packageCode}</p>
            </div>
            <div><p className="text-xs text-gray-400">上架版本</p><p className="mt-1 font-medium">{task.versionCode}</p></div>
            <div><p className="text-xs text-gray-400">美工</p><p className="mt-1 font-medium">{task.designerName}</p></div>
            <div><p className="text-xs text-gray-400">副素材数量</p><p className="mt-1 font-medium">{variants.length}</p></div>
            <div><p className="text-xs text-gray-400">派发时间</p><p className="mt-1 font-medium">{formatDateTime(task.assignedAt)}</p></div>
            <div className="md:col-span-6 border-t pt-3 text-xs text-gray-500">
              原始文件包：<span className="font-medium text-gray-700">{overview.upload?.originalPackageName ?? '-'}</span>
              {overview.upload && (
                <span className="ml-3 text-gray-400">（{UPLOAD_TYPE_LABEL[overview.upload.uploadType]}）</span>
              )}
              {task.remark && <span className="ml-4">备注：<span className="font-medium text-gray-700">{task.remark}</span></span>}
            </div>
          </CardContent>
        </Card>

        {task.status === 'ACTIVE' && (
          <Card>
            <CardContent className="flex items-center justify-between py-4">
              <div className="flex items-center gap-3 text-sm">
                <PackageCheck className="h-5 w-5 text-[#3d3192]" />
                <span>该任务由 {task.operatorName} 接收，首次进入请确认接收。</span>
              </div>
              <Button className="bg-[#3d3192] hover:bg-[#32277a]" onClick={handleReceive}>接收素材</Button>
            </CardContent>
          </Card>
        )}

        {task.status === 'CANCELLED' && (
          <Card>
            <CardContent className="py-4 text-sm text-gray-500">
              该派发任务已取消，如需继续请由美工重新派发（同一版本取消后允许重新派发给 {task.operatorName}）。
            </CardContent>
          </Card>
        )}

        {/* ------------------------------------------------------ 副素材 Grid */}
        <Card>
          <CardHeader className="flex-row items-center justify-between">
            <div>
              <CardTitle className="text-base">副素材（{task.versionCode}）</CardTitle>
              <p className="mt-1 text-xs text-gray-400">
                副素材就是一张 JPG；`1-1` 表示主素材1在第1套上架版本中的副素材。多个派发任务复用同一套底层素材，不复制图片。
              </p>
            </div>
            <Button size="sm" variant="outline" onClick={handleDownload}>
              <Download className="h-4 w-4" />
              {downloaded ? '已准备下载' : '下载素材包'}
            </Button>
          </CardHeader>
          <CardContent>
            {variants.length === 0 ? (
              <p className="py-10 text-center text-sm text-gray-400">该版本暂无副素材</p>
            ) : (
              <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 md:grid-cols-6 lg:grid-cols-8">
                {variants.map((material) => (
                  <div key={material.id} className="overflow-hidden rounded-md border bg-white">
                    <img
                      src={material.imageUri || `/mock/mat-01.jpg`}
                      alt={material.displayCode}
                      className="aspect-square w-full bg-gray-50 object-cover"
                    />
                    <div className="flex items-center justify-between px-2 py-1.5 text-xs">
                      <span className="font-medium text-gray-700">{material.displayCode}</span>
                      <a
                        href={material.imageUri || '#'}
                        target="_blank"
                        rel="noreferrer"
                        className="text-gray-400 hover:text-[#3d3192]"
                        title="查看大图"
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </a>
                    </div>
                    {material.currentRevision > 1 && (
                      <div className="border-t px-2 py-1 text-[10px] text-gray-400">
                        当前 Revision {material.currentRevision}（共 {material.revisions.length} 版）
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>

        {/* ------------------------------------------------------ 上架记录（Listing） */}
        <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
          <Card>
            <CardHeader><CardTitle className="text-base">上架记录（Listing）</CardTitle></CardHeader>
            <CardContent className="space-y-5">
              {/* 已有上架链接列表：URL 先行，Parent ASIN 可空后补 */}
              {listings.length === 0 ? (
                <p className="rounded-md border border-dashed border-gray-200 py-6 text-center text-xs text-gray-400">
                  还没有上架链接。上架 Amazon 后，先粘贴 Listing URL 即可；Parent ASIN 拿到后再补。
                </p>
              ) : (
                <div className="space-y-2">
                  {listings.map((item) => (
                    <div key={item.id} className="rounded-md border border-gray-200 px-3 py-2.5">
                      <div className="flex items-center gap-2 text-xs">
                        <Link2 className="h-3.5 w-3.5 shrink-0 text-gray-400" />
                        {item.listingUrl ? (
                          <a
                            href={item.listingUrl}
                            target="_blank"
                            rel="noreferrer"
                            className="truncate font-mono text-[#3d3192] hover:underline"
                            title={item.listingUrl}
                          >
                            {item.listingUrl}
                          </a>
                        ) : (
                          <span className="text-gray-400">（历史记录，无 URL）</span>
                        )}
                        <span className="ml-auto shrink-0 text-gray-400">{formatDateTime(item.createdAt)}</span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[11px] text-gray-500">
                        <span>
                          Parent ASIN：
                          {item.parentAsin ? (
                            <span className="font-mono font-medium text-gray-800">{item.parentAsin}</span>
                          ) : (
                            <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">待补充</span>
                          )}
                        </span>
                        <span>店铺：<span className="text-gray-700">{item.store || '—'}</span></span>
                        <span>站点：<span className="text-gray-700">{item.site || '—'}</span></span>
                        <span>素材：<span className="text-gray-700">{item.materialCount} 主 / {item.variantCount} 副</span></span>
                        {/* 后补 Parent ASIN（行内编辑，更新原行不新建） */}
                        <span className="ml-auto flex items-center gap-1.5">
                          <Input
                            className="h-7 w-32 text-[11px]"
                            placeholder="B0XXXXXXXX"
                            value={parentDraft[item.id] ?? ''}
                            onChange={(event) =>
                              setParentDraft((prev) => ({ ...prev, [item.id]: event.target.value }))
                            }
                          />
                          <Button
                            size="sm"
                            variant="outline"
                            className="h-7 px-2 text-[11px]"
                            disabled={!parentDraft[item.id]?.trim()}
                            onClick={() => void handleBindParent(item.id)}
                          >
                            {item.parentAsin ? '更新 Parent' : '补 Parent ASIN'}
                          </Button>
                        </span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* 新增上架链接 */}
              <div className="space-y-3 rounded-md bg-gray-50 px-3 py-3">
                <p className="text-xs font-medium text-gray-600">
                  <Plus className="mr-1 inline h-3.5 w-3.5" />添加上架链接
                  <span className="ml-2 font-normal text-gray-400">同一 URL 重复录入时自动关联到现有链接，不会重复建</span>
                </p>
                <label className="block space-y-1.5 text-sm">
                  <span className="font-medium">Listing URL <b className="text-red-500">*</b></span>
                  <Input
                    placeholder="https://www.amazon.com/dp/B0XXXX（粘贴即可，追踪参数自动清理）"
                    value={listingUrl}
                    onChange={(event) => setListingUrl(event.target.value)}
                  />
                  {listingUrl && !urlReady && (
                    <span className="text-xs text-red-600">URL 必须以 http:// 或 https:// 开头</span>
                  )}
                </label>
                <div className="grid gap-3 md:grid-cols-3">
                  <label className="block space-y-1.5 text-sm">
                    <span className="font-medium">Parent ASIN<span className="ml-1 text-xs font-normal text-gray-400">（选填，可后补）</span></span>
                    <Input
                      placeholder="B0XXXXXXXX"
                      value={listingParent}
                      onChange={(event) => setListingParent(event.target.value)}
                    />
                    {listingParent && !parentReady && (
                      <span className="text-xs text-red-600">格式应为 B0 + 8 位字母或数字</span>
                    )}
                  </label>
                  <label className="block space-y-1.5 text-sm">
                    <span className="font-medium">店铺<span className="ml-1 text-xs font-normal text-gray-400">（选填）</span></span>
                    <Input
                      placeholder="如 店铺A"
                      value={listingStore}
                      onChange={(event) => setListingStore(event.target.value)}
                    />
                  </label>
                  <label className="block space-y-1.5 text-sm">
                    <span className="font-medium">站点</span>
                    <Select value={listingSite} onValueChange={setListingSite}>
                      <SelectTrigger><SelectValue /></SelectTrigger>
                      <SelectContent>
                        {AMAZON_SITES.map((item) => (
                          <SelectItem key={item.code} value={item.code}>{item.label}（{item.domain}）</SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </label>
                </div>
                <div className="flex items-center gap-3">
                  <Button className="bg-[#3d3192] hover:bg-[#32277a]" disabled={!canAddListing} onClick={handleAddListing}>
                    <CheckCircle2 className="h-4 w-4" />
                    {saving ? '保存中…' : '保存上架链接'}
                  </Button>
                  {task.status === 'ACTIVE' && <p className="text-xs text-gray-400">请先点击上方「接收素材」后再登记。</p>}
                </div>
              </div>
            </CardContent>
          </Card>

          <div className="space-y-4">
            {task.parentAsin && (
              <Card>
                <CardHeader><CardTitle className="text-base">历史 ASIN 关联</CardTitle></CardHeader>
                <CardContent className="space-y-3 text-xs">
                  <div>
                    <p className="text-gray-400">Parent ASIN</p>
                    <a
                      href={resolveListingUrl({ asin: task.parentAsin.asin, listingUrl: task.parentAsin.listingUrl, site: task.parentAsin.site })}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 font-mono text-[#3d3192] hover:underline"
                    >
                      {task.parentAsin.asin} <ExternalLink className="h-3 w-3" />
                    </a>
                  </div>
                  <div>
                    <p className="text-gray-400">Child ASIN（{task.children.length}）</p>
                    <div className="mt-1 flex flex-wrap gap-1.5">
                      {task.children.map((child) => (
                        <a
                          key={child.asin}
                          href={resolveListingUrl({ asin: child.asin, listingUrl: child.listingUrl, site: child.site })}
                          target="_blank"
                          rel="noreferrer"
                          className="inline-flex items-center gap-0.5 rounded border border-gray-200 px-1.5 py-0.5 font-mono text-[11px] text-gray-600 hover:border-[#3d3192] hover:text-[#3d3192]"
                        >
                          {child.asin}
                          <ExternalLink className="h-2.5 w-2.5" />
                          {child.overrideVariantIds?.length ? <span className="ml-1 text-amber-600">单独素材</span> : null}
                        </a>
                      ))}
                    </div>
                    <p className="mt-2 text-[11px] text-gray-400">默认继承 Parent 素材组；Child 单独素材覆盖（overrideVariantIds）已在数据结构中预留。</p>
                  </div>
                </CardContent>
              </Card>
            )}

            <Card>
              <CardHeader><CardTitle className="text-base">维护记录</CardTitle></CardHeader>
              <CardContent className="max-h-[420px] overflow-y-auto">
                <ActivityTimeline logs={logs} />
              </CardContent>
            </Card>
          </div>
        </div>
      </main>
    </div>
  )
}

const DISTRIBUTION_STATUS_LABEL: Record<string, string> = {
  ACTIVE: '待接收',
  RECEIVED: '已接收',
  COMPLETED: '已上架',
  CANCELLED: '已取消',
}
