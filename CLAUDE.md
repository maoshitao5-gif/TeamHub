# TeamHub 开发指引

以 [项目基石](docs/product/TeamHub项目基石.md)、[MVP 蓝图](docs/product/TeamHub-MVP实施蓝图.md) 和 [数据契约](docs/architecture/MVP数据与接口契约.md) 为依据。文档总入口：[docs/README.md](docs/README.md)。

## 架构与边界

- Electron 管理桌面窗口、预加载桥和自有个人后端进程。
- Vue 用户界面通过个人 FastAPI 管理私有资料；个人服务通过共享适配层连接组织 FastAPI。
- 个人库与组织库使用独立 SQLite 和受管文件目录；主动发布，不做双向文件同步；MVP 不依赖 OSS/MinIO。
- 默认私有；组织副本独立；哈希标识内容，不能单独代表文档身份；已保存版本不可改写。
- 共享版本查看与取回使用共享资料页面。备份使用停写后的维护脚本，不恢复旧云端数据库备份流程。
- 遗留的 410 路由、迁移、仍在使用的历史模型需保持兼容；不得因名称陈旧删除。
- 主界面仍有团队/引导页使用旧直连客户端，属于待收敛问题；管理子应用的独立认证另行看待。

## 操作入口

安装、启动、账号和试用见 [启动指引](docs/guides/MVP本机试用启动指引.md)。两个 Python 虚拟环境独立，requirements.txt 引用各自 macOS ARM64 锁文件。

```sh
npm run dev:browser
npm run dev:status
npm run dev:stop
venv/bin/python -m pytest backend/tests -q
cloud_backend/venv/bin/python -m pytest cloud_backend/tests -q
npm run frontend:build
```

桌面构建：`npm run electron:build -- --mac --dir`，先生成 `dist/backend` 和 `frontend/dist`，再输出 `dist-electron`。其他平台和正式部署尚待验证。

## 修改约定

保持中文 UI 与注释风格。不覆盖已有工作区改动，不提交凭据、数据库、用户文件和构建产物。测试使用临时目录和独立端口，不停止归属不明的服务。数据结构变更必须通过迁移；不能靠删除数据库解决兼容问题。备份和恢复遵循 [运维指引](docs/guides/MVP运维与交接.md)。
