# Testbench · UI 自动化测试平台

一个可以导入 Excel 测试用例、注册测试服务器（Selenium 节点）、并触发执行 + 查看报告的轻量级平台。

```
浏览器(管理界面) ──HTTP── FastAPI 后端 ──Selenium Remote WebDriver── 测试服务器(Grid/节点)
                              │
                          SQLite 数据库
```

## 目录结构

```
ui_test_platform/
├── backend/
│   ├── main.py           # FastAPI 入口，挂载路由与静态资源
│   ├── database.py       # 数据库连接（默认 SQLite，可切换 MySQL/PostgreSQL）
│   ├── models.py         # 数据表：TestServer / TestCase / TestStep / TestRun / StepResult
│   ├── schemas.py        # 接口请求/响应的数据结构
│   ├── excel_import.py   # 解析 Excel 用例并写入数据库
│   ├── executor.py       # 执行引擎：连接测试服务器、逐步执行、写回结果
│   └── api/
│       ├── servers.py    # 测试服务器管理接口
│       ├── testcases.py  # 用例导入/查询接口
│       └── runs.py       # 触发执行/查询报告接口
├── frontend/
│   └── index.html        # 管理界面（原生 HTML/CSS/JS，无需构建）
├── screenshots/          # 失败步骤自动截图存放目录
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 准备一台测试服务器

平台本身不内置浏览器，需要你提供一台能跑 Selenium 的"测试服务器"。最简单的方式是用 Docker 在本机或任意一台机器上启动一个：

```bash
docker run -d -p 4444:4444 --shm-size=2g selenium/standalone-chrome
```

启动后，这台机器的 `http://<IP>:4444/wd/hub` 就是你要在平台里填写的"Hub 地址"。
如果测试量大，可以用 `selenium/hub` + 多个 `selenium/node-chrome` 组成 Grid 集群，平台同样通过 Hub 地址接入，会自动分发到空闲节点。

### 2. 安装依赖并启动后端

```bash
cd ui_test_platform
pip install -r requirements.txt
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

打开浏览器访问 `http://localhost:8000` 即可看到管理界面（前后端同一服务，无需额外部署）。

### 3. 使用流程

1. **测试服务器**页签 → 添加你在第 1 步准备好的 Hub 地址，点"检测"确认在线。
2. **测试用例**页签 → 点"下载模板"，按格式在 Excel 里填写步骤，再拖拽导入。
3. **执行任务**页签 → 选择用例 + 服务器 → 开始执行，可实时看到状态变化。
4. **执行报告**页签 → 查看历史记录，点开某条可看到每一步的结果、耗时，失败步骤会自动截图。

## Excel 用例格式说明

| 列名 | 说明 |
|---|---|
| 用例名称 | 同一用例的多个步骤行填相同名称（可以只在第一行填，工具会自动向下填充） |
| 所属模块 | 可选，用于分类 |
| 用例描述 | 可选 |
| 步骤序号 | 数字，决定步骤执行顺序 |
| 操作类型 | 见下表 |
| 定位方式 | id / name / css / xpath / class / link_text / tag |
| 定位值 | 配合定位方式使用的选择器 |
| 输入值/URL | 视操作类型而定，例如 open_url 时填网址，input 时填要输入的文字 |
| 期望结果 | 人工可读的说明，仅作展示不参与判断 |
| 超时(秒) | 可选，等待元素出现的最长时间，默认 10 秒 |

支持的操作类型（`操作类型` 列）：

| action | 作用 | 需要 定位值 | 需要 输入值/URL |
|---|---|---|---|
| open_url | 打开网址 | - | 网址 |
| click | 点击元素 | ✓ | - |
| input | 输入文本（自动先清空） | ✓ | 文本 |
| clear | 清空输入框 | ✓ | - |
| select | 下拉框按可见文本选择 | ✓ | 选项文本 |
| hover | 鼠标悬停 | ✓ | - |
| wait_for_element | 等待元素出现 | ✓ | - |
| assert_text | 断言元素文本包含某内容 | ✓ | 期望包含的文本 |
| assert_element_exists | 断言元素存在 | ✓ | - |
| assert_element_not_exists | 断言元素不存在 | ✓ | - |
| assert_title | 断言页面标题包含某内容 | - | 期望包含的文本 |
| screenshot | 主动截图（失败步骤会自动截图，无需专门加这一步） | - | - |
| scroll_to | 滚动到元素可见 | ✓ | - |
| press_key | 模拟按键，如 ENTER / TAB | 可选 | 按键名 |
| refresh | 刷新页面 | - | - |
| go_back | 浏览器后退 | - | - |
| sleep | 固定等待 N 秒 | - | 秒数 |
| switch_to_frame | 切入 iframe | ✓ | - |
| switch_to_default | 切回主文档 | - | - |

导入时可以点击"下载模板"直接拿到一份填好示例的 Excel 作为参考。

## 后续可扩展方向

- **认证/多用户权限**：目前是单租户，可加登录和用户体系。
- **定时/批量执行**：加一个 APScheduler 定时任务，或支持勾选多个用例一起跑。
- **并发调度**：`TestServer.max_sessions` 字段已预留，可以在 `executor.py` 里加一个简单的信号量/队列，按服务器并发上限调度多个 `TestRun`。
- **移动端支持**：把 `executor.py` 换成 Appium client，`TestServer` 加个 `type` 字段区分 web/mobile 即可复用现有表结构。
- **失败重试、飞书/钉钉通知**：在 `runs.py` 执行完成后加钩子。
- **生产部署**：`DATABASE_URL` 环境变量切到 PostgreSQL/MySQL；用 `docker-compose` 把后端、数据库、Selenium Grid 编排在一起。
