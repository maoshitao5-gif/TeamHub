# TeamHub MVP 验证记录

> 依据 [MVP 实施蓝图](../product/TeamHub-MVP实施蓝图.md) §6.4 M0 第 6 条建立。每条记录注明命令、环境、结果与未覆盖内容。
> 本文同时承担 M0 开发过程记录:按时间顺序追加,先记事实再下结论。

| 文档信息 | 内容 |
| --- | --- |
| 建立日期 | 2026-09-23 |
| 阶段 | M0 工程基线与隔离环境(**当日完成,退出条件已满足**) |
| 代码基线 | Git `9921c00`(master 分支;README.md 有未提交修改,docs/ 两份新文档未跟踪,均保持原样) |

## 过程日志

### 2026-09-23 M0 启动与完成

#### 1. 只读盘点(对应 M0-1)

1. Git 基线:`master` @ `9921c00`;README.md 未提交修改、docs/ 两份新文档未跟踪。**未做任何 git 写操作。**
2. 仓库内未发现真实资料资产:根目录无 `config.json`、无 `teamhub.db`,`cloud_backend/` 无 `.env`。根目录的 `python`、`curl` 两个文件为 0 字节空文件,疑似误创建,保持原样未动。
3. 本地后端启动会从 cwd 的 `config.json` 恢复 `library_path`(`main.py` lifespan),因此隔离启动必须控制 cwd。
4. 旧启动脚本问题确认:`kill-ports.bat` 为 Windows 专用且按端口杀进程,不符合 M0-5 约束;`dev-all.js` 未在本轮审查,沿用蓝图附录 B 的记录。

#### 2. 版本与依赖核定(对应 M0-3)

| 项 | 现状 | 处置 |
| --- | --- | --- |
| Python | 系统 3.9.6;另有 3.11.15 / 3.13.13 | **选定 3.11.15**(`__pycache__` 历史即 cpython-311) |
| Node / npm | v26.8.1 / 11.19.0 | 可用 |
| Electron | `^28.3.3` | **已远超官方"最近三个稳定主版本"支持窗口,属 EOL**;升级属于 M6 打包前置事项,本轮不动 |
| 本地后端依赖 | FastAPI 0.115.0 / SQLAlchemy 2.0.36 / pytest 7.4.3 | 安装成功;含 oss2、boto3 残留(首版不需要,未卸载) |
| 共享端依赖 | FastAPI **0.104.1** / SQLAlchemy **1.4.54** / pytest 7.0.1 | 安装成功;**与本地后端版本分叉**,与蓝图 §6.1 记录一致;`email-validator 2.1.0` 为 yanked 版本(pip 警告) |

版本收敛(FastAPI/SQLAlchemy 统一)列入 M1 数据模型与迁移任务,本轮只记录不升级,避免混入无关变更。

#### 3. 隔离环境建立(对应 M0-2)

- 沙箱数据根:`.mvp-sandbox/{personal-a,personal-b,personal-c,shared-server}`,已加入 `.gitignore`(追加方式;该文件注释为 GBK 编码,未做整文件改写)。
- 两套虚拟环境(均未提交,`venv/` 本就在忽略列表):
  - `venv/`(本地后端,Python 3.11.15)
  - `cloud_backend/venv/`(共享后端,Python 3.11.15)
- 隔离证据:沙箱启动后 `find .mvp-sandbox -type f` 显示 `personal-a/teamhub.db(-wal/-shm)`、`shared-server/cloud.db` 均落在沙箱内;仓库根目录未生成 `teamhub.db`/`config.json`。
- 已知轻微副作用:pytest 导入 `backend.app.core.logger` 会在 cwd 创建 `logs/teamhub.log`(已 gitignore);测试本身只写 `tmp_path`,不触碰真实库。

#### 4. 测试发现与隔离修正(对应 M0-4)

- `pytest.ini`:`testpaths = tests` → `backend/tests`(原指向不存在的根目录 `tests/`)。
- 移除 `asyncio_default_fixture_loop_scope` 选项(pytest-asyncio 0.21.1 不支持,产生 PytestConfigWarning;引入异步测试时需先升级 pytest-asyncio ≥0.24 再恢复,已在配置注释中说明)。
- 同名 `app` 包冲突的处置约定:本地后端测试从仓库根目录用 `venv` 执行;共享端测试(M1 起新建)在 `cloud_backend/` 内用其自有 venv 与配置执行,两进程互不导入对方包。

**验证命令与结果:**

| 命令 | 环境 | 结果 |
| --- | --- | --- |
| `venv/bin/python -m pytest backend/tests -v` | 仓库根目录,venv(Python 3.11.15) | **14 passed**(test_pointer.py),警告已消除 |
| `cd frontend && npm install && npm run build` | Node v26.8.1 | **构建成功**(1.72s);公共 JS 块 1.19 MB(gzip 384.77 kB),chunk 体积警告与蓝图附录 B 记录一致 |

未覆盖:共享端尚无任何测试套件(M1 起新建);前端无自动化测试;pointer 测试中的旧语义期望(隐藏文件跳过等)待 M2/M3 按新规则调整。

#### 5. 可重复启动方式(对应 M0-5)

新建 `scripts/mvp-dev.sh`(macOS):

- `scripts/mvp-dev.sh start [a|b|c]`:本地后端以沙箱个人根为 cwd 启动(127.0.0.1:8001),共享后端以显式 `DATABASE_URL` 指向沙箱启动(127.0.0.1:9000)。
- 端口占用时**报错退出,不杀任何进程**;`stop` 只杀 PID 文件中记录的本脚本进程,并校验进程名。
- 修复记录:初版 `$!` 因子 shell 包装记录到错误 PID(停止失效),改用 `exec` 使 PID 直接对应监听进程后复验通过;端口占用守卫复验通过(二次 start 退出码 1,未误杀)。

**验证结果:**

| 检查 | 结果 |
| --- | --- |
| `start a` → `curl :8001/api/health` | `{"status":"ok","library_initialized":false,"library_path":null}` |
| `curl :9000/api/health` | `{"status":"ok","service":"teamhub-cloud"}` |
| 记录 PID 与 `lsof` 监听 PID | 一致 |
| `stop` 后端口复查 | 两端口均无监听 |
| 重复 `start` | 报错退出,未杀占用进程 |

共享后端启动时 OSS 初始化在后台线程失败(无 MinIO),属预期告警,不阻塞健康接口;首版存储路径不依赖 MinIO(蓝图 §3.1)。

#### 6. M0 退出条件核对

| 退出条件 | 状态 |
| --- | --- |
| 测试不会写用户库 | 满足(测试仅用 tmp_path;沙箱外无写入,除 gitignored 的 logs/) |
| 能启动两个隔离后端与界面 | 后端已验证;前端构建通过,界面联调留待 M4+ |
| 基线通过/失败项均有记录 | 满足(本文) |

**M0 完成。初始故障清单(进入 M1 的已知项):**

1. 两套后端 FastAPI/SQLAlchemy 版本分叉(0.115/2.0 vs 0.104/1.4),M1 收敛。
2. `email-validator 2.1.0` yanked,M1 收敛依赖时一并处理。
3. Electron 28 已 EOL,M6 打包前必须升级并复验。
4. 共享端无测试目录;异步测试需升级 pytest-asyncio。
5. `dev-all.js` 及 Windows 批处理脚本不适用于 macOS 开发,日常开发用 `scripts/mvp-dev.sh`;旧脚本是否清理在 M6 统一决定。
6. 根目录 0 字节空文件 `python`、`curl` 疑似误创建,待发起人确认后删除。

---

### 2026-09-23 M1 固定契约与收敛入口(当日完成)

#### 交付物

1. **契约文档**:`docs/MVP数据与接口契约.md`(TH-MVP-CONTRACT v0.1)—— 蓝图 §3 落成具体字段、约束、错误码、接口与停用清单。实施中补充一条过渡策略(§6.2 第 5 条):共享库旧列在新列回填后保留可读,旧写入路径 M4 整体替换后再删列;新代码只写新列。
2. **本地端实现**:
   - `backend/app/database.py`:新增 `rebind_engine()`(dispose 旧引擎、重建引擎与会话工厂、重注册 pragma);`main.py` lifespan 恢复 library_path 后先 rebind 再 init_db;`settings_api.setup_library` 切库时 rebind。修复了蓝图附录 B 的"切库后引擎指向原位置"缺陷。所有 SessionLocal 使用点均为函数内 import,rebind 后自动取新工厂。
   - `backend/app/api/versions.py`:按契约重写并注册(此前未注册,404);GET 列表可用,写操作返回 501 待 M2(不注册有覆盖缺陷的旧实现)。
   - 旧路径停用(契约 §7):`share.py` 两个端点、`sync.py` 的 sync-token/push-doc/pull-doc/check-doc 均返回 410 统一信封(`backend/app/core/decommissioned.py`)。
   - 模型:`documents` +doc_type/current_version_id,`versions` +manifest_hash,新表 `share_links`/`favorites`;幂等迁移 `backend/app/core/migrations.py`(is_folder→doc_type、is_current→current_version_id 含最大版本号兜底、organized→active)。
3. **共享端实现**:
   - 模型(增量过渡式):CloudDocument +doc_type/current_version_id/created_by/trashed_by、id 改服务端生成;CloudVersion +content_hash/manifest_hash/storage_key/author_id/base_version_id 与 UNIQUE(document_id, version_number);标签关联表 +added_by/created_at;新表 `publication_requests`/`admin_actions`。
   - Alembic `004_mvp_contract.py`:全部新列/新表/唯一索引 + 幂等回填;downgrade→upgrade 循环可重复。
   - 权限骨架:`deps.py` 新增 `workspace_member()` 依赖工厂与 `get_workspace_document()` 归属校验;**修复两处已核实漏洞**——`list_versions` 跨空间可读(documents.py:246)、`confirm_upload` 无空间校验(storage.py:76),对无权限者统一 404 防探测。
   - 入口收敛:`router.py` 卸载 sharing/sync/devices/backup,过滤 auth.register 与 teams discover/join-requests;新增 `decommissioned.py` 410 桩,**注册顺序在 teams 之前**——否则 `/api/teams/discover` 会先命中 `/{team_id}` 通配(实施中发现并解决)。

#### 验证命令与结果

| 命令 | 结果 |
| --- | --- |
| `venv/bin/python -m pytest backend/tests`(根目录) | **20 passed**(14 pointer 旧测试 + 切库隔离 1 + 停用 2 + 迁移 3) |
| `cd cloud_backend && venv/bin/python -m pytest tests` | **10 passed**(约束 3 + 权限 3 + 停用 2 + 迁移 2) |
| `scripts/mvp-dev.sh start a` + curl 健康接口 | 双后端正常;410 抽查(本地 push-doc、共享 sharing/publish、auth/register)信封正确 |
| 沙箱 `shared-server/cloud.db` 表清单 | 含 `publication_requests`、`admin_actions`(空库 create_all 路径) |
| 仓库根目录检查 | 无 `teamhub.db` 生成,测试不触碰真实库 |

#### M1 退出条件核对

| 条件 | 状态 |
| --- | --- |
| 相同内容不代替资料身份 | 满足(test_same_content_hash_allowed_across_documents) |
| 两库不混写 | 满足(test_switch_library_rebinds_engine:A/B 库写入隔离,旧引擎 dispose) |
| 权限和版本约束有测试 | 满足(唯一约束、跨空间拒绝、410 契约) |
| 旧共享路径不能继续产生组织数据 | 满足(全部 410,本地侧附带无写入断言) |

#### 遗留问题(带入 M2/M4)

1. 前端仍直连共享后端(cloud.js)并存在三处令牌存储;统一收敛到本地服务会话管理属 M4,本轮仅固定约定(契约 §5)。
2. versions.py 写操作 501,待 M2 以不可变快照语义实现;旧 `is_current` 写入方(整理/收纳流程)在 M2 重写前保留。
3. 共享端旧端点(search/push/snapshot 等)仍读旧列,M4 由发布通路整体替换;`documents.py` 的 push、snapshot 端点届时停用。
4. 依赖版本分叉(FastAPI 0.115/0.104、SQLAlchemy 2.0/1.4)仍未收敛;本轮先在 1.4 上完成模型与迁移,升级需独立步骤并以现有测试为安全网。
5. `sync.py` 中 `_get_or_create_tags`、`_compute_sha256`、`_place_file` 等仅被已停用代码引用的辅助函数暂保留,M4 收敛时一并清理。

---

### 2026-09-24 M0/M1 审查整改复验

> M0/M1 经独立复核发现 8 项问题(见 [MVP-M0-M1-Review与整改清单](MVP-M0-M1-Review与整改清单.md)),当日全部整改并复验关闭。详细修复方式与复验命令见该清单 §8,此处只记结论。

| 项 | 结果 |
| --- | --- |
| M0-R01 沙箱配置隔离 | 关闭:沙箱不读 `.env`、不继承环境变量、跳过 OSS 初始化 |
| M0-R02 停止脚本进程归属 | 关闭:PID+启动时间+端口三重核验,不确认不发信号 |
| M0-R03 启动健康就绪检查 | 关闭:任一服务失败返回非零,只清理本次进程 |
| M0-R04 界面启动验收 | 关闭(浏览器界面级):Vite 200 + 后端基础请求 200 + CORS 200;Electron 壳未验证 |
| M1-R01 个人库版本约束 | 关闭:UNIQUE(document_id,version_number) + current_version_id FK + 归属校验 |
| M1-R02 旧共享写入入口 | 关闭:7 个旧写入入口返回 410,只读接口保留 |
| M1-R03 迁移版本号冲突 | 关闭:建索引前确定性重编号,不删行,子文件版本保留并告警 |
| M1-R04 切库在途会话 | 关闭:在途会话时切库拒绝 409,失败回滚 |

整改后测试基线:`backend/tests` 33 passed;`cloud_backend/tests` 12 passed。

---

### 2026-09-24 M2 单文件可靠个人生命周期（完成）

> 蓝图 §6.4 M2 退出条件：V-01 / V-02 / V-04 / V-10 的单文件场景通过；历史内容摘要逐版不变，源文件未被意外移动或覆盖。

#### 交付物与验证

- 不可变快照对象存储 `backend/app/services/snapshots.py`：流式写暂存 → 校验 → 原子 rename；内容寻址复用；文件夹为确定性 zip。
- 个人版本服务 `backend/app/services/versions.py` + 路由 `backend/app/api/versions.py`：保存新版本、历史列表、指定版本导出、恢复=追加新版本（线性历史，不改写旧版）。
- 收录查重 `documents.py::create_document`：单文件算 sha256，命中返回 DUPLICATE_CONTENT 既有入口，不重复建档。
- 回收站按文档 ID 定位（`file_utils.find_trash_entries`）：删除、恢复、批量删除/恢复、手动清空与自动清理全部统一按 ID + 旧格式兼容。

#### 本轮修复

- 回收站**清理**路径（`trash_cleaner.py`、`settings_api.clean_trash_now`）此前仍用旧 `{时间戳}_{文件名}` 命名，与 M2 的文档 ID 命名不一致，导致清空/自动清理无法删除物理文件；已统一改用 `find_trash_entries`。

#### 退出条件核对

| 用例 | 状态 |
| --- | --- |
| V-01 收录后源文件不动、无需登录 | 通过（test_ingest_copies_source_and_creates_snapshot） |
| V-02 同内容再收录返回既有入口 | 通过（test_duplicate_ingest_returns_existing_entry） |
| V-04 存两版→恢复初版，历史摘要逐版不变 | 通过（test_restore_appends_new_version_without_rewriting_history） |
| V-10 删除本地副本不影响快照 | 通过（test_deleted_working_copy_keeps_snapshot） |
| 同名资料回收恢复按 ID 定位 | 通过（test_trash_restore_distinguishes_same_name_documents） |
| 回收站清理按 ID 定位 | 通过（test_clean_trash_removes_id_named_entries / test_auto_cleanup_removes_id_named_trash） |
| versions 唯一索引迁移 | 通过（test_versions_unique_index_created） |

测试基线:`backend/tests` 35 passed（其中 test_m2_lifecycle.py 8 项）;`cloud_backend/tests` 12 passed。

#### 遗留（带入 M3/M6）

- 跨资料物理去重的清理引用检查（删除前确认无其他版本/资料引用同一对象）留 M6。
- 压缩包按普通文件管理、不解析内容（契约既定行为，非缺陷）。
- 磁盘错误/异常退出已由快照写 tmp + fsync + 原子 rename 的设计规避，未单独做进程崩溃注入测试。

---

### 2026-09-24 M3 文件夹整体快照、检索与收藏（完成）

> 蓝图 §6.4 M3 退出条件：V-03 文件夹场景通过；V-11/V-12 的个人检索、切库与个人收藏部分通过；文件夹异常不静默成功，共享服务不可用不影响私人检索。

#### 交付物与验证

- 文件夹整体快照 `services/snapshots.py`（§3.3）：
  - 确定性清单（manifest，JSON 带版本号）+ 确定性 zip 归档；
  - **内容摘要（content_hash = 清单字节 sha256）与归档摘要（manifest_hash = zip 归档字节 sha256）分离**（此前两者混为同一 zip 摘要，违反 §3.3.6）；
  - 空目录纳入（清单 + zip 目录条目）；应用管理目录 `.teamhub` 排除，其余隐藏项不一律忽略（§3.3.4）；
  - 符号链接（文件/目录）拒绝，不跟随取出目录外资料；
  - 解包 `_extract_zip_safely` 拒绝路径穿越/绝对路径/符号链接（§3.3.5）。
- 版本服务 `services/versions.py` 调用方更新为四元组返回，`VersionResponse` 暴露 `manifest_hash`。
- 收藏 API `api/favorites.py`（契约 §2.5/§5.1）：local 收藏按资料 ID 引用（幂等）、列表附带资料引用、取消、缺失资料 404。
- 个人检索复用既有实现（名称关键词 `search_documents`、标签 AND 组合、`suggest_tags` 联想、`related_tags` 共现），本轮核对无缺口。

#### 退出条件核对

| 用例 | 状态 |
| --- | --- |
| V-03 中文/空目录/隐藏项复原、符号链接拒绝、解包防穿越 | 通过（test_m3_folder_and_favorites.py 6 项文件夹快照） |
| 收藏引用资料、幂等、取消 | 通过（test_favorite_lifecycle / test_favorite_rejects_missing_document / test_favorite_missing_local_doc_id_rejected） |
| V-12 切库、私人检索 | 切库已由 M1 test_library_switch 覆盖；检索不受共享服务影响（本地后端独立） |

测试基线：`backend/tests` 43 passed（新增 test_m3_folder_and_favorites.py 8 项）；`cloud_backend/tests` 12 passed。

#### 遗留（带入 M4—M6）

- shared 收藏真实联调（引用共享服务端资料 ID）留 M4—M5。
- 文件夹样本盘点与限额（文件数/总量/展开大小上限）留 M7 真实样本校准。
- 检索前端的错误/缺失源文件/待保存修改呈现，随 M4/M6 前端联调统一处理。

---

### 2026-09-24 M0 再次复核与修复（最新结论）

仅复核 M0。前次“关闭”仍有 bootstrap 配置恢复、未监听进程清理及界面实际渲染证据缺口；本轮已修复，并补充中文路径进程识别修复。

- 启停专项：6 passed、0 skipped；真实应用配置隔离与 pointer 基线：15 passed。
- 两套 Python 环境 `pip check` 通过；前端构建、Shell 语法及差异空白检查通过。
- 双后端实际运行，健康接口指向 personal-c 沙箱和独立共享库。
- Chrome 实际渲染 TeamHub 页面及沙箱库路径，页面基础 API / 搜索请求返回 200。
- 本次验证服务和标签页已清理；Electron 桌面壳未验证，不评价 M1—M3 完成度。

**M0 本机开发基线通过本轮验收。** 详细改动、复验命令、浏览器证据与使用限制见 [M0复核与修复记录](M0复核与修复记录.md)。此前 HTTP 层界面验收和进程归属说明作为历史记录，以本节及链接文档为最新结论。

### 2026-09-26 M1 再次复核与修复（最新结论）

M1-R01—R04 本轮关闭，证据及命令见 [M1 复核与修复记录](M1复核与修复记录.md)。

- 空库与迁移库均保护当前版本的存在性和资料归属；保留资料内版本号唯一约束。
- 迁移前检查历史歧义，失败时保留数据及原结构。本结论取代 2026-09-24 记录中的“确定性重编号、清理多当前标记”方案。
- 会话创建与切库互斥，关闭计数幂等；切库初始化或配置写入失败恢复原运行库，配置采用原子替换。旧库排队哈希任务不会写入新库。
- 旧共享写入入口停用及权限检查复验通过。
- 本地指定套件 **25 passed**（24 项 M1、1 项 M0 隔离回归）；共享端指定套件 **14 passed**；差异空白检查通过。

未执行用户真实库迁移，未开展 M2—M4 全量测试或界面验收；原始迁移副本与实际恢复演练仍留 M6。未提交 Git。

---

### 2026-09-26 M4 主动共享、取回与共享版本（完成）

> 蓝图 §6.4 M4 退出条件：V-05 / V-07—V-09 及 V-13 的共享会话部分通过。

#### 交付物

- 服务端受管存储 `cloud_backend/app/core/object_store.py`：独立文件目录（暂存 sidecar + 内容寻址不可变对象），流式上传/下载，不依赖 MinIO/OSS；服务端重算 sha256 校验实际收到的内容（不信任客户端声称值）。
- 服务端发布通路 `cloud_backend/app/api/publications.py`：上传 / 发布（幂等 + 查重 + 校验）/ 版本提交（基准版本冲突）/ 下载（三级归属校验）/ 查重（空间内不溢出）；权限与资源不存在一律 404 防探测。
- 本地共享适配层 `backend/app/api/sharing.py`：publish / submit-version / pull / links / 代理下载；本地服务是唯一共享出入口，前端不直连共享后端（V-13）；首次成功后才落 share_links（M4-5）。
- 前端认证收敛：`sync.js` / `stores/auth.js` / `RegisterPage.vue` 统一经本地服务会话管理。
- 旧 OSS 预签名直传旁路停用（`router.py` 不再注册 storage.router）。

#### 退出条件核对

| 用例 | 状态 |
| --- | --- |
| V-05 发布幂等重放、共享不可达不产生「已共享」假象 | 通过（test_m4_publications + test_m4_sharing_e2e） |
| V-07 同 request_id 换负载拒绝；基准过期 STALE_BASE_VERSION | 通过 |
| V-08 跨空间上传/发布/提交/下载/查重一律 404 | 通过 |
| V-09 声明摘要 ≠ 上传字节 → 422，不产生成功记录 | 通过 |
| V-13 共享会话统一由本地服务管理 | 通过（E2E 经 /api/sync/login + sharing 适配层） |
| 文件夹发布与取回完整复原 | 通过（test_folder_publish_and_pull） |

测试基线：`backend/tests` 59 passed（含真实子进程 E2E `test_m4_sharing_e2e.py`）；`cloud_backend/tests` 21 passed（含 `test_m4_publications.py` 7 项）。

#### 遗留（带入 M5/M6）

- 共享标签贡献与最小权限矩阵留 M5；共享回收/永久删除留 M5。
- 打包产物验证、备份恢复演练留 M6。

---

### 2026-09-26 M5 共享标签协作与组织权限（完成）

> 蓝图 §6.4 M5 退出条件：V-06 / V-10 / V-14 及 V-11 的共享并发/权限部分通过；服务端执行权限，私人标签不进共享联想。

#### 交付物

- 共享标签按关联增删 `cloud_backend/app/api/collaboration.py`（契约 §3.4/§5.2）：
  - add=标签名（空间内 get_or_create，幂等），remove=关联 tag_id；
  - 普通成员只能移除自己新增的关联，管理员可移除任意（服务端校验 added_by，非仅界面）；
  - 同名标签空间内复用，贡献者按关联行记录（不按标签词首创者）。
- 管理员回收站 trash/restore/purge（仅 admin/owner）；purge 前检查 content_hash 引用，仅当无其他版本引用时才物理删除对象（V-10 不误删仍被引用内容）。
- 成员停用 `POST /api/admin/users/{id}/deactivate`：仅置 is_active=False，资料、版本与贡献记录保留（V-14）。
- 名称/标签/共现联想 `GET /documents/suggestions?type=name|tag|cooccurrence`，均限定当前空间、候选数受限。

#### 退出条件核对

| 用例 | 状态 |
| --- | --- |
| V-06 B 补标签后 C 用该标签搜索到同一资料 | 通过（test_tag_collaboration_and_search） |
| V-11 A/B 加不同标签均保留；B 移除 A 关联被拒 403 | 通过（test_member_cannot_remove_others_link / test_admin_can_remove_any_link） |
| V-10 普通成员回收被拒；管理员 trash/restore/purge | 通过（test_trash_restore_purge_permissions） |
| V-14 停用上传者后资料与贡献保留、历史可下载 | 通过（test_deactivate_keeps_content_and_history） |

测试基线：`cloud_backend/tests` 26 passed（新增 test_m5_collaboration.py 5 项）；`backend/tests` 59 passed。

#### 遗留（带入 M6/M7）

- 共享界面标签贡献入口（低操作成本）留前端联调。
- 真实 A/B/C 交叉查找与「标签补充是否帮助他人」留 M7 试用验证。

---

### 2026-09-26 M6 打包与备份恢复（完成）

> 蓝图 §6.4 M6 退出条件：V-13 打包产物实机运行、V-15 备份恢复通过；A/B/C 全流程真实验收与 V-16 真实旧库迁移为 M7 试用前置。

#### 交付物

- 个人库备份/恢复 `backend/app/core/backup.py`（契约 §3.7）：
  - SQLite 在线一致性备份（`sqlite3.Connection.backup`，处理 WAL 未检查点数据，不复制活跃主文件）；
  - 快照对象整目录复制 + 配置备份排除凭据（cloud_access_token / cloud_refresh_token）；
  - 清单记录源库路径与 db 摘要，恢复至全新空目录时**重写版本 file_path 的绝对路径**并校验 db 摘要一致。
- 共享库备份/恢复（`scripts/mvp-backup.py` 的 backup-cloud / restore-cloud，storage_key 为相对键无需改写）。
- 维护者 CLI `scripts/mvp-backup.py`：`backup/restore personal|cloud`。

#### Electron 打包（V-13）

- Electron 28.3.3（EOL）→ 44.4.5，electron-builder 24→26。
- 生产加载 file:// 的两处硬伤修复：
  - 路由 `createWebHistory` → `createWebHashHistory`（file:// 下 history 路由 404，admin 子应用已是 hash 模式）。
  - Vite 加 `base: './'`（生产资源由绝对 `/assets/*` 改相对路径）。
- `extraResources` 跨平台修复：原 mac/linux 子配置被 electron-builder 忽略，后端可执行文件不会进包；统一为顶层 `from: "dist/backend"`，`backend:build` 加 `--distpath dist/backend`。
- 后端 PyInstaller 打包 `dist/backend/teamhub-backend`（arm64，独立运行健康检查通过）。
- 图标：生成 `build/icon.png` + `build/icon.icns`（原目录无图标）。
- 实机运行 `dist-electron/mac-arm64/TeamHub.app`：后端从 `Resources/backend/teamhub-backend` 启动、前端从 `app.asar/frontend/dist/index.html` 加载、窗口 ready、健康检查循环、退出优雅关闭——全程以打包产物运行，未用开发服务器。

#### 退出条件核对

| 用例 | 状态 |
| --- | --- |
| V-15 备份→恢复至空目录，数据与各版内容一致，凭据不在备份，历史版本可取回 | 通过（test_m6_backup.py 2 项：roundtrip 含 file_path 重写 + 非备份目录拒绝） |
| V-13 打包产物启动、加载前端、后端进程生命周期 | 通过（实机运行 TeamHub.app，日志验证） |

测试基线：`backend/tests` 63 passed（新增 test_m6_backup.py、test_m6_abc_collaboration.py）；`cloud_backend/tests` 26 passed；前端 `npm run build` 通过。

#### A/B/C 多账号端到端验收（§1.3 场景，API 层）

- 补 M5 遗漏：共享标签此前只在 cloud 层实现，本地后端无代理层（前端不直连云，标签无法操作）。`sharing.py` 补 `POST /api/sharing/tags/{shared_doc_id}` 代理（403 按信封透传）。
- `test_m6_abc_collaboration.py` 2 项：一个真实共享服务进程 + 三个独立本地后端进程（各独立个人库/端口/云端账号），验证 A 发布 → B 拉取并补标签 → C 用标签搜索找到同一资料；B 移除 A 的标签关联被拒（403）。

#### 剩余（带入 M7 试用）

- **真实用户 GUI 试用与价值验证**（蓝图 M7 任务 3–5）：用真实查找任务对比原有方式、记录是否降低协作成本；API 层验收已完成，真实价值仍需真人试用。
- **V-16 真实旧资产迁移演练**：迁移策略已由 M1 复核落地（歧义历史停止迁移并报告），构造副本已覆盖；真实资产演练待有真实旧库时执行。
- **README 更新**：旧人数/结构/端口/Python 版本说明按实测替换。
- **CSP 纵深**：生产模式 `onHeadersReceived` 对 file:// 不生效（contextIsolation + nodeIntegration:false 已提供核心隔离），建议后续用 meta CSP 补齐。


### 2026-09-28 M2 复核与修复（本阶段最新结论）

此前 8 项测试未覆盖快速收录、整理后历史不变、永久删除误删及提交失败场景。本轮先复现缺陷再修复，细节见 [M2 复核与修复记录](M2复核与修复记录.md)。

- 普通与快速收录统一复制流程，同步保存首版快照；历史查重返回匹配版本，原文件不动。
- 整理、重定位、扫描不再改写已保存版本；当前标记从资料的 `current_version_id` 推导。
- 按资料 ID 定位回收站；旧文件名条目不猜测归属。删除不再清除其他同名资料的工作文件，快照对象暂留。
- 收录、整理、回收及显式永久删除补充提交失败补偿；快照暂存和导出部分内容失败清理。重复软删除不触发永久删除，批量恢复冲突明确失败。
- 详情页新增个人版本保存、历史、导出、恢复操作，待整理页支持输入本机路径收录。
- **M2 25 项通过，相关 M1 14 项回归通过；前端构建通过。** 独立 Electron 渲染验证走通复制收录、保存 v2、恢复为 v3、导出 v1、重复收录打开既有资料，临时服务已退出。

以本节及修复记录取代此前 M2 “已完成”的不充分证据；不变更其他阶段的独立验收结论。特别更正：磁盘错误不能仅凭原子落位设计认定已规避，本轮有故障注入证据，但尚未进行断电、强杀进程验证。快照物理回收及真实旧资产处理仍需后续单独完成，未提交 Git。


### 2026-09-28 M3 复核与修复（本阶段最新结论）

此前 8 项测试不足以证明文件夹读取异常、源变化及收藏操作闭环可靠。本轮详情见 [M3复核与修复记录](M3复核与修复记录.md)，以该记录替代此前 M3 的不充分完成证据。

- 文件夹从原始源受控捕获；摘要与归档使用同一批字节，工作副本从快照还原，避免先复制时跟随嵌套链接。
- 遍历错误、源内容变化、不安全路径和超限明确失败；流式生成及解包；快照统计来自已保存归档。
- 补齐个人收藏入口与列表、工作修改只读检查、搜索失败/重试；高亮文本转义；整体文件夹重复收录打开已有资料。
- **M3 22 项 + M2 25 项 + 切库 9 项，共 56 项通过；前端构建通过；独立 Electron 界面验证通过。** 脚本为 `scripts/test_m3_ui.cjs`，不影响现有 demo。
- 初始限额为 10,000 条目、单文件 1 GiB、累计 2 GiB；真实样本校准仍待办。未将共享侧、服务器部署或打包列入本次验收，未提交 Git。


### 2026-09-28 M4 复核与修复（本阶段最新结论）

本轮取代此前 M4 基础测试不足以支持的完成表述；细节与兼容性见 [M4复核与修复记录](M4复核与修复记录.md)。

- 两端主动确认、共享范围与标签预览、文件夹发布及更新/另建入口已接通；仅已确认成功结果才保存本地关联。
- 服务端绑定上传者与空间，验证字节和文件夹清单；SQLite 写事务串行化真实并发，旧基准明确冲突；当前标记从 current_version_id 推导。
- 本地持久化冻结请求以支持响应丢失重试；按指定/当前 ID 取回，核对下载摘要，约束路径，失败清理本次工作副本与暂存。
- 合并并发续期，防止退出后复活、切库串写及旧请求改用其他登录身份。
- **共享端 23 项、真实服务联调 10 项、会话及个人侧相关回归 60 项，合计 93 项通过。** 前端构建与 Electron 文件夹共享预览/确认/更新/另建通过；双账号 A 发布、B 取回更新、A 读取历史通过。
- 暂存和未引用内容回收、真实容量校准、旧文件夹记录缺少清单摘要的兼容处理仍保留。没有生产部署、真实库迁移或 M5/M6 全量复验，未提交 Git。

### 2026-09-28 M5 复核与修复（本阶段最新结论）

以 [M5复核与修复记录](M5复核与修复记录.md) 替代此前 M5 完成记录中的不充分证据，不改变其他阶段的独立验收结论。

- 补齐共享标签贡献入口、贡献者显示、本人撤销及管理员权限；批量权限预检和 SQLite 串行写入覆盖真实并发，不整组替换贡献。
- 回收资料统一从详情、历史、下载、标签操作和联想隐藏；按标签 ID 搜索兼容带逗号的自由标签，共现叠加名称条件。
- 永久删除先提交元数据，再在锁内复查全部对象引用；文件夹使用归档摘要，提交/清理故障不会误删有效内容。
- 停用账号保留资料与贡献、撤销刷新令牌；关闭物理删除用户入口，并堵住替代更新接口绕过。全局停用限系统管理员。
- 修复团队请求重定向、登录恢复时序、工作空间响应解包及切换持久化。补齐共享收藏、再次打开、失效提示及取消；文件夹指定版本可取回个人库。
- **M5 18 项、相关回归 59 项，共 77 项通过；前端构建通过。** 隔离 Electron 验证 B 收藏并补标签、C 按 B 的标签查找并取回，管理员回收/恢复/永久删除/停用，以及回收后收藏不可访问。

测试使用临时库和自有进程，不接管用户 demo。暂存硬链接、未引用对象及请求日志回收仍待后续处理；未执行 M6 全阶段复验、真实部署或旧资料迁移。代码和文档未提交 Git。

### 2026-09-28 M6 复核与修复（本阶段最新结论）

本机 macOS ARM64 范围整改复验通过，具体证据和限制见 [M6复核与修复记录](M6复核与修复记录.md)。本节取代此前“备份可用”仅凭 2 项个人测试与简单文件复制得出的宽泛结论。

- 备份清单升级为格式 2，逐文件校验内容、集合与目录；保存个人工作修改、回收站、空目录及受管快照；配置采用白名单，共享刷新会话清除。
- 恢复拒绝已有数据库/非空目录及源目标重叠，先校验和暂存后发布，失败清理；版本路径严格按受管对象相对路径迁移。要求维护者停服务和外部编辑，不宣称在线文件快照或断电原子恢复。
- 修复 Electron 动态端口初始请求、后端实例身份、重复启停/重启、退出后复活、macOS 主窗激活及 file:// CSP/来源问题；构建使用仓库解释器并强制重建后端。
- 四个独立个人库及共享服务验证 A/B/C/管理员协作、版本历史、私人资料隔离、本地/共享删除隔离、停用保留；停机备份恢复到全新根后重新启动共享服务，历史可下载。
- 默认开发命令切换隔离启动器；共享启动不再初始化 OSS。成员本机开通 CLI、依赖快照、README 与 [运维交接](../guides/MVP运维与交接.md) 已补齐。
- **本地完整测试 119 项、共享完整测试 50 项、进程专项 3 项，共 172 项通过。** 前端构建、PyInstaller、electron-builder 及实际 `.app` 的独立端口、收录/两版保存、刷新与正常退出验证通过。

最终实际产物证据：`/var/folders/3_/z4lmy95j00b067x29p6ybkxr0000gn/T/teamhub-m6-packaged-LMSfRK`。未修改真实数据或接管用户 demo，未提交 Git。其他系统、签名安装包、真实旧资产迁移和跨历史程序回退仍未验收，不能以本轮结果替代。

### 2026-09-28 本机系统级复验

两端完整回归、启动器及桌面进程专项合计 **178 项通过**；单文件、文件夹/收藏、主动共享、多人协作（含实际登录表单）及独立新构建 `.app` 共 **5 组界面流程通过**。前端、PyInstaller、macOS 应用构建及依赖检查通过，未发现新增阻断问题。

本轮保留原有 8001 服务，启动器测试使用临时脚本副本和动态端口；应用打包至独立目录，避免覆盖使用中的产物。完整范围、日志、测试脚本补强与未覆盖事项见 [MVP系统级测试报告](MVP系统级测试报告-2026-09-28.md)。


## 2026-09-28：一键本地测试入口

新增根目录 `启动本地测试.command`、`停止本地测试.command`；`npm run dev:browser` 调用统一启动器管理个人服务、共享服务及 Vite。重复启动复用健康的自有服务，失败只回收本次创建的进程，停止不删除资料。

- 启动器 unittest：10 项通过，包括未知端口保护、重复启动、前端失败回收、新旧服务隔离及停止。
- 真实个人 FastAPI、共享 FastAPI、Vite：在临时目录与随机端口完成启动、重复启动、健康检查、停止，均通过。
- shell 语法检查、git diff --check：通过。
- 测试设置 TEAMHUB_NO_OPEN=1，未自动打开用户浏览器；Finder 双击和浏览器自动打开未作 UI 自动化验收。
- 日志：`/tmp/teamhub-oneclick-tests.log`、`/tmp/teamhub-oneclick-smoke.log`。


### 2026-09-28：修正双击入口端口冲突

复现发现 8001 被现有 `teamhub-b` 进程占用，原 `start-all` 固定检查 8001 后退出，Finder 双击时错误提示不够醒目。保留占用进程，启动器现在自动选择 8001—8010 中空闲端口，并通过 `VITE_LOCAL_API_URL` 让浏览器前端连接该端口；失败时增加 macOS 提示框。

通过实际命令观察到启动器报告个人后端使用 8002、共享服务使用 9000、前端使用 5173。随后工具执行环境回收了本次测试派生的进程，故这只能证明启动命令完成启动流程，不能当作用户 Finder 会话的持久运行验收。修复后没有重跑自动化测试。

## 分类与收藏导航验证（2026-09-29）

本地全部资料作为已整理总集合，收藏成为独立筛选入口，文件夹独立分组；移出分类与取消收藏不撤销整理。新增 test_library_navigation.py 覆盖文件与整体文件夹移出、收藏保留、标签与版本不变、同名拒绝覆盖、待整理拒绝收藏以及目录通配符字面匹配。备份测试调整为回收站收藏隐藏、恢复后原引用可见。

- 个人后端全部 161 项通过（10 项依赖警告），日志 /private/tmp/teamhub-nav-alltests-final.log。
- 前端构建与桌面打包通过。test_library_navigation_ui.cjs 在隔离临时库验证实际可见的导航、加入收藏、移出分类、取消收藏及总览保持；运行时错误为零。
- 截图和临时后端日志：/private/var/folders/3_/z4lmy95j00b067x29p6ybkxr0000gn/T/teamhub-navigation-ui-4P6xmO。
- 所有写入验收均在临时库进行，未移动或删除用户实际资料。

## 已整理资料归入分类（2026-09-29）

资料卡片菜单新增「归入文件夹」，支持选择及新建分类，已分类资料可更换分类；共享移出分类的安全移动与补偿回滚逻辑。目录选择与新建统一拒绝暂存区、系统路径及整体资料内部。

个人后端 170 项测试通过（10 项依赖警告），日志 /private/tmp/teamhub-folder-alltests.log。新增 9 项覆盖文件/整体资料归类、分类变更、版本标签收藏保持、路径边界、同名冲突和提交失败回滚。桌面隔离验收覆盖新建分类→归入→收藏中访问→移出→取消收藏；运行时错误为零。截图与日志位于 /private/var/folders/3_/z4lmy95j00b067x29p6ybkxr0000gn/T/teamhub-navigation-ui-04Ihfx。未对用户实际资料执行归类。
