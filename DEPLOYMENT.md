# material-platform 部署文档（V1）

> 本文档只记录**可公开**的部署结构与流程，**不包含任何密码 / Secret / Token**。
> 数据库密码等敏感信息只由部署环境的 `.env` 提供，`.env` 不进 Git。

---

## 一、生产拓扑

```text
浏览器
  → http://192.168.0.27:8021
    → mp-frontend（Nginx 容器，托管 React SPA + /api 同源反代）
       ├─ 静态资源（Vite build 产物 dist，SPA fallback）
       └─ /api  →  backend:8020（Docker 内部网络，同源反代，浏览器不直连后端）
            → mp-backend（FastAPI/uvicorn 容器）
               ├─ mp-mysql（容器内 3306）→ 宿主机 192.168.0.27:3307
               │    └─ database = material_center
               └─ /data/storage/material-platform（本地持久化存储）
```

**访问链路**：

```text
用户 → 192.168.0.27:8021（mp-frontend / Nginx）
     → 前端静态页（React SPA）
     → 前端请求 /api（同源，浏览器只访问 8021）
     → Nginx 反代 /api → backend:8020（Docker 网络内部）
     → mp-backend → mp-mysql（material_center）/ 本地存储
```

---

## 二、服务端口

| 服务 | 容器 | 宿主机端口 | 容器内端口 | 说明 |
|---|---|---|---|---|
| 前端 | `mp-frontend` | **8021** | 80 | Nginx 托管 SPA + /api 反代 |
| 后端 | `mp-backend` | **8020** | 8020 | FastAPI/uvicorn |
| MySQL | `mp-mysql` | **3307** | 3306 | 库 `material_center` |

> **注意**：`192.168.0.27:3306` **不是** material-platform 的数据库（那是另一套 MySQL）。
> material-platform 的 MySQL 对外是 **3307**（容器内部才是 3306）。不要混淆。

---

## 三、部署目录

```text
源码：        /data/apps/material-platform     （git 仓库，main 分支）
部署配置：    /data/deploy/material-platform    （docker-compose.yml + .env）
素材持久化：  /data/storage/material-platform   （Asset 文件，本地存储）
数据库数据：  /data/mysql/material_center       （MySQL 数据文件）
备份：        /data/backups/                    （mysqldump 产物，不进 Git）
```

约定：
- `.env`（在 `/data/deploy/material-platform/`）**不进 Git**，只存于部署环境。
- 数据库密码只由部署环境 `.env` 提供。
- 存储当前是**本地持久化**（`STORAGE_BACKEND=local`），**不是 MinIO**。
- 9000 端口上的 MinIO 属于其它服务（milvus），与 material-platform 无关，不要操作。
- FRP 与 material-platform 无关；`10015` 与 material-platform 无关（不是本系统入口）。

---

## 四、构建产物（不手改）

| 文件 | 作用 |
|---|---|
| `backend/Dockerfile.deploy` | 后端生产镜像构建（补齐 requirements.txt 缺失的 numpy） |
| `Dockerfile.frontend` | 前端生产镜像（多阶段：node build → nginx 托管） |
| `nginx.conf` | 前端 Nginx 配置（SPA fallback + /api 反代 backend:8020） |
| `backend/Dockerfile` | 后端开发用镜像（仓库自带） |

前端构建参数（compose 传入）：
- `VITE_MATERIAL_API_BASE=/api`（同源相对路径，浏览器不直连后端，不硬编码局域网地址）
- `VITE_MATERIAL_API=1`（**生产必须走真实后端，禁止 Mock**）
- 生产构建会剔除 `public/mock` 演示图片。

---

## 五、部署更新流程

以 `/data/deploy/deploy_update.sh` 为准（当前生产已验证），标准步骤：

```bash
# 1. 拉取最新代码（fast-forward，不改写历史）
cd /data/apps/material-platform && git pull --ff-only

# 2. 重建 + 重启（后端镜像重建；前端镜像也随 compose 重建）
cd /data/deploy/material-platform && docker compose up -d --build
```

容器启动时后端自动执行 `alembic upgrade head`（幂等）。

健康检查：

```bash
curl http://192.168.0.27:8020/api/health        # 后端
curl http://192.168.0.27:8021/api/health        # 经前端反代的后端
curl -I http://192.168.0.27:8021/               # 前端 SPA
```

**回滚注意事项**：
- 代码回滚：`git reset --hard <上一个已知好版本>` 后 `docker compose up -d --build`。
- 数据库回滚：DDL 非事务性，**升级前必须先备份**（见下节）；回滚代码前若已升库，需评估新版本是否兼容旧库结构。
- 不要直接在生产上 `alembic downgrade` 而不评估数据。

---

## 六、数据库升级规则

当前 migration head：`0011_distribution_asin`

**上线前数据库升级必须遵循**：

```text
备份（mysqldump → /data/backups/）
→ alembic current           （确认当前 revision）
→ 检查 schema drift         （真实表结构 vs migration 历史一致）
→ alembic upgrade head      （只升不退）
→ health check              （/api/health + 关键业务接口）
```

**禁止**：不备份直接对生产库强升；禁止 `alembic stamp` / `drop` / 强制 `ALTER` 来掩盖漂移。

备份命令（示例，密码从 .env 读）：

```bash
docker exec mp-mysql mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" \
  --single-transaction --routines --triggers --databases material_center \
  > /data/backups/material_center_$(date +%Y%m%d_%H%M%S).sql
```

---

## 七、order-center 对接契约（素材侧只读）

order-center 正式依赖（只读契约，**不要改坏**）：

```
GET /api/contract/materials-by-child-asin/{child_asin}
```

契约语义：

```text
Child ASIN
→ 未取消的 DistributionTask
→ Batch（该次派发对应的上架版本）
→ Variant 候选集合（一整套副素材，不是单个）
→ MAT（主素材）
→ 图片角色：MATERIAL_SOURCE / FINAL_EFFECT（Black / White）
```

约定（**后续开发不要破坏**）：
- Child ASIN **不是**固定绑定单个 Variant；一个 Batch 下是一整套 Variant 候选。
- 订单侧负责最后识别是哪个 Variant；material-platform 只提供候选事实。
- 已取消（CANCELLED）的派发任务**不参与**契约查询。
- 未绑定的 Child ASIN 返回 **404**（不返回编造关系）。
- 契约不暴露内部 ORM / `storage_key` / `blake3` / `phash`。

---

## 八、V1 明确不做的功能

留待后续明确需求再开新版本：

- deliveryRound 补派发（追加交付轮次）
- MinIO 迁移（当前本地存储即可）
- 公网 FRP / 外部访问入口（由运维另行管理）
- 新品类订单解析
- order-center 订单逻辑（属 order-center 仓库）
- Child ASIN 直接绑定单个 Variant
- 第二套 ASIN 关系表

---

## 九、常用运维命令

```bash
# 查看容器
docker ps --filter name=mp-

# 后端日志
docker logs mp-backend --tail 100

# 前端日志
docker logs mp-frontend --tail 100

# 进入 MySQL
docker exec -it mp-mysql mysql -uroot -p"$MYSQL_ROOT_PASSWORD" material_center

# 手动迁移（在 backend 容器内）
docker exec mp-backend python -m alembic current
docker exec mp-backend python -m alembic upgrade head
```
