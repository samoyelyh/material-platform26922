import { useEffect, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { formatDateTime } from '@/lib/workflow';
import { materialApi, type ListingDto } from '@/services/apiClient';

/**
 * 素材「上架信息」：该素材被哪些 Listing 使用（关联链接数量 + 列表）。
 * Parent ASIN 为空显示「待补充」，不显示 null。
 * 数据源：materialApi.materialListings(MAT) / materialApi.variantListings(副素材)。
 * 页面结构预留：后续可在每条链接右侧追加「关联链接销量」（下一阶段与 order-center 对接）。
 */
export function MaterialListings({
  materialCode,
  variantId,
}: {
  /** 主素材 MAT code（materials/{code}/listings） */
  materialCode?: string;
  /** 副素材 variant id（material-variants/{id}/listings） */
  variantId?: string;
}) {
  const [rows, setRows] = useState<ListingDto[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    const fetcher = materialCode
      ? materialApi.materialListings(materialCode)
      : variantId
        ? materialApi.variantListings(variantId)
        : Promise.resolve([] as ListingDto[]);
    fetcher
      .then((data) => {
        if (!cancelled) setRows(data);
      })
      .catch(() => {
        if (!cancelled) setRows([]);
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [materialCode, variantId]);

  if (loading) {
    return <div className="px-5 py-16 text-center text-[13px] text-gray-400">正在加载上架信息…</div>;
  }

  if (!rows.length) {
    return (
      <div className="px-5 py-16 text-center text-[13px] text-gray-400">
        该素材尚未出现在任何上架链接中
        <div className="mt-1 text-[11px] text-gray-400">
          运营领取素材并上架后，在派发任务里登记 Listing URL，这里就会出现关联链接。
        </div>
      </div>
    );
  }

  return (
    <div className="px-5 py-4">
      <p className="mb-3 text-[11px] text-gray-400">
        关联链接：<b className="text-gray-700">{rows.length}</b>（Parent ASIN 为空表示运营尚未补充；销量展示属下一阶段）
      </p>
      <div className="space-y-2">
        {rows.map((row) => (
          <div key={row.id} className="rounded-md border border-gray-100 bg-white p-3 text-xs">
            <div className="flex items-center gap-2">
              {row.listingUrl ? (
                <a
                  href={row.listingUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex min-w-0 items-center gap-1 font-mono text-[#3d3192] hover:underline"
                >
                  <span className="truncate" title={row.listingUrl}>{row.listingUrl}</span>
                  <ExternalLink className="h-3 w-3 shrink-0" />
                </a>
              ) : (
                <span className="text-gray-400">（历史记录，无 URL）</span>
              )}
              <span className="ml-auto shrink-0 text-[11px] text-gray-400">{formatDateTime(row.createdAt)}</span>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
              <span>
                Parent ASIN：
                {row.parentAsin ? (
                  <span className="font-mono font-medium text-gray-800">{row.parentAsin}</span>
                ) : (
                  <span className="rounded bg-amber-50 px-1.5 py-0.5 text-amber-600">待补充</span>
                )}
              </span>
              <span>店铺：<span className="text-gray-700">{row.store || '—'}</span></span>
              <span>站点：<span className="text-gray-700">{row.site || '—'}</span></span>
              <span>运营：<span className="text-gray-700">{row.createdBy || '—'}</span></span>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
