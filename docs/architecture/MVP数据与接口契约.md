# TeamHub MVP 数据与接口契约

> M1 交付物。把 [MVP 实施蓝图](../product/TeamHub-MVP实施蓝图.md) §3 落成具体字段、约束、接口与错误码。
> 本文是**工程默认**的具体化:不改变基石已确认原则;与蓝图冲突时以蓝图为准并修订本文。

> **新增规划边界（2026-10-05）：** 本文记录既有 MVP 结构与后续实现补充。新一轮资料级授权、独立网页、投递附件及版本来源改造见[云端与资料投递实施蓝图](../product/云端与资料投递实施蓝图.md)；本轮已落地的字段/API 见 §10，完整验证范围见对应验收记录。旧空间级权限、ShareLink 或下载通路不能据此被视为已满足新增规则。

| 文档信息 | 内容 |
| --- | --- |
| 编号 / 版本 | TH-MVP-CONTRACT / v0.3 |
| 建立日期 | 2026-09-23 |
| 最后更新 | 2026-10-23 |
| 上位依据 | [项目基石](../product/TeamHub项目基石.md)、[MVP 实施蓝图](../product/TeamHub-MVP实施蓝图.md) §2—§3 |
| 适用范围 | M1 起全部新/改代码;旧接口仅按 §7 停用清单处理 |

## 1. 概念与 ID 规则

| 概念 | 规则 |
| --- | --- |
| 资料 ID | 本地资料:本地 UUID(v7 优先,回退 v4);共享资料:**服务端生成** UUID,与个人 ID 无对应假设 |
| 版本 ID | 由所在库生成,全局唯一;`(document_id, version_number)` 在所在库内唯一(数据库约束) |
| 内容摘要 | 单文件/压缩包:内容字节的 `sha256`;文件夹:规范化清单摘要(算法见蓝图 §3.3,M3 固定编码版本) |
| 归档摘要 | 文件夹传输归档字节的 `sha256`,与内容摘要分开保存,用于传输校验 |
| 存储键 | 不可变受管内容的定位键,形如 `objects/<sha256前2位>/<sha256>`;版本只引用存储键,不直接指向可改写的工作文件 |
| 当前版本 | 资料行的 `current_version_id` 是**唯一事实来源**;旧 `is_current` 列迁移后不再写入(见 §6) |
| 请求 ID | 客户端生成的 UUID;(操作者, 空间, 请求 ID) 唯一,绑定请求内容摘要 |

改名、改目录、补标签不产生内容版本。成功提交的新版本即当前版本,线性历史,无分支。

## 2. 个人库 Schema(每个文件库一个 SQLite)

### 2.1 documents(个人资料)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | str(36) PK | 本地 UUID |
| name | str(255) | 用户可改,不影响版本 |
| description | text | — |
| doc_type | str(10) | `file` / `folder` / `archive`;由旧 `is_folder` 迁移(folder 外一律 file,压缩包不单独识别) |
| work_path | str(1000) | 工作位置(用户可见的副本);由旧 `storage_path`/`original_path` 迁移 |
| current_version_id | str(36) FK→versions.id | **新增**,唯一当前版本来源 |
| status | str(20) | `pending` / `organized` / `missing` / `trashed`(**保留旧值**;原拟更名 `active` 已撤销,见 §6.1) |
| file_count / total_size | int | 当前版本快照的统计 |
| created_at / updated_at / trashed_at | datetime | UTC |

废弃(保留列、停止写入,读方仅迁移代码):`storage_mode`、`sync_status`、`last_synced_at`、`cloud_sync_enabled`、`share_visibility`、`server_version`、`workspace_id`、`local_modified`、`cloud_updated_at`、`cloud_source`、`cloud_doc_id`、`cloud_pushed_at`、`cloud_hash`。共享关联统一由 §2.4 `share_links` 承接。

### 2.2 versions(个人版本)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | str(36) PK | — |
| document_id | str(36) FK | 级联删除 |
| version_number | int | **新增约束:UNIQUE(document_id, version_number)** |
| content_hash | str(64) | 内容摘要(不含 `sha256:` 前缀);由旧 `sha256_hash` 迁移,索引保留 |
| manifest_hash | str(64) NULL | 文件夹归档摘要 |
| storage_key | str(1000) | 不可变快照位置;由旧 `file_path` 迁移语义,旧 `relative_path` 废弃(文件夹为整体快照) |
| file_size | int | — |
| original_filename | str(255) | — |
| note | text NULL | — |
| created_at | datetime | UTC |

废弃:`is_current`(迁移后停写)、`sync_status`、`last_synced_at`、`last_file_mtime`(M3 需要时以新机制重引入)。

**M2 修复补充（2026-09-28）：** 个人版本 API 的 `is_current` 响应从 `documents.current_version_id` 推导；新版本旧列固定为 false，不再更新历史行的当前标记。整理、重定位、扫描不得修改任何已保存版本的路径和摘要。恢复语义已于 2026-09-30 更新：直接替换工作文件并将所选历史版本设为当前；未保存修改先自动保存保护版本，操作记录写入变更日志。恢复本身不追加内容版本，替换或提交失败则补偿回滚。

普通收录默认复制；2026-09-29 产品决策调整：拖拽快速收录采用移动，成功后原路径不再保留文件，同步创建首版快照；失败补偿回原路径，撤销移回原路径且不覆盖已有文件。重复内容跳过收录并保留原文件。重复内容响应同时返回 `document` 与匹配的历史 `version`。普通接口显式 move 收录被拒绝（移动仅用于快速收录），原 index 兼容入口保留。当前个人删除仅移除资料记录和工作副本，快照暂留；物理回收必须另行检查全部引用。回收站只按资料 ID 定位，旧文件名条目不自动猜测归属。详细边界见 [M2 修复记录](../reviews/M2复核与修复记录.md)。

### 2.3 tags / document_tags(个人标签)

- `tags`:id、name(** UNIQUE,限当前个人库**)、color。维持现状。
- `document_tags`:document_id + tag_id 复合主键,**新增 `created_at`**。个人库贡献者恒为本人,不建贡献者列。
- 重复添加同一关联 = 幂等成功,不报错。

### 2.4 share_links(个人↔共享关联,新增表)

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| id | str(36) PK | — |
| local_document_id | str(36) FK→documents.id | **UNIQUE**:本地一份资料最多关联一个共享资料 |
| server_url | str(255) | 共享服务端点 |
| workspace_id | str(36) | 共享空间 |
| shared_document_id | str(36) | 服务端资料 ID |
| base_version_id | str(36) | 本地已知的共享当前版本(提交新版本时的基准) |
| created_at / updated_at | datetime | — |

### 2.5 favorites(收藏,新增表)

id PK;`target_type` = `local` / `shared`;local 目标存 `local_document_id` FK;shared 目标存 `server_url + workspace_id + shared_document_id`(纯引用,不复制内容);`created_at`。失去访问权时前端提示不可用,不绕过鉴权。

### 2.6 change_logs

保留表;仅作为本地操作记录,**不再承担推送职责**(通用双向同步停用,见 §7)。

## 3. 共享库 Schema(共享服务独立 SQLite)

沿用 `User / RefreshToken / Team / TeamMember / Workspace` 骨架;首版一个组织一个空间,管理员预置成员(公开注册、加入审批路由不挂载,见 §7)。

### 3.1 cloud_documents(共享资料)

| 字段 | 变化 | 说明 |
| --- | --- | --- |
| id | 改 | **服务端生成**,删除"与桌面端 UUID 一致"假设 |
| workspace_id | 保留 | 归属空间,一切权限检查的边界 |
| name / description / doc_type / file_count / total_size | 保留+调整 | `is_folder` → `doc_type`,同 §2.1 |
| current_version_id | **新增** | 唯一当前版本来源,替代 `is_current` 与 `server_version` 游标语义 |
| created_by | 改 | 由 `uploader_id` 迁移回填;发布者记录,不随版本提交改变 |
| status | 保留旧值 | `pending` / `organized` / `trashed`(更名 `active` 已撤销,同 §6.1) |
| trashed_at / trashed_by | 新增后者 | 管理员操作留痕 |
| created_at / updated_at | 保留 | — |

废弃:`server_version`(冲突检测改由基准版本 ID 承担,蓝图 §2.2)。

### 3.2 cloud_versions(共享版本)

| 字段 | 变化 | 说明 |
| --- | --- | --- |
| id | 改 | 服务端生成 |
| document_id / version_number | 保留 | **新增 UNIQUE(document_id, version_number)** |
| content_hash / manifest_hash | 改/增 | 由 `sha256_hash` 迁移;content_hash **不设唯一约束**(恢复历史可形成同内容新记录) |
| storage_key | 改 | 由 `oss_key` 迁移语义:指向服务端受管目录的不可变对象,不再是外部 OSS key |
| file_size / original_filename / note / created_at | 保留 | `relative_path` 废弃 |
| author_id | **新增** FK→users.id | 版本提交者;不得以文档 created_by 代替;成员停用后记录仍可辨认 |
| base_version_id | **新增** NULL | 提交时声明的基准版本(审计用) |

废弃:`is_current`(迁移后停写)。

### 3.3 cloud_tags / cloud_document_tags(共享标签)

- `cloud_tags` 维持:`UNIQUE(workspace_id, name)`。
- `cloud_document_tags`:**新增 `added_by` FK→users.id、`created_at`**;复合主键不变。"本人贡献"按关联行判断,不按标签词首创者判断;重复添加幂等。
- 不提供改写标签词的全局接口(蓝图 §3.4)。

### 3.4 publication_requests(发布请求,新增表)

| 字段 | 说明 |
| --- | --- |
| id | str(36) PK,服务端内部 ID |
| request_id | str(36),客户端幂等键;**UNIQUE(actor_id, workspace_id, request_id)** |
| actor_id / workspace_id | 操作者与空间 |
| request_hash | str(64),请求内容摘要(目标、内容哈希、标签集合);同 request_id 不同摘要 → 拒绝 |
| action | `publish` / `submit_version` |
| status | `pending` / `succeeded` / `failed` |
| result_document_id / result_version_id | 成功后的结果 |
| error_code / error_message | 失败原因 |
| created_at / completed_at | — |

### 3.5 admin_actions(管理操作记录,新增表)

id PK;actor_id;action(`trash` / `restore` / `purge` / `deactivate_user` / `remove_tag_link`);target 描述(JSON,含资料/版本/关联 ID);created_at。不含令牌与文件正文。

### 3.6 保留但不挂载到 MVP 入口的表

`Device`、`CloudChangeLog`、`JoinRequest`、`UserBackup`:表保留(不删数据),对应路由停用(§7)。

## 4. 错误信封与错误码

所有 4xx/5xx 响应统一:

```json
{ "error": { "code": "STALE_BASE_VERSION", "message": "基准版本已过期", "details": { "current_version_id": "..." } } }
```

| code | HTTP | 含义 |
| --- | --- | --- |
| VALIDATION_ERROR | 422 | 输入校验失败 |
| UNAUTHENTICATED | 401 | 未登录或令牌失效 |
| FORBIDDEN | 403 | 已认证但无权限(非成员、非贡献者、非管理员) |
| NOT_FOUND | 404 | 资源不存在或对调用者不可见(不区分,防探测) |
| STALE_BASE_VERSION | 409 | 基准版本不是当前版本;details 带当前版本入口 |
| REQUEST_MISMATCH | 409 | 同 request_id 携带不同内容摘要 |
| DUPLICATE_CONTENT | 云发布及本地取回 200(携候选)；个人发布适配层 409 | 查重命中;返回既有资料入口,**不创建**；接口形状见“双向实体查重修订” |
| CONTENT_UNCHANGED | 200 | 提交内容与当前版一致;返回当前版本 |
| STORAGE_ERROR | 507/500 | 磁盘不足、校验不符等;不产生成功记录 |
| GONE_DECOMMISSIONED | 410 | 旧路径已停用,message 指明替代入口 |

## 5. 接口契约(目标形态)

本地接口仅接受回环来源(蓝图 §3.7);共享接口全部要求 Bearer JWT + 空间归属检查。

### 5.1 本地服务(个人库)

| 接口 | 输入 | 输出 / 约束 |
| --- | --- | --- |
| POST /api/documents | source_path、doc_type、tags[]、note? | 默认**复制**收录;查重命中返回 `DUPLICATE_CONTENT` 与既有入口,不新建 |
| POST /api/documents/{id}/organize | tags[]、target_dir?、name? | 至少一个有效标签；target_dir 省略或空表示资料库顶层；完整保留文件夹内部结构 |
| POST /api/documents/batch/organize | document_ids[]、tags[]、target_dir? | 目录可选；追加共同标签并保留原标签；整个批次失败时回滚 |
| GET /api/documents/{id} | — | 详情含 current_version |
| GET /api/documents/{id}/versions | — | 版本列表(版本号倒序) |
| POST /api/documents/{id}/versions | note? | 从工作位置创建快照新版本(M2 实现完整语义) |
| POST /api/documents/{id}/versions/{vid}/restore | — | 恢复=替换工作文件并切换当前版本；未保存修改自动保护（2026-09-30） |
| DELETE /api/documents/{id}/versions/{vid} | — | 删除非当前版本记录；当前版本返回 409；底层快照保留，待引用安全清理 |
| POST /api/documents/{id}/versions/{vid}/export | target_dir | 复制指定版本内容到目标目录,不覆写已有文件(M2) |
| GET /api/favorites / POST / DELETE /{id} | 资料引用 | M3 实现 |
| 检索(名称/标签/联想) | 沿用现有路由 | M3 按契约对齐,本轮不改 |

### 5.2 共享服务(组织空间)

路由前缀 `/api/workspaces/{ws_id}`,全部经 `check_workspace_access` + 资源↔空间归属校验:

| 接口 | 输入 | 输出 / 约束 |
| --- | --- | --- |
| GET /documents | name?、tag_id[]、分页 | 权限过滤后列表;标签组合 AND |
| GET /documents/suggestions | type=name\|tag\|cooccurrence、q、selected_tag_ids[] | 限当前空间,限候选数(M3/M5) |
| POST /duplicates | doc_type、content_hash | 当前空间内匹配的资料+版本;不暴露其他空间 |
| POST /publications | request_id、资料描述、tags[]、内容哈希(内容经上传票据先行) | 校验完整才可见;同 request_id 幂等返回 |
| GET /publications/{request_id} | — | 本人请求的状态与结果 |
| POST /documents/{id}/versions | request_id、base_version_id、内容哈希、note? | 基准过期 → STALE_BASE_VERSION;成功即当前版 |
| GET /documents/{id}/versions | — | 版本列表(含 author、摘要) |
| GET /documents/{id}/versions/{vid}/download | — | 三者归属匹配后流式返回 |
| POST /uploads(M4 新增) | 原始字节流(application/octet-stream,filename 经查询参数) | 流式暂存并**重算 sha256**;返回 upload_id 与实测摘要/大小,供发布/提交引用;暂存被消费后移入受管对象库 |
| POST /documents/{id}/tags | add: 名称[] / remove: 关联 ID[] | 按关联增删;只能移除自己的关联(管理员除外) |
| POST /documents/{id}/trash · /restore · /purge | — | 管理员;purge 前检查内容引用 |
| POST /admin/users/{id}/deactivate | — | 停用成员,资料与贡献保留 |

上传/下载经本地服务流式转发(蓝图 §3.6);共享服务对暂存内容重新计算摘要,不信任客户端声称值。

## 6. 迁移规则(M1 交付,M6 演练)

**2026-09-26 补充：** 以下回填规则只适用于通过预检的历史。个人库与共享库必须在结构修改前检查重复版本号、多个旧当前标记及旧子文件历史；存在上述情况时停止并报告资料 ID，不自动重编号、删行或把子文件记录解释为整体快照。个人库同时检查已有当前版本引用的存在性及归属。应在副本中核实后重试；真实资产迁移演练仍留 M6。

个人库空库与升级库均通过唯一索引及引用保护保证：版本号在资料内唯一，非空当前版本属于本资料；被引用版本不能改归属，删除时清空当前引用。无当前标记时的最大版本号兜底不适用于多当前标记冲突。详见 [M1 复核与修复记录](../reviews/M1复核与修复记录.md)。

1. **个人库迁移**(幂等函数,随启动执行):新增列/表不存在则创建;`is_folder` → `doc_type`;`is_current=true` 的版本写入 `documents.current_version_id`(每个资料取 version_number 最大者为兜底);写完后代码不再写 `is_current`。**状态值不更名**:`organized` 等旧值保留(原拟更名 `active`,因现有代码与前端深度依赖旧值、更名属装饰性改动,M2 撤销)。
2. **共享库迁移**(Alembic,cloud_backend/alembic 已有骨架):同上原则;`uploader_id` → `created_by`;`oss_key` → `storage_key` 仅改语义不改值(旧 OSS 内容不作迁移承诺,见蓝图 §6.7)。
3. 旧库副本验证:构造含旧 schema 的副本执行迁移,校验约束生效、当前版本引用唯一、无数据丢失;原库只读不动。
4. 任何"两套当前版本来源"的中间状态都视为缺陷:迁移后**新代码**读当前版本只认 `current_version_id`。
5. **过渡期保留策略(M1 实施决定):** 共享库旧列(`is_folder`/`uploader_id`/`server_version`/`is_current`/`sha256_hash`/`oss_key`/`relative_path`)在新列回填后保留可读,因为搜索、详情等旧端点在 M4 重写前仍引用它们;旧写入路径在 M4 由发布通路整体替换后再删除旧列。个人库同理(契约 §2 已列废弃字段)。该策略不改变"新代码只写新列"的规则。

## 7. 旧路径停用清单(M1 执行)

旧路径保留**显式 410 响应**(`GONE_DECOMMISSIONED`,message 指明替代),不静默 404,不保留可写入的后台入口。

| 端 | 停用路由 | 原机制 | 替代 |
| --- | --- | --- | --- |
| 本地 | POST /api/share/documents/{id}/quick-share;PUT /api/share/documents/{id}/sync-enabled | 设备 manifest 共享 | M4 发布通路 |
| 本地 | POST /api/sync/push-doc、/pull-doc、/check-doc | 通用双向同步写入 | M4 发布/取回 |
| 本地 | POST /api/sync/sync-token | 前端写令牌到 config.json | M4 本地服务统一管理会话 |
| 共享 | /api/sharing/** 全部 | 设备 manifest 共享 | M4 发布通路 |
| 共享 | POST /api/workspaces/{ws}/sync/push、GET …/sync/pull | 通用增量同步 | M4 发布/取回 |
| 共享 | /api/devices/** | 设备注册/游标 | 首版不需要 |
| 共享 | /api/auth/register、/api/teams/discover、/api/teams/**/join-requests** | 公开注册/加入审批 | 管理员预置成员 |
| 共享 | /api/backup/** | 用户级备份上传 OSS | M6 维护者脚本备份 |
| 共享 | POST /api/storage/presign-upload、/presign-download、/confirm-upload(M4 执行) | 预签名直传 OSS,客户端自报摘要,版本缺失时静默 200 | M4 流式上传(POST /uploads)+ 服务端重算摘要 + 发布/提交事务 |

本地 `/api/sync/login`、`/api/sync/workspaces`、`/api/sync/logout` 等**读与认证**路径暂保留(前端现有登录依赖),M4 统一收敛到本地服务会话管理时替换。

## 8. M1 验收对应

| 蓝图 M1 退出条件 | 验证方式 |
| --- | --- |
| 相同内容不代替资料身份 | 模型测试:同 content_hash 可存在于不同资料/版本;查重只返回候选 |
| 两库不混写 | 切库测试:A 库切 B 库后写入只落 B;A 连接已 dispose |
| 权限和版本约束有测试 | UNIQUE(document_id, version_number) 冲突测试;标签关联越权移除被拒测试;版本列表/上传确认的归属检查测试 |
| 旧共享路径不能继续产生组织数据 | §7 每条路径返回 410 的契约测试 |


## M3 修订补充（2026-09-28）

- `GET /api/documents/{id}/versions/working-state`：按需只读比较工作内容与当前版，返回 `state`（`unchanged` / `changed` / `missing` / `unavailable`）和 `message`。不写入对象或版本；前端另有未检查状态。该结果只代表检查时刻，不是实时监视。
- `GET /api/favorites?target_type=local`：仅返回当前个人库的本地收藏；省略参数保留原全部类型列表行为。详情与列表支持收藏、取消及按资料 ID 打开。共享收藏访问权另验。
- 文件夹摘要与归档以同一次流式读取生成，前后核对目录及文件身份；包含隐藏项和空目录，排除 `.teamhub` 管理目录。读取失败、源变化、链接/特殊文件及不安全路径明确失败，工作副本从快照还原。
- 文件夹捕获和解包使用 `SNAPSHOT_MAX_ENTRIES=10000`、`SNAPSHOT_MAX_FILE_BYTES=1073741824`、`SNAPSHOT_MAX_TOTAL_BYTES=2147483648` 初始上限，可配置且须为正值。真实样本及共享端统一限额仍待后续校准。
- 文件夹的 `file_count` / `total_size` 从已保存快照统计；`Version.file_size` 仍为归档字节数。恢复历史继续追加版本，不改写工作副本。
- 个人搜索标签参数为标签**名称**列表；共现建议 `selected` 为标签 **ID** 逗号列表（重复 ID 去重），限当前个人库 organized 资料，默认 8、最多 20 条。

验证与保留限制见 [M3复核与修复记录](../reviews/M3复核与修复记录.md)。


## M4 修订补充（2026-09-28）

- 本地 publish / submit-version 与服务端 publications / versions 写请求要求 `confirmed=true`，缺省 false 拒绝。确认是显式请求意图标记，不替代登录和成员权限校验。
- 发布支持 `force_new=true` 显式另建不同内容资料；2026-10-13 修订后它不再绕过相同实体内容检查。查重返回既有资料入口但不建立本地“已共享”关联。更新不替换组织标签。
- 文件夹 `content_hash` 是规范化清单摘要，`manifest_hash` 是 ZIP 字节摘要；单文件不得提供文件夹归档摘要。`original_filename` 是幂等负载的一部分；未传时以资料名称作为导出名。服务端流式重新核验，而非接受客户端自报摘要。
- `request_id` 在操作者/空间内唯一；同编号不同操作、目标、基准或内容描述返回 REQUEST_MISMATCH。更换上传票据以重传同一请求不改变业务身份。未变内容返回 status=succeeded、code=CONTENT_UNCHANGED 并沿用当前版。
- 个人端共享请求日志保存首次冻结负载与对象，成功后才写 ShareLink；已应用结果重放不倒退关联。提交来源服务器必须与当前配置匹配。
- 指定取回使用 version_id，默认使用资料 current_version_id，不再按最大版本号推测。校验完整后才建档，取回目录必须在个人库工作区内，失败回滚本次新建内容。
- SQLite 使用 BEGIN IMMEDIATE 序列化发布/提交，先校验后提交引用；写锁等待失败可重试。同一基准真实并发仅一个提交成功。
- 初始上传/个人下载上限各 3 GiB，配置项为 UPLOAD_MAX_BYTES / SHARED_DOWNLOAD_MAX_BYTES；共享文件夹采用 M3 同名条目、单文件与累计上限。均待真实样本校准。
- 旧共享文件夹若缺 manifest_hash 且以 ZIP 摘要充当 content_hash，不视作可验证的新格式；必须在副本上迁移或重新确认发布，禁止猜测性改写真实记录。

具体测试、暂存保留/回收、重试界面及部署限制见 [M4复核与修复记录](../reviews/M4复核与修复记录.md)。

## M5 修订补充（2026-09-28）

- 共享标签按关联增删，预检整批移除权限后提交；重复添加不改变原 added_by。写操作在 SQLite 写锁内重新检查身份和空间权限，覆盖真实同时添加同名/不同名标签。
- 新增 GET `/api/workspaces/{ws}/documents/{doc}/tags`，返回贡献者名称及 can_remove；GET `/collaboration-context` 返回当前账号、空间与 can_tag/can_manage/can_deactivate_users。界面能力字段不替代服务端逐请求授权。
- 新共享搜索使用 `tag_ids`（ID 逗号列表，AND），保留旧 `tags` 名称参数；含逗号的自由标签不再被名称分隔符拆开。联想只来自可见且未回收资料，共现叠加名称条件，已选 ID 去重。
- 未回收资料是普通详情、历史、下载及标签操作的共同前提。无空间成员资格返回 404，空间内能力不足返回 403；管理员通过独立 GET `/trash` 查看回收列表，恢复后才能查看内容。
- 永久删除要求先回收，否则 409 TRASH_REQUIRED。先提交元数据删除与审计，再加写锁复查全部空间/历史版本引用并清理对象。文件夹定位使用 manifest_hash；响应含 objects_deleted / objects_retained，保留数量包含其他引用或清理失败，不能视作纯错误计数。
- 账号 DELETE 返回 410。停用与用户更新共用校验，撤销刷新令牌并保留资料/作者；禁止自停用/自降级，保留可用系统管理员。全局账号停用仅系统管理员可操作，空间 admin/owner 只管理空间资料和标签。
- 本地 GET `/api/sharing/context` 额外提供当前 server_url；共享页全部协作请求经本地适配层。工作空间切换校验远端可见性后持久化，清空选择受支持，登录恢复保留有效选择。
- 共享收藏仍保存个人库内引用；同服务器/空间/资料重复收藏复用记录。新增 GET `/api/favorites/{id}/resolve`，必须匹配当前服务器并按当前账号重新鉴权；不向收藏自带 URL 发送令牌。回收、失权或离线不自动删除收藏。

验证、账号令牌边界及未实施的暂存回收见 [M5复核与修复记录](../reviews/M5复核与修复记录.md)。

## 独立存储区域补充（2026-09-29）

- Document.storage_area 标记 library/pending；旧记录自动兼容。storage_path 保持逻辑相对路径，pending 的旧前缀由解析器移除后拼接独立 pending_path。桌面打开使用响应中的 working_path，不能直接拼接 library_path。
- POST `/api/settings/storage/preview`、`/migrate` 接收 area、destination；GET `/api/settings/storage/migration` 查询状态，POST `/api/settings/storage/recover` 处理恢复。
- 迁移按登记对象构建清单，执行时重新检查冲突、对象重叠及区域边界。文件库迁移保留数据库关系并重写版本对象绝对路径；待整理迁移只移动工作资料。
- `.teamhub/storage-migration.json` 保存指纹和阶段。提交前恢复取消任务并保留副本；提交后验证目标再清理来源。旧日志支持启动入口重定向。
- 迁移锁约束本实例请求；不承诺阻止外部编辑器或其他进程修改文件。库外待整理目录需单独备份，现有维护备份范围未自动扩大。

## M6 修订补充（2026-09-28）

- 维护者备份工具统一使用 `backup-personal / restore-personal / backup-cloud / restore-cloud` 子命令，要求 `--services-stopped` 明确停写前提；不自动停止进程。
- 清单格式 2 包含 kind、format_version、时间、逐文件 SHA-256/大小和目录集合；个人另含源库根。旧无内容清单拒绝自动恢复。备份源/目标不重叠，不接受链接或特殊文件。
- 个人备份含数据库、受管对象、回收站、库内工作文件及非凭据配置白名单；不含库外 index 原文件、凭据、应用 profile、日志和未完成发布暂存。共享备份含数据库与受管对象，清除刷新会话；不含 JWT 环境密钥或上传暂存，密码散列作为账号数据保留。
- 恢复前检查全部清单、SQLite 完整性/外键、每个版本对象；恢复不改变版本号/当前版指针。个人版本路径按受管对象相对路径重新绑定，不做字符串替换。
- 恢复仅接受全新/空目录，共享目标 DB 及 WAL/SHM 必须不存在。个人目录验证后发布；共享对象先准备、数据库最后排他发布，失败清理本次自有输出。断电跨目标原子性未承诺。
- 本地 `/api/health` 增加可空 instance_id，仅用于桌面确认本次自有后端实例，不作为鉴权令牌。preload 在渲染入口前获取真实端口；桌面个人服务仍绑定回环地址，桌面模式允许 file:// 的 null 来源。
- 生产 HTML 注入 CSP meta；仅 self 脚本，不允许 eval/内联脚本。默认开发命令使用隔离启动器，打包脚本在生成 .app 前重建 Python 与前端产物。

维护命令、升级副本与回退要求见 [MVP运维与交接](../guides/MVP运维与交接.md)；实测平台和证据见 [M6复核与修复记录](../reviews/M6复核与修复记录.md)。

## 已整理集合与导航关系（2026-09-29）

- POST /api/documents/{id}/move-to-folder 接收非空相对 target_dir，将已整理受管资料归入或改归另一分类目录；复用移出文件夹的移动与回滚逻辑。分类选择和新建接口共用边界校验，排除系统目录、待整理区、链接路径及整体资料内部。

- 本地分类查询和收藏查询只返回 organized；新增收藏也要求 organized。离开该状态时保留历史收藏引用但隐藏，恢复为 organized 后再次显示。共享收藏的远端引用规则不变。
- SearchRequest 新增 favorites_only，和名称、标签、目录、类型及分页排序共同执行服务端筛选。目录前缀按字面匹配，不把下划线或百分号当作通配符。
- POST /api/documents/{id}/remove-from-folder：只对受管且已整理资料执行，移动到库根目录，保留资料 ID、状态、标签、版本和收藏。同名目标拒绝覆盖；文件操作与数据库失败执行原有补偿回滚，不移动历史快照。
- 左侧总览和收藏常驻，文件夹独立分组。原 library_show_flat_view 配置保留兼容读取，新界面不再提供隐藏总览的选项。

### 2026-09-30：历史版本删除

允许删除非当前版本，当前版本不能删除。删除不修改工作文件、其他版本或共享端记录。版本编号不重排、不复用：documents.version_sequence 保存已分配的最大序号，旧库迁移从现有版本最大值初始化。删除记录会写入操作日志。底层对象暂不物理删除，本轮解决版本列表过多，不提供磁盘空间回收。

## 10. C0—C7 云端与投递实施补充（2026-10-05）

本节描述本轮落地接口，新增行为优先于前文的旧空间级权限描述。实施与验证证据见 [云端与资料投递验收记录](../reviews/云端与资料投递验收记录.md)。

### 10.1 稳定身份和权限

云端schema `005`、protocol_major `1`。`GET /api/server-info`返回server_id与能力；服务身份随同一服务备份保留。新版启动拒绝旧schema，升级显式进行。个人库可离线工作，桌面云通信由本地服务持有凭据；普通成员网页和管理网页使用同源 `/api/web-auth/login|refresh|logout`。网页访问令牌仅内存，刷新Cookie为HttpOnly、SameSite=Strict，HTTPS时Secure；网页JWT会话族sid逐请求验证，通用桌面刷新接口拒绝web会话凭据。

cloud_documents新增visibility/access_revision；document_access按(document_id,user_id)唯一。`capabilities={read,contribute,manage,distribute,delete}`由服务端计算。restricted资料读取须明确授权，组织管理员不例外；团队viewer仍为写权限上限。受限资料删除暂不开放。

发布体必须显式 `visibility=workspace|restricted` 和 `access=[{user_id,role}]`；role为reader/contributor/manager，创建者自动manager。缺少范围拒绝。`GET/PUT .../documents/{id}/access`管理范围，PUT携base_access_revision防并发覆盖，至少保留一名有效manager。普通详情不返回完整授权名单。所有旧读取面和写入口继续鉴权。

团队成员角色仅接受 `owner/admin/member/viewer`。仅owner可以授予或变更owner角色；转让须先建立另一名有效owner，再降级原owner。拒绝邀请停用账号或向其转让所有权；SQLite写事务内重新核对操作者与有效owner数量，防止并发降级清空拥有者。此团队角色不替代资料级ACL，组织管理员仍无受限资料默认读取权。系统复验见[2026-10-05测试报告](../reviews/MVP系统级测试报告-2026-10-05.md)。

### 10.2 固定投递

云端前缀 `/api/workspaces/{ws}`：

| 方法/路径 | 输入或返回 |
| --- | --- |
| GET /delivery-members | 当前组织有效成员id/display_name，支持q |
| POST /uploads?filename=...&purpose=delivery | raw bytes，票据绑定当前用户、空间、用途 |
| POST /deliveries | request_id、recipient_ids、note；上传票据+摘要+original_filename+doc_type，或source_document_id+source_version_id，二选一 |
| GET /delivery-requests/{request_id} | 本人固定请求的当前结果，撤回不复活 |
| GET /deliveries?box=inbox\|outbox&page=1&page_size=20 | 当前账号的分页收发记录 |
| GET /deliveries/unread-count | count；提醒展示不标已读 |
| GET /deliveries/{id} | 附件详情，无其他收件人名单或源资料历史 |
| POST /deliveries/{id}/read | 当前收件人幂等已读 |
| POST /deliveries/{id}/recall | 限发送者，整次撤回，幂等 |
| GET /deliveries/{id}/items/{item}/download | 明确收件资格、有效成员、未撤回；校验后再次鉴权，固定快照流式传输 |

DeliveryItem独立引用受管对象，源资料删除不会级联删附件。共享版本和投递附件统一参与配额和物理对象引用检查。未确认长期留存方案前，不自动过期或清除已发送附件；失败暂存不作为成功投递。

本地适配前缀 `/api/deliveries`：members、unread-count、列表、详情、read、recall、download；`POST /send`需要doc_id、可选version_id、workspace_id、recipient_ids、note、request_id。未选历史版本时保存工作内容并冻结。`POST /{id}/items/{item}/intake`携workspace_id/request_id，仅显式操作才建立待整理对象；不建立可提交源资料的ShareLink。`GET /operations`列出当前服务/账号/空间未完成请求，`POST /operations/{request_id}/retry`核对固定负载并恢复。

### 10.3 本地更新和来源

新增VersionSource记录共享/投递来源，RemoteOperation持久记录固定输入、状态、结果及恢复日志。服务、账号、空间、请求组成幂等身份；同键不同负载拒绝。ShareLink的server_id/account_id/base_version_id/observed_version_id各自承担身份、提交基准和观察作用。Favorite也按核实后的server_id区分服务。

- `POST /api/sharing/update-local`：doc_id、version_id、request_id、confirmed；执行基石§8.8全部当前快照/工作内容/远端目标组合。
- `GET /api/sharing/links/{doc_id}/remote-status`：观察云端当前版，不推进提交基准。
- `POST /api/sharing/links/{doc_id}/confirm-base`：明确冲突处理后的version_id与confirmed；不会自动合并内容。
- `GET /api/sharing/operations/{request_id}`：当前账号/服务/空间下的操作状态。
- 提交历史内容遇OLD_CONTENT_CONFIRMATION_REQUIRED后须明确confirm_old_content=true；仍校验云端基准。

更新前下载校验，文件切换前持久日志和工作修改保护；启动补偿无法确认外部编辑时保留双方并标记待核对。恢复历史版本本身不造新版；目标内容只匹配更早历史且不同于当前快照时，远端接收仍产生新的本地接收版本。

### 10.4 双向实体查重修订（2026-10-13）

本节替代此前允许 `force_new` 绕过内容查重的行为；不改变相同哈希不代表相同资料身份的原则。

个人端 `POST /api/sharing/pull` 与投递附件 `POST /api/deliveries/{id}/items/{item}/intake`，在远端资料／附件鉴权后、下载和建立新资料前，检查当前个人库中 pending／organized 对象的实际工作内容。实体一致返回 HTTP 200：

```json
{
  "status": "duplicate",
  "duplicate": true,
  "code": "DUPLICATE_CONTENT",
  "message": "本地已存在相同文件，未重复收录",
  "document_id": "既有个人资料ID",
  "name": "既有名称",
  "storage_path": "已有工作位置",
  "document_status": "pending或organized",
  "duplicate_document": {
    "id": "既有个人资料ID",
    "name": "既有名称",
    "status": "pending或organized",
    "storage_path": "已有工作位置"
  }
}
```

页面将其解释为“已存在”，提供已有资料入口，不显示“取回成功／收录成功”，不创建文档、版本、ShareLink 或新来源。已完成同请求的投递收录仍先返回原幂等结果，不将重试改为另一次收录。

云端 `/api/workspaces/{ws}/duplicates` 与 `/publications` 仅匹配同空间、当前调用者可访问、未回收资料的**当前**版本。file／archive 都按完整实体字节摘要比较；folder 独立按规范清单摘要比较。正式 publication 仍在 SQLite 写事务中复核，预检不是唯一防线。命中返回 HTTP 200，`status=failed`、`code=DUPLICATE_CONTENT`，并携 `duplicate_document={document_id,name,doc_type,current_version_id,current_version_number,total_size}`。幂等重放仍复核候选资料即时访问权限。

个人发布适配层对上述结果及上传前预检，统一返回 HTTP 409 的 `error` 信封，`details` 携 `document_id`、`duplicate_document`、`workspace_id`、`request_id`。没有成功发布就不建立 ShareLink，不自动绑定查重命中的资料。

预检后若正式提交因并发命中重复，本地冻结操作记为确定失败并保存结果，不留作“结果未知”的 pending；同请求重试只查询原云端发布结果并重新鉴权，不重新上传或更换内容。网络中断等结果未明情况仍保留待核对状态。投递恢复中即使已经下载并记录恢复负载，也要检查后来进入本地库的实体；命中时不再生成第二份资料，保留已有恢复文件且将确定重复从“未完成操作”中排除。

云端命中候选以及 `CONTENT_UNCHANGED` 沿用当前版本前，需核实该当前快照对象仍存在、为正常文件且大小与摘要正确。失真时明确返回存储错误，不提示“已存在／内容未变成功”，不删除旧记录或擅自修复对象；仅检查命中对象，不扫描整个云仓库。

首次发布先捕获实际工作内容。重复预检命中时不新增本地内容版本；确需发送的新内容使用同一捕获生成版本和冻结请求，重试不改为后来编辑的内容。相同当前内容的版本提交继续返回 `CONTENT_UNCHANGED`，不追加云端版本；更早历史命中不自动恢复，不绕过基准冲突。

本地读取使用实际大小筛选及 SHA-256，核对读取前后的文件身份、大小和修改信息；不能仅使用历史快照或过期缓存。缺失／回收对象不报存在，读取异常明确中止，检查不扫描未接管的磁盘目录。已有重复资料清理与底层无引用对象回收不属于本轮操作。

### 10.5 共享资料库选择与发布目标（2026-10-14）

UI 不再要求手动绑定工作空间。底层 workspace ID 及 `POST /api/sync/workspace` 保留兼容；此接口保存当前访问选择，空 ID 清除选择，不改变任何 ShareLink。选择前验证资料库访问权，返回成功后客户端才更新当前库。迟到登录、选择与状态结果须核对服务、账号及个人库上下文；本地配置写入失败不得报告选择成功。

`GET /api/sync/workspaces` 返回 `{teams, workspaces}`，每个库含 `id/name/team_id/team_name`，覆盖当前账号实际可访问的组织。加载失败保留可识别错误，不返回空列表冒充无库。`POST /api/sync/login` 先核实服务身份，成功返回账号、组织、库列表及 `server_id`，清除旧账号的活动库；`GET /api/sync/status` 返回服务身份而不暴露令牌。客户端的上次库 ID 按 `server_id + user_id` 保存，仍需按当前权限验证。

新发布的目标由对话框传入 workspace_id，不强制修改浏览选择。更新原资料从 ShareLink 读取原位置，新增 `GET /api/sharing/links/{doc_id}/target` 在服务、账号、资料读取权限核实后返回 `workspace_id/workspace_name/team_id/team_name/shared_document_id/shared_document_name/can_contribute`，响应禁止缓存；已有离线 `GET /links/{doc_id}` 保持本地查询行为。

`POST /api/sharing/submit-version` 可携 `expected_workspace_id/expected_document_id`，用于阻止窗口打开后原关联变化导致目标误判。请求重试仍固定原负载，不因浏览或对话框选择变化改投。UI 的管理选择不写入普通当前库；权限仍由服务端逐操作执行，选择状态不授予权限。

### 10.6 桌面云会话连续续期（2026-10-15）

`POST /api/auth/refresh` 输入 `{refresh_token}`，成功响应必须包含新的 `access_token`、新的 `refresh_token` 和 `token_type=bearer`。旧刷新令牌通过条件更新消费一次，新刷新令牌以摘要入库，二者在同一数据库事务内提交；签发或保存失败则回滚。继承原设备归属，校验账号仍启用，桌面刷新接口拒绝网页 Cookie 会话令牌。

个人服务遇到上游 401 时合并并发续期，仅在完整新凭据成功原子写入原个人库后重试原操作。缺少新刷新令牌的成功响应属于协议错误，不能继续复用已撤销值。网络、限流及暂时服务故障返回可重试错误并保留云会话；明确刷新鉴权失败，且服务、原配置路径及凭据对仍匹配时，只清除该云会话的凭据和活动库。

明确终态为 HTTP 401，响应 `detail.error.code=CLOUD_SESSION_EXPIRED`、`message=云服务登录已失效，请重新登录`。普通业务 401、403 或网络错误不代表此终态。前端逐请求捕获无凭据的会话代次，只有终态且代次仍匹配时才清云身份、旧资料与预览并停止投递轮询；显示重新登录入口，成功后返回原页面。旧请求不能清理新账号、服务或个人库后的会话，个人资料及共享来源记录保留。

登录、退出、续期、设备信息与设置的配置读改写使用统一会话锁，并固定捕获的配置路径；切库和迁移与续期遵守统一锁顺序，避免复制或覆回已撤销的旧凭据。详见[本轮修复验收](../reviews/云端会话连续续期修复验收-2026-10-15.md)。

### 10.7 个人 AI 辅助整理（2026-10-15）

第一期调用 DeepSeek 格式的 `/chat/completions`，默认 `deepseek-flash`、非思考模式、JSON 输出。个人后端负责内容读取、范围限制、密钥与结果校验。共享端不参与调用，无新增数据库表或 schema 迁移。

| 本机接口 | 语义 |
| --- | --- |
| `GET /api/ai/settings` | 返回 provider/base_url/model/has_api_key/key_storage/can_persist_key，不返回密钥 |
| `PUT /api/ai/settings` | 保存服务、模型及可选新密钥；省略密钥保持原值；clear_api_key 停用并清理；改变服务地址须提供新密钥或清除 |
| `POST /api/ai/test` | 发送简短测试消息；有费用，不发送资料 |
| `POST /api/ai/documents/{id}/prepare` | 本机读取登记的 pending/organized 单文件，返回 preparation_id、document_id/name、content_hash、text、truncated、notes、expires_in 及准确 service={base_url,model} |
| `POST /api/ai/analyze` | 仅接受 preparation_id 和严格布尔 consent=true；发送对应准备内容，返回匹配的 ID/hash、summary/tags/model/notes，不直接修改资料 |

本机路由同时校验请求 Host、来源、loopback 客户端和 `X-TeamHub-AI:1`。Electron 使用每次桌面实例生成的 32 字节随机凭据，经子进程环境 `TEAMHUB_AI_CAPABILITY` 交给后端，受信任主 frame 的 IPC/preload 提供给界面；请求携带 `X-TeamHub-AI-Capability`，后端恒时比较。`Origin:null` 本身不作为身份凭据。此随机值不放入 health、普通配置、磁盘状态或日志；后端自动重启沿用本次桌面值，不同桌面实例不同。

设备配置单独保存，macOS 使用系统钥匙串；普通非支持环境为会话内存或环境变量，沙箱不访问真实钥匙串。非敏感 JSON 使用原子写和 0600 权限，只保存受保护凭据的引用；不进入资料库备份。验证错误不回传请求 body，上游错误也不原样透传，避免密钥泄漏。

准备记录仅有界保存在个人进程内存，最多 16 条、有效期 600 秒。哈希与提取使用同一份至多 10 MiB 的字节；只发送展示的名称和至多 12000 字符正文，不发送全库标签词表或原始二进制文件。记录绑定库、资料身份、工作内容和服务/模型；调用前后复核，变化返回 409。已有名称仅在本机匹配，AI 不执行正文指令、工具调用或写文件动作。

解析保护：文本安全解码；DOCX/XLSX 检查 ZIP 路径、链接、重复、条目、展开大小和 XML，拒绝 DTD/entity，不执行宏、公式、外链。XLSX 最多 10 表、每表 200 行、50 列；PDF 使用 pypdf 上下文限额，最多前 20 页，不支持扫描或加密文档；达到读取限额返回截断说明，解析失败明确报错。

付费请求进程内最多一个；不自动重试付费 POST。上游密钥/权限、余额不足分别返回固定错误；限流 429、服务不可用 503、超时 504、非法输出 502。成功结果按同一准备任务复用；付费失败后作废任务，已提前取得旧任务的并发请求也必须在获得调用许可后复核，不能再次发送。用户须重新准备、再次确认才能重试；关闭等待不保证撤销已产生的上游费用。

前端按资料、个人库和请求代次丢弃迟到结果，展示准备响应绑定的实际服务；任何分析失败重新读取并再次同意。建议标签默认不选，选择后只填入当前编辑区，保留已有标签并经原保存动作确认；摘要仅参考，不覆盖描述，不创建内容版本。

### 10.8 个人资料内容索引（2026-10-15）

这一步独立于 §10.7 的临时工作文件摘录。新增 `document_indexes`、`index_texts` 派生表，以及 FTS5 trigram 全文表和维护触发器；启动时幂等安装于原个人数据库，不删除、重建原资料表。`document_indexes` 每个文档一条，保存当前版本 ID、内容哈希、解析器版本、任务 generation、状态、原文结果和独立 AI 结果。外键随文档及版本清理。

后台单 worker 发现 pending/organized 资料的缺失或过期索引，持久记录 queued/processing/ready/partial/failed。先在受跟踪的短数据库会话内认领、读取受控快照并验证哈希，离线解析后新开会话；提交时原子比较资料库绑定、文档状态、当前版本、哈希和 generation。重启恢复中断任务；已知解析失败等待显式重建，不无限重试。资料库切换时禁止把前库结果写入后库。

输入来自 `.teamhub/objects/<hash前两位>/<hash>` 的不可变快照，格式由版本原始文件名判定。允许配置库根与其规范路径的系统别名；拒绝库内目录或对象符号链接、路径越界和哈希不符。32 MiB 读取限额、500 PDF 页、5000 单元、100 万字符及既有 Office 安全解析限额共同限制工作量。PDF 在物化内容操作和提取文字前保守计数词法项，每流最多 5 万、每页累计 10 万；检查实际调用的 Form，不因发现首个图形就跳过剩余检查。整体文件夹本轮不递归解析。

通用规则位于 `backend/app/skills/document-index/SKILL.md`，当前 schema/skill/parser 版本均为 1。`units` 包含 id/kind/text/location/label/context；overview 标为原文摘录。coverage 含 total/processed/failed/unit，满足 processed+failed=total，描述已读取文字或结构，不能代表视觉理解。gaps/notes 明确未处理项，缺口最多 1000 条，超出部分聚合计数。内容不执行宏、公式、指令或外链。输出由代码校验；原文与 AI 推断分开存储。

| 本机接口 | 语义 |
| --- | --- |
| `GET /api/ai/indexes/{id}?offset=0&limit=20` | 当前资料索引、状态、版本、覆盖、缺口、分页原文和有效 AI 线索；limit 最大 100 |
| `POST /api/ai/indexes/{id}/rebuild` | 清除派生结果和 AI 线索，提高 generation，持久排队重建 |
| `POST /api/ai/indexes/{id}/prepare` | 仅本机准备：当前 ready/partial 索引最多 32 完整单元，展示负载最多 12000 字符；返回确切文本、服务、版本、哈希、过期时间和覆盖说明 |
| `POST /api/ai/indexes/enrich` | 接受 preparation_id 和严格 consent=true；发送所展示名称及 JSON 文本，校验并保存独立 AI 概览和带 unit_id 的检索线索 |
| `POST /api/documents/search` 的 `include_content=true` | 在既有名称/描述条件中加入当前原文和有效 AI 线索，保留区域、分类、标签、收藏等过滤；默认 false 兼容旧调用 |

以上索引路由与启用正文的搜索均校验 §10.7 的本机来源和 Electron capability。AI 使用现有设备设置及单并发付费调用锁，不自动外发全库。准备记录最多 16 条、600 秒，绑定资料库、文档、版本、内容哈希、generation、整理元数据及服务。调用前后复核，保存时再次原子比较；版本、重建、元数据或配置变化导致旧结果拒绝。失败消费准备任务；成功重复提交复用结果。

AI 只返回 overview/clues；clue 的 unit_id 必须来自实际发送单元，不得新增伪造引用。最多 32 条线索、每条 6 个词、每词 40 字。processed_units 保存本次参考单元数。修改标签、名称、描述或位置后，元数据指纹不匹配的 AI 线索在展示和搜索中立即失效，正文保持可搜。

搜索在读取时联结当前文档状态、当前版本、哈希和解析器版本，旧版、回收站及失效 AI 结果不可命中。三字符及以上使用转义后的 FTS 字面短语，一至两字符使用参数化 LIKE；不是向量相似度或自然语言问答。首步仅适用于个人库，云端协议和权限未扩展。

### 10.9 AI 智能伙伴（2026-10-16 建立，2026-10-18 更新；历史兼容）

**兼容与历史说明：** §10.9—10.10 保留早期接口及首次 Hermes 集成细节。2026-10-19 新聊天界面采用 §10.11 的直接发送、原生历史和恢复契约；逐轮 prepare、逐段确认、默认深度思考、不保存原生协议字段、18000 字预览清空、未知引用整轮拒绝及空搜索暂停均不再描述新流程。未被替代的资料工具和业务边界继续适用。

依据[基石 §10.7](../product/TeamHub项目基石.md#foundation-ai-partner)及[实施蓝图](../product/AI智能伙伴实施蓝图.md)，2026-10-18 起默认采用独立 Hermes 引擎执行模型与工具的多轮循环，详细进程契约见 §10.10。首版 PydanticAI 实现保留为历史兼容与对照代码。伙伴独立于此前标签辅助弹窗；会话、消息、任务、授权、来源与资料版本仍由个人服务管理。

`partner_sessions` 保存标题及时间；`partner_messages` 保存用户问题、最终回答及可信的资料依赖；`partner_tasks` 保存会话、请求编号、准备凭据、状态、授权输入、步骤、回答、来源和成果。表通过个人库初始化幂等建立，不替换旧资料表。密钥、原始图片、原始思考和 SDK 原始工具消息不写入这些表。

所有接口统一前缀 `/api/ai/partner`，沿用 §10.7 的本机来源、Host、loopback、自定义 header 及桌面 capability 校验。

| 方法与路径 | 请求 / 响应 |
| --- | --- |
| `GET /budget-policy` | `{default_mode,presets,bounds}`，返回实际默认值、预设及自定义字段边界 |
| `GET /sessions` | `{items:[session]}`，session 含 id/title/created_at/updated_at/active_task_id |
| `POST /sessions` | `{title?}` → session |
| `GET /sessions/{id}` | `{session,messages,tasks}`，消息含 role/content/sources；资料依据绑定当时固定版本 |
| `DELETE /sessions/{id}` | 删除会话及记录；运行中的会话先停止，已收录成果不被删除 |
| `POST /sessions/{id}/prepare` | `{message,scope_mode:"selected"或"library",document_ids:[],budget?,thinking_mode?:"enabled"或"disabled"}` → preparation_id/session_id/message/scope/history/context/service/thinking_mode/budget/limits/notes/expires_in |
| `POST /sessions/{id}/turns` | `{preparation_id,consent:true,request_id}` → HTTP 202 task；同编号、同准备重取原任务，不再次调用 |
| `GET /tasks/{id}` | task 含 id/session_id/state/message/answer/answer_preview/steps/sources/artifacts/error/budget/limits/usage/segment_usage/progress/pause/created_at/updated_at；不返回内部 checkpoint |
| `GET /tasks/{id}/events` | 本机 capability fetch SSE；`event: task` 的 data 为完整 task JSON，终态后 `event: done` 并关闭；只读订阅不启动或重发模型任务 |
| `POST /tasks/{id}/prepare-continue` | `{budget?,thinking_mode?:"enabled"或"disabled"}` → 准备信息及 task_id/continuation:true/progress/continuation_context；展示再次发送的阶段分析、完整保留文字和草稿；未传思考方式沿用原任务选择 |
| `POST /tasks/{id}/continue` | `{preparation_id,consent:true,request_id}` → HTTP 202 同一 task；新预算经明确确认后才启动，重复请求复用结果 |
| `POST /tasks/{id}/cancel` | 停止本轮，幂等返回任务状态 |
| `POST /tasks/{id}/artifacts/{artifact_id}/save` | `{consent:true,request_id}` → `{document_id,artifact_id,duplicate}`，确认后新成果进入待整理 |

问题最多 8000 字，所选资料最多 100 份；整库范围最多 10000 份，在本机冻结文档 ID、当时当前版本、哈希和元数据指纹。准备有效期 600 秒，内存最多 16 条；预览最多列出前 100 份，明确总数和截断。默认空 selected 可通用交流，不能读取资料库。确认说明问题、历史、目标服务、授权范围和一次任务多次调用性质；新资料不自动扩大已有范围。

完整原始历史保留在本机。`history` 为最多近期 16 条、18000 字的完整消息；`context={summary,evidence,sources}` 展示实际接续内容，其中 summary 是本机按规则挑选的较早原文完整句段、消息 ID 及省略说明，最多 12000 字，不调用模型或冒称语义总结。`evidence`、`sources` 从最近保存了可核验检查点的完成任务中接续仍有效的文字及表格证据，最多最近 100 个来源、60000 字完整原文单元，保留原来源编号；超限明确说明省略，相关文档的整体游标与已读声明清除，不以短摘录伪造全文。图片字节不跨轮带入，视觉细节需重新读取。普通新轮不继承旧草稿、图像交付计量和旧任务 usage；暂停续做仍保留原任务的证据、草稿、图像累计计量与用量。

范围、版本、状态或元数据已失效的旧资料及依赖消息均不带入。准备与确认复算完整历史及检查点指纹，实际 history/context 或依赖变化即要求重新准备。历史的资料依赖和工具实际暴露对象保留到任务校验：每次模型外发前及最终短写事务中，核对历史来源、初始预览、搜索候选/摘录和实际读取对象仍属于冻结范围且版本、状态与元数据有效。回答引用和业务依赖分别处理，搜索线索不冒充已读原文。更换资料库引擎、规范路径或 AI 配置修订后终止旧任务；调用期间不长期持有数据库会话。

`thinking_mode` 按轮冻结，默认为 enabled，与任务预算及全局 AI 设置独立；disabled 对应快速回答。DeepSeek 适配发送 `thinking:{type:"enabled"|"disabled"}` 及 `reasoning_effort:"high"`，本段原生工具链由 Hermes 保留相应 `reasoning_content`；原始思考不展示、不落库、不跨轮发送。其他服务不强加 DeepSeek 字段。单请求输出上限仍服从本段剩余预算，思考模式上限 32000 tokens、快速模式为 6000。宿主计量每次真实请求，预算耗尽时保留最后完整工具边界，不另行调用模型生成阶段总结；历史 PydanticAI 的预留总结策略不再用于默认引擎。准备上下文无需额外模型请求或收费。

实际模型请求通过宿主代理接收 SSE，Hermes 从正常回答流提供 Markdown 预览，无需模型调用结构化 `final_result` 工具。`answer_preview` 仅在生成时保存在进程内存，最多 18000 字，不写入任务结果或正式消息，终态先清除；正式回答仍须通过来源 ID、资料版本与依赖核验。上游响应逐块校验并按解压后累计大小限制，取消即关闭；供应商返回合法非流式 JSON 时只转换该次响应，不额外重发。

任务事件只在变化时发送完整 JSON，无变化时每 15 秒 heartbeat。前端以 fetch 携带本机 capability 订阅，45 秒无活动中止连接并回退 `GET /tasks/{id}`，每次成功后间隔 1 秒继续查询，单次查询超时 30 秒；查询失败提示手动重新获取。事件读取在同一激活锁内检查库绑定并建立短数据库会话，推送前再核对绑定；等待不占数据库会话，切库、会话及操作代次隔离旧响应，断流不取消后台任务。

宿主工具为 `search_documents(query,limit,offset)`、`document_overview(document_id)`、`read_document(document_id,offset,limit)`、`read_table(document_id,sheet,start_row,row_count,offset)`、`read_visual(document_id,pages)`、`read_embedded_images(document_id,image_ids)` 和 `create_draft(name,content,source_ids)`。模型自行选择顺序，搜索候选每次最多 10 份并可翻页；正文默认 60、最多 100 单元，单次仍最多 16000 字。表格默认读取 80 行，允许 1—200 行，每个行块保留坐标及固定来源；超长行通过 `next_call` 的块内 offset 继续，不能简单跳过到下一行。结构总览不作为已读全文。视觉读取接收受管快照字节，PDF 任意选中页面均渲染原图；每次最多 3 页、每页最多 400 万像素，受子进程 CPU、15 秒超时、输出大小及取消守卫约束。渲染子进程入口在主应用、配置和数据库导入之前分流，不能隐式启动第二个资料服务。

格式扩展后的 `document_overview` 增加 `visual_assets:[{image_id,label,location}]`、`visual_asset_count`、`visual_state`、`visual_notes`。资产列表最多 300 项，达到上限明确说明后续未列出；只枚举真实内部引用的支持位图，不返回归档成员路径，也不下载外链或读取未引用媒体。`read_embedded_images` 接受 1—3 个不重复的实际 `image_id`；返回 metadata（固定文档/版本/哈希、read_image_ids、asset_count、images、notes、progress）和真实 PNG，经 SDK 作为图像输入给同一模型。images 中含 image_id/source_id/label/location/width/height，Word 用 paragraph+image_index，工作表使用已有锚点+image_index，演示文稿使用 page+image_index；无真实锚点时保留image_index并说明。Word 图片编号不能当作原页码。PPTX/ODP 的 page 表示幻灯片顺序。单元格行块按解析所得 cells 能力分流，支持 XLSX、ODS、CSV 和 TSV，不再按扩展名硬编码。

本机解析器版本为 `2`，索引 schema 与 Skill 主版本保持 `1`。新格式覆盖与限制见使用指引；旧派生索引失效后排队重建，不改写原资料。图片不自动 OCR 入索引。解析出的零文字单元保留具体缺口，伙伴给出检查内嵌图或原页的下一步，不伪造文字或来源。所有未知来源引用仍拒绝。

Office 图像发现另外限制累计遍历 100000 个节点，包含无效引用和逻辑重复呈现；超过时明确报告剩余未读。重复工作表、幻灯片、绘图结构目标拒绝展开，图片实体仍可在多个真实位置复用。ODS 稀疏空白区只统计并推进坐标，非空展开受 50000 格和 100 万字符工作预算约束，超限保留可读前文。正文和图像共用 Word/ODT 位置规则；透明位图叠白底后转为 PNG，避免透明底黑线图失真。

`POST /api/ai/test` 可接受空请求、`{}` 或 `{mode:"connection"|"vision"}`，严格拒绝额外字段，默认连接测试保持旧行为。vision 只生成随机合成 PNG，服务回答由本机比对；返回 `{ok,mode:"vision",message}`，不回传上游原文或用户资料，不保存持久能力结论。沿用本机鉴权、同一服务配置及付费互斥许可；配置在调用过程中变化则拒绝旧结果。仅用户主动触发，网络、权限、余额及识别不匹配保持不同含义。

模型结果为受校验的回答及来源 ID，只能引用真实读取来源。预算每段独立确认，`budget.mode` 为 quick/standard/deep/custom；前三种仅接受 mode，自定义必须同时填写 model_requests/tool_calls/output_tokens/time_seconds 四个严格整数。快速为 12/24/16000/180，标准默认 32/80/48000/600，深入 64/160/96000/1800；自定义边界分别为 4—128、8—256、4000—128000、60—3600。模型输出逐步受剩余预算限制，主循环预留一次模型请求及一部分输出和时间进行阶段整理，整理计入授权上限。单次模型响应解压后最多 512 KiB，单次网络超时 60 秒。请求正文单次最多 16 MiB、单段累计最多 64 MiB，包含工具历史重复携带的图像，超过时发送前拒绝，不虚计为已发请求。单任务视觉工具累计交付最多 24 张或 12 MiB PNG，重复读取也计数，计量写入检查点并沿续做保留。HTTP 客户端不继承环境代理、不跟随重定向、SDK 重试设为 0；错误返回固定安全消息，原始上游错误、密钥和资料不进入日志。

预算原因分别为 model_requests/tool_calls/output_tokens/time_seconds，无进展为 no_progress；达到可保留证据容量为 evidence_limit，明确提示保存成果、缩小范围后新问。实际文本位置、图像页和检索证据参与进展判断；连续 3 次无新增证据后暂停，正常翻页不误判。任务记录本段和累计 usage，包括请求、工具、输入与输出 tokens、执行秒数。模型请求按传输层实际发送尝试计数，超时未返回也不能少算；未完整取得的 token 用量以 token_usage_complete:false、unmetered_requests 明确标记。失败、取消和中断时的本段计量可能不完整，界面显示未知说明并保留此前可靠累计值，不显示为零消耗；供应商未完整返回的用量不能据此承诺固定费用。

暂停保存实际完整已读文字、稳定来源编号、阅读范围、下一位置和实际依赖；上限为 180000 字、1 MiB JSON 和 1000 来源，超限原子回退本次未返回的读取，禁止静默截掉原文后伪称已保留。图像 bytes、密钥、任意路径和 SDK 原始历史不保存。新检查点 schema 为 3，保留 image_usage，并增加 `next_source_number` 高水位，支持过滤后稀疏且稳定的来源编号；兼容通过完整校验的 schema 1/2，解析器及原冻结范围指纹必须一致，恢复后不能让原 S2 指向另一份证据。续做恢复经过校验的证据和阶段分析，阶段推断继续与原文区分，视觉内容可能需要重读。准备时展示的阶段分析和原文须与实际发送一致，准备后进度变化需重新确认。

每任务最多 3 份 .md/.txt 草稿，单份含附录总计不超过 30000 字；固定版来源说明随成果保留。草稿工具只生成任务成果，不写工作文件。任务处于暂停或其他已结束状态时，已有草稿可单独确认保存；保存接口核验成果身份、文件名、内容哈希和实际资料依赖，再复用现有收录、实体查重、快照和初始版本流程。来源依赖核验至收录首提交必须在同一短写事务内，不能允许并发换版穿过校验窗口。重复请求返回原资料；源文件和其版本不变。

正式回答、生成预览、阶段分析及 Markdown 成果经 markdown-it 渲染，设置 `html:false` 并禁用 image，禁止模型 HTML 执行及自动加载远程图片；仅允许 HTTP(S) 链接。Electron 内的显式点击经 preload 桥由主进程核验受信任主 frame 和 URL 后打开。工作步骤默认折叠，来源卡片显示实际 `[S#]` 编号。

状态为 queued/running/paused/completed/failed/cancelled/interrupted，每会话仅一个未结束任务；paused 释放付费许可，但须继续或结束后才能提新问题。与原有 AI 功能共享付费并发许可。启动、切库、目录迁移及迁移恢复统一在数据库激活后恢复遗留状态；已持久的暂停任务保留，同绑定真实运行及提交预约任务保留，旧绑定任务线程安全取消。激活不取得伙伴任务锁或等待模型，避免与切库锁反向等待。暂停阶段不追加正式助手消息，完成后只写一次最终回答；阶段分析独立展示，取消保留已持久的成果。取消由线程可见事件传递到工具与原页渲染器；中断不会自动续费，界面以原请求编号手动核对不确定结果。前端按个人库、会话和操作代次隔离异步返回，旧资料入口不能污染新对话。

首轮仅接入个人库，关键词/正文检索与自主查询不宣称向量语义检索。组织直接检索、任意历史版探索、原件任意编辑和自动发布没有开放；跨轮语义摘要、历史搜索工具、长期记忆及自动学习 Skill 留待后续。使用见[智能伙伴指引](../guides/AI智能伙伴使用指引.md)，历史工程证据见[体验优化验收](../reviews/AI伙伴体验优化验收-2026-10-17.md)、[第一版验收](../reviews/AI智能伙伴第一版验收-2026-10-16.md)、[预算及持续处理优化验收](../reviews/AI伙伴预算与持续处理优化验收-2026-10-16.md)及[格式与多模态验收](../reviews/AI伙伴格式与多模态扩展验收-2026-10-16.md)。

### 10.10 Hermes 本机独立引擎（2026-10-18，历史集成契约）

- 公开 `/api/ai/partner` 会话、授权准备、任务、事件、暂停继续和成果保存接口沿用 §10.9。`prepare.service` 新增并冻结 `engine:"hermes"`、`engine_version:"v0.22.2"`、`engine_protocol:1`；确认时连同模型服务地址及配置修订一起核验。
- 每段已确认任务按需启动一个自有 `teamhub-hermes` 进程，通过有界双向 NDJSON 管道工作。先核对 `ready` 的版本和协议，再发送已确认问题、历史原文、证据和七个工具定义；没有独立监听端口或云端 Hermes 服务。
- 已确认历史作为当前问题中明确标注的 `approved_history` 数据传递，不伪造缺失原始推理状态的模型历史消息。Hermes 仅维护本段真实模型/工具链；跨轮事实来源仍是 TeamHub 复核后的原文与检查点。系统提示只使用 TeamHub 规则，不额外读取本机环境、`AGENTS.md`、个人记忆或其他会话。
- 子进程以 `model_request` 请求模型，以 `tool_call` 请求资料；真实密钥、服务 URL、资料根路径和数据库均留在宿主。宿主对每次请求固定服务目标、模型、预算及思考参数，使用 §10.9 的容量限制、配置/版本守卫和真实计量；重试、压缩等请求也必须经过同一入口。上游失败不自动重发。
- 子进程收到真实原文或图片字节；来源、依赖、检查点和草稿均以宿主实际执行记录为准。任意终端、任意文件路径、插件发现、外网工具、长期记忆、自动学习和后台任务不在本版工具目录中。独立进程不宣称为操作系统沙箱。
- 停止、切库、退出、EOF 或进程错误终止本段工作；宿主关闭模型连接，短限时 TERM 后必要时 KILL 并等待回收。重复取消也必须完成清理，再传播取消。应用重启恢复为中断，不自动重放付费请求。
- 固定来源、归档校验及明确保存规则不变；当前生成格式为 `.md/.txt`。Hermes 上游能力不会自动等同于 TeamHub 已开放能力，Word/Excel 加工需独立工具与验收。
- 开发时通过 `scripts/prepare-hermes.py` 校验固定源码归档 SHA-256、构建独立 Python 3.14.6 环境并按完整哈希锁安装依赖；`npm run hermes:build` 生成独立运行程序。桌面包携带运行环境和上游 MIT 许可，用户只安装 TeamHub，首次使用不下载引擎或依赖。

### 10.11 自然对话与 Hermes 原生会话（2026-10-19 建立，2026-10-20 验收）

依据[自然交互重构蓝图](../product/AI自然交互重构蓝图-2026-10-19.md)。旧接口保留兼容，新页面使用以下契约：

| 方法与路径（前缀 `/api/ai/partner`） | 内容 |
| --- | --- |
| `GET /chat-config` | 返回可显示的 `service`、`service_revision`、`has_api_key` 和 `defaults`；修订值绑定地址、模型及引擎身份，不含密钥 |
| `POST /sessions/{id}/send` | `{message,scope_mode,document_ids,thinking_mode,request_id,expected_service_revision,consent:true,budget?}` → `202 task`，一次完成校验、冻结、注册及调度 |
| `POST /tasks/{id}/resume` | `{request_id,expected_service_revision,consent:true,budget?}` → `202 task`，继续原任务及原范围；服务身份变化拒绝暗中切换 |
| `GET /sessions/{id}` | 原有响应增加 `chat_options`，恢复上次资料范围及思考方式 |
| `GET /sessions/{id}/requests/{request_id}` | 只读返回原请求对应任务；暂未登记返回 404，不创建或重发任务 |

公开 task 增加初始 `request_id` 和最近已接受操作的 `last_request_id`，用于发送、续行响应丢失后的只读对账；私有原生历史不公开。

点击发送即提交可见服务与范围，不再要求另一轮弹窗。相同 request_id 与规范化内容重取同一任务，内容不一致拒绝复用。正在运行的同一会话不能重复启动；已暂停任务可以由新问题结束旧执行状态并保留结果。共享的模型并发许可忙时，新聊天可排队且可取消，不因后台索引正在运行立即失败。

默认普通回复（`thinking_mode:"disabled"`），可开启深度思考。新聊天默认总预算为 64 次模型请求、160 次工具调用、128000 输出 tokens、1800 秒；高级设置允许现有预设或自定义。内部执行段最多 24 次模型请求、64 次工具调用，满足进展和可恢复条件时自动衔接，使用同一总额度、时间及固定范围。达到总预算或无法自动恢复时保留进度并说明原因；不会仅因正常内部执行段切换要求用户操作，也不通过内部续段重置总量。

私有 `result_json.engine_history` 保存校验后的原生 user/assistant/tool 消息、调用配对及必要 `reasoning_content`，最多 512 条、4 MiB。它不进入公共 task DTO、普通日志或可见思考过程。真实密钥、路径和图片字节仍不存入原生历史；含图片或不完整工具序列时回退到文字上下文，按需重新看图。下一轮复用要求服务一致、资料仍获授权、实际依赖版本有效、来源与检查点对应；旧会话没有原生状态仍可继续。

原生 `conversation_history` 与当前自然语言问题分别传给 Hermes，取代在问题 JSON 内嵌 `approved_history`。压缩和其他实际模型请求都经过原网关并计入预算。全库模式不再提前曝光任意前 30 份文件名；校验依赖来自实际搜索、阅读和有效历史。一般用户目标与事实证据分开，证据版本变化时重读，范围收窄时不能重新带入撤销资料。

授权来源使用独立的 `authorization_scope_ids`（资料 ID 集合），保存在任务 `input_json` 和正式助手消息 `sources_json`，继承本轮输入范围及实际采用历史的授权来源。它与精确版本依赖 `history_dependencies` / `dependency_records` 分离：未读取资料的版本变化不应抹掉一般对话目标，范围收窄仍须过滤用户原文、助手转述、旧摘要及原生历史。没有工具调用的助手回答也继承其输入的授权来源，不能以空版本依赖重新外发已经撤销的内容。旧记录缺少该字段时，从原任务范围和已知历史依赖保守核验；无需新增数据库列。

Hermes 的单次响应容量扩大到 8 MiB，回答与部分预览统一最多 64000 字符，旧 PydanticAI 容量单独保留。未知引用移除有效来源外观并说明缺口，保留其余正文与有效引用；版本或权限失效仍不能采纳。空搜索、重复读取与证据容量问题先返回可恢复的工具反馈，由模型调整或收束。

部分回答在取消和可恢复失败时保存在任务结果，公开为 `answer_preview`，界面标记未完成，不写成正式助手消息。已撤销资料、换库和失效版本仍受校验，不能利用保留预览绕过范围。事件流断开采用 GET 退避恢复同一任务。提交响应不确定时保留原 payload 与编号，按请求号只读对账；暂时 404 不代表未执行。期间的新草稿保留，手动重试仍用原编号；不自动重发 POST。已出站且费用状态未知的模型失败不重放，429 等明确错误保持安全原因提示。

Hermes 适配握手新增 `adapter_revision:2`，公开服务身份含 `engine_adapter_revision:2`。旧适配器在任何模型请求前以 `HERMES_ADAPTER_OUTDATED` 明确拒绝；上游版本及传输协议主版本仍为 v0.22.2 / 1，源码归档校验不变。

本节工程证据及最终桌面包见[自然交互重构验收](../reviews/AI自然交互重构验收-2026-10-19.md)。

<a id="contract-ai-preorganize"></a>

### 10.12 待整理 AI 预整理与确认（2026-10-20—21）

产品边界见基石 §4.9；本轮接口表与结构化建议以[AI 预整理实施蓝图 §4—5](../product/AI预整理实施蓝图.md#4-方案与确认)为准。统一前缀 `/api/ai/preorganize`，沿用本机 AI header、Origin/Host 及桌面 capability 检查。

- **数据分离：** `preorganize_policy` 保存当前库启用范围与服务修订；`preorganize_proposals` 保存当前方案、独立状态、来源及 generation；`preorganize_requests` 保存启动收据；`preorganize_operations` 保存采用前后状态、请求身份和撤销时间。首次打开库幂等建表，不删除旧数据库。预整理状态不替代 `Document.status` 或 `DocumentIndex`。
- **授权：** 自动模式默认关闭；按批次或用户主动启用的当前库待整理范围工作。写请求绑定每次库激活的 `library_token`；发起处理还绑定当前服务修订与 consent。服务密钥不存入资料库、方案或引擎进程。后台失效不影响已经生成且资料来源仍有效的 ready 方案供人工采用。
- **来源：** 读取单文件资料的固定保存版本；整体收录的文件夹在调用模型前标明暂不支持，继续提供手工整理。分类语境最多 500 个标签名称和 200 个已有目录，扫描有界且排除受管整体文件夹、待整理和隐藏目录，触顶记录缺口。工具只读当前资料并提交 `propose_organization`，不可检索其他资料、创建草稿或修改文件。
- **任务：** queued/running 内部分相，用户只见四状态；运行中断标失败，不自动重放；取消后同一内容不被自动重新排队。独立队列共享现有付费租约，交互任务优先。每份预算见实施蓝图，达到预算没有有效方案时不能标记成功。
- **方案：** 名称、标签、可选分类、简短说明、关联线索、缺口和来源编号均结构化。模型的来源编号必须来自本任务真实阅读；宿主附加实际覆盖、解析及缩放提示。没有可靠标签可提交空标签建议，用户必须补充后才能整理。
- **采用：** `BEGIN IMMEDIATE` 后重读并比较资料、方案身份及版本/管理指纹，核验工作文件内容与保存版本一致；通过共用移动补偿事务更新正式名称、标签、位置和 organized，保存 before/after 记录，不创建内容版本。批量逐项返回成功或可读错误；相同请求身份与内容重试返回原操作。
- **撤销：** 恢复原名称、原标签 ID/名称、原待整理位置和状态。当前资料与采用后快照不符、原标签已改名/删除、内容变化或目标被占用时拒绝；不覆盖、不清理其他资料可能使用的新标签。撤销操作幂等。
- **界面：** 单份与批量审阅使用固定对象/方案快照；轮询只刷新列表状态和选择，不改正在编辑的内容；切库及关闭后丢弃迟到响应。提交结果不确定时保留原 request ID，避免重复执行。

本轮 Hermes 适配握手为 `adapter_revision:3`（取代 §10.11 中 revision 2），原生聊天仍使用原有工具集与历史协议。上游固定为 v0.22.2、传输协议主版本仍为 1。

本节实现及最终桌面包已于 2026-10-21 完成隔离工程验收，具体测试范围与交付入口见[预整理验收](../reviews/AI预整理验收-2026-10-21.md)。

移动补偿保障进程内异常的数据库回滚与工作文件位置恢复；本轮未将既有资料移动机制升级为断电时文件系统和 SQLite 的跨介质原子事务。日常备份及中断后位置核对仍按既有存储规则执行。工程流程通过不等于真实模型建议准确率或整理时间收益已经得到证明。

### 10.13 R1 资料索引发布与可解释词法检索（2026-10-23）

本节承接 §10.8 的个人库索引及 §10.9—10.11 的伙伴资料工具，更新搜索响应与发布恢复语义；不扩展组织云仓库索引或启用向量语义检索。运行指引见[资料内容索引使用指引](../guides/资料内容索引使用指引.md)，隔离工程验证见[第一阶段验收](../reviews/AI整理与准确检索第一阶段验收-2026-10-23.md)。

**文档搜索。** `POST /api/documents/search` 在 `include_content=true` 且 `keyword` 非空时使用词法检索，继续执行本机来源与桌面 capability 校验。`include_content` 默认 false，空关键词维持列表浏览；现有 `GET /api/documents/autocomplete` 仍为名称联想，不返回下列命中说明。请求继续支持 status、tags、is_folder、favorites_only、folder、page、page_size；先应用既有过滤，再在该资料集合内检索、计数和分页。`sort_by` 增加 `relevance`，同时保留 name/updated_at/created_at/total_size 和 asc/desc；相同排序值按资料 ID 稳定排列。接口的默认排序仍为 updated_at，关键词界面可显式采用相关性排序。

返回的 `DocumentResponse` 增加可选 `search_match`；未进行正文关键词搜索时可为空，不改变既有 documents/total/page/page_size 信封。

| `search_match` 字段 | 已实现语义 |
| --- | --- |
| mode | 固定 `lexical`，不表示向量相似度 |
| relevance | matched_terms、matched_term_count、query_term_count；描述已匹配查询词，不是准确率或置信度 |
| channels | content/name/tag/description/card/note/ai；分别区分保存原文、人工元数据、有效资料卡、人工补充和 AI 检索线索 |
| passages | 每份至多 3 条，每条含 source_kind、excerpt、label，以及可空的 unit_id/ordinal/location；摘录至多 500 字符。只有有可信片段位置的原文才提供 ordinal/location，元数据与派生线索不伪造原文位置 |
| coverage | state/index_state、current_index、complete、processed/total/failed/unit、gaps；存在当前未完成工作时另含 processing_progress={stage,cursor,total_units}。解析覆盖与片段发布进度分别表达，旧 ready 索引回填未结束时 complete=false |

查询最多 512 字符、16 个空格分隔词，控制字符拒绝；FTS 语法、通配符和运算符不作为可执行表达式。三字符及以上由 FTS5 trigram 参数化字面短语选取，一至两字符使用转义 LIKE；保留 Unicode 查询原词。SQL 聚合匹配词和来源权重后排序，优先覆盖较多查询词的资料；所有显式编号词必须命中。纯数字使用数字边界，复合设备编号使用 ASCII 编号边界，例如 5053 不匹配 50530，ZX-2048 不匹配 ZX-20480，2026 可匹配 2026-10-22。原文跨片连接只发生于同一原始位置的真实连续字符；编号核对使用真实邻接边界，不以生成行号、页码或截断桥边缘制造命中。工作表真名从保存的 location.sheet 单独参与匹配，不能从生成位置标签猜测。

**当前身份与兼容回填。** 检索限调用者明确授权、且为 pending/organized 的资料；原文片段同时核验当前 version、generation、内容/来源哈希及 parser 版本。queued、失效版本、旧 generation 和回收站片段不参与召回；processing 已提交的当前片段可先返回。资料卡及人工补充核验各自版本，AI 线索继续核验当前元数据语境。升级旧 ready/partial 索引时，合法的当前 `IndexText(kind=content)` 全文投影在当前回填完成前继续参与检索，避免尾部暂时丢失；该回退返回 source_kind=content，但 unit_id/ordinal/location 均为空，label 标为位置回填中，不能据此生成虚构读取偏移。

**持久片段与工作项。** 新增可重建派生表 `index_chunks` 和 `index_work_items`，启动时幂等安装于原个人 SQLite，旧 chunk 表补齐安全检索段字段。IndexChunk 保存原文单位、ordinal、位置、上下文、版本/generation/来源与正文哈希、parser 版本，以及搜索段的真实边界；唯一键为 document_id/version_id/generation/unit_id。IndexWorkItem 按 document_id/generation 唯一，保存版本/哈希/parser 身份、stage/state、attempt、发布 cursor/total_units、lease/token 与发布中的持久 parsed_result_json；完成后结果由 DocumentIndex.result_json 保留，工作项释放重复产物并保留完成状态。认领采用租约，发布批次与游标同事务提交，同批重放幂等；默认每批 128 单元。重建与版本变化仍使旧 generation 失效，失效任务不能覆盖新结果。

恢复保证始于**完整解析产物已经落盘后**：重启可继续未提交的发布批次，租约未过期时不抢占其他执行者。产物落盘前发生中断仍可能重新读取、解析；本轮不提供逐页解析检查点，也不提高 §10.8 的 32 MiB、500 PDF 页、5000 单元、100 万字符及其他解压/解析限制。`GET /api/ai/indexes/{id}` 继续分页返回状态与原文，可另含 processing_progress；DocumentIndex 的 ready/partial 是解析结果状态，不能单独据此认定片段全部发布。

**伙伴搜索与阅读。** `search_documents` 使用同一授权词法检索，返回 retrieval_mode=lexical、total、has_more/next_offset、匹配词、来源通道、coverage 和 matched_passages。仅 source_kind=content 且 ordinal 为可信非负整数的片段附带 `next_call={tool:"read_document",arguments:{document_id,offset:ordinal,limit:3}}`。搜索命中只是候选，不登记该轮已读来源；后续实际读取、核对授权与固定版本后才形成原文来源。元数据、AI 线索及旧全文回退的空位置不提供伪造 next_call。

上述验收覆盖合成资料中的工程行为与边界，不宣称真实资料检索质量、模型效果或整理收益已经验证。
