# Testbench · UI 自动化测试平台

基于 **Playwright 本机执行**的轻量级 UI 自动化测试平台。测试人员通过 Excel 描述业务测试步骤（做什么、操作什么、期望什么），平台自动完成元素定位、浏览器操作、断言与报告生成。

```
浏览器(管理界面) ──HTTP── FastAPI 后端 ──Python + Playwright── 本机 Chromium
                              │                    │
                          SQLite 数据库      HTTP/HTTPS 访问被测试环境(Base URL)
```

## 核心设计

- **执行引擎**：Playwright 运行在 Testbench 所在机器（本机），直接启动 Chromium/Chrome/Edge 浏览器，通过 HTTP/HTTPS 访问被测试环境。
- **不依赖远程执行**：不需要 SSH 连接测试服务器，不需要在被测试环境安装 Python / Playwright / Selenium / Docker 等任何自动化依赖。
- **测试人员友好**：测试用例只描述业务步骤（操作 + 操作对象 + 输入值 + 预期结果），不填写 CSS / XPath / ID 等元素定位信息，元素定位由平台自动完成。
- **登录信息统一管理**：客户名、用户名、密码在「测试环境」中统一维护，业务用例执行前自动完成登录，测试人员无需重复填写。

## 目录结构

```
ui_test_platform/
├── backend/
│   ├── main.py            # FastAPI 入口：登录认证、路由挂载、静态资源
│   ├── database.py        # SQLite 连接 + 表结构自动迁移（兼容旧版本库）
│   ├── models.py          # 数据模型：测试环境 / 用例 / 步骤 / 执行记录 / 步骤结果
│   ├── schemas.py         # Pydantic 请求/响应模型
│   ├── excel_import.py    # Excel 用例导入解析 + 模板生成（仅表头）
│   ├── executor.py        # Playwright 执行引擎：自动定位、自动登录、逐步执行
│   ├── env_manager.py     # （历史遗留）SSH 远程环境管理，当前架构未使用
│   └── api/
│       ├── servers.py     # 测试环境管理 + 本机执行环境检查/初始化
│       ├── testcases.py   # 用例导入 / 新增 / 编辑 / 删除 / 模板下载
│       └── runs.py        # 执行任务（单个/批量）+ 报告查询 + 批量删除 + PDF 导出
├── frontend/
│   └── index.html         # 前端单文件（登录 / 测试环境 / 测试用例 / 执行任务 / 执行报告）
├── screenshots/           # 失败步骤自动截图（运行时自动创建）
├── requirements.txt
└── README.md
```

## 快速开始

### 1. 安装依赖

需要 **Python 3.9+**，并安装 Playwright 浏览器运行环境：

```bash
cd ui_test_platform
pip install -r requirements.txt
playwright install chromium      # 安装 Chromium 浏览器（首次）
```

> 也可以只装 Chrome 或 Edge：`playwright install chrome` / `playwright install msedge`。
> 平台内的「执行环境检查」可以自动检测本机 Python / Playwright / Chromium 是否就绪。

### 2. 启动后端

```bash
python backend/main.py
# 或（开发模式，支持热重载）
uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
```

打开浏览器访问 `http://localhost:8000` 即可进入管理界面。

**局域网访问**：其他电脑访问 `http://<本机局域网IP>:8000`（如 `http://172.16.165.193:8000`）。

### 3. 默认账号

| 账号 | 密码 |
|---|---|
| admin | 888888 |

登录后可在左下角用户区修改密码。

## 使用流程

```
1. 测试环境  → 添加被测试环境（名称 + Base URL + 登录信息）
2. 测试用例  → 下载模板 → Excel 填写业务步骤 → 拖拽导入（或直接在页面新增）
3. 执行任务  → 选择模块/用例 + 测试环境 + 浏览器 + 执行模式 → 开始执行
4. 执行报告  → 查看统计、筛选历史记录、查看详情与失败截图、批量删除/导出 PDF
```

### 测试环境

维护被测试的业务环境，仅需：

| 字段 | 说明 | 示例 |
|---|---|---|
| 环境名称 | 自定义名称 | 测试环境 |
| 环境地址 / Base URL | 业务系统地址 | http://172.16.10.151 |
| 备注 | 可选 | 生产环境 |
| 客户名 / 用户名 / 密码 | 登录信息，自动登录时使用 | - |
| 登录路径 | 登录页相对路径 | /#/login |

### 测试用例（Excel 导入）

模板仅含以下列，测试人员只需描述业务步骤：

| 用例名称 | 所属模块 | 用例描述 | 步骤序号 | 操作 | 操作对象 | 输入值 | 预期结果 |
|---|---|---|---|---|---|---|---|
| 登录成功 | 登录 | - | 1 | 打开 | 登录页面 | /login | 登录页面正常展示 |
| 登录成功 | 登录 | - | 2 | 输入 | 用户名 | testuser | 输入成功 |
| 登录成功 | 登录 | - | 3 | 输入 | 密码 | 123456 | 输入成功 |
| 登录成功 | 登录 | - | 4 | 点击 | 登录按钮 | | 登录成功 |
| 登录成功 | 登录 | - | 5 | 校验 | 欢迎信息 | | 页面显示欢迎信息 |

- **用例名称 / 所属模块 / 用例描述**：同一用例的多行只需在第一行填写，自动向下填充。
- **操作**：支持中文业务操作，导入时自动映射为技术操作；也兼容直接填写技术操作名。

**支持的中文操作**（自动映射）：

| 中文 | 技术操作 | 说明 |
|---|---|---|
| 打开 | open_url | 打开网址（相对路径自动拼接 Base URL） |
| 输入 | input | 在输入框输入文本 |
| 点击 | click | 点击按钮/链接/菜单等 |
| 校验 | assert_text | 校验元素文本包含预期内容 |
| 选择 | select | 下拉框/单选/级联等选择 |
| 悬停 | hover | 鼠标悬停 |
| 清空 | clear | 清空输入框 |
| 等待 | sleep | 固定等待 N 秒 |
| 刷新 | refresh | 刷新页面 |
| 返回 | go_back | 浏览器后退 |
| 截图 | screenshot | 主动截图 |
| 滚动 | scroll_to | 滚动到元素可见 |
| 按键 | press_key | 模拟按键（ENTER/TAB 等） |
| 切换iframe | switch_to_frame | 切入 iframe |
| 切回主文档 | switch_to_default | 切回主文档 |
| 校验标题 | assert_title | 校验页面标题 |
| 校验元素存在 | assert_element_exists | 断言元素存在 |
| 校验元素不存在 | assert_element_not_exists | 断言元素不存在 |

### 执行任务

- **测试模块 + 测试用例（可多选）**：先选模块，再选该模块下的用例，支持多选批量执行。
- **测试环境**：选择要访问的业务环境。
- **浏览器**：Chromium / Chrome / Edge。
- **执行模式**：无头模式（默认，后台静默执行）/ 有头模式（弹出真实浏览器窗口，便于观察执行过程）。
- **登录模式**（用例级）：
  - 自动登录（默认）：执行前平台自动使用测试环境中的登录信息完成登录；
  - 不登录：从登录页面直接开始（用于登录模块本身的测试）；
  - 复用登录状态：预留模式，用于连续执行多个业务用例时复用登录会话，避免重复登录。

### 执行报告

- 统计概览：总执行数 / 通过率 / 平均耗时 / 今日失败数。
- 筛选：状态（全部/通过/失败）、时间范围（今天/近7天/近30天/自定义）、用例名称搜索、更多筛选（环境/浏览器/执行人）。
- 详情：点击某条记录查看每一步的执行结果、耗时，失败步骤自动截图。
- 批量操作：批量删除、批量导出为 PDF 报告。

## 元素定位机制

平台根据「操作对象」自动定位页面元素，测试人员无需填写任何定位信息：

1. `get_by_role()`（按钮类优先 button，输入类优先 textbox/combobox）
2. 标签相邻输入框（Ant Design 表单专用，label → 容器 → 可交互元素）
3. `get_by_label()` / `get_by_placeholder()` / `get_by_text()`
4. CSS / 属性 / XPath 选择器
5. 模糊文本匹配（自动去除空格，适配"登 录"等）

针对 Ant Design 组件做了专门优化：下拉框（Select）、单选按钮组（Radio.Group）、级联选择器（Cascader）、日期选择器（Picker）、数字输入框（InputNumber）等均可自动识别与操作。

**异常提示区分**：
- 本机环境问题 → "当前Testbench执行环境未安装Playwright运行环境，请先完成本机环境初始化"
- 测试环境不可访问 → "无法访问测试环境：http://xxx"
- 元素定位失败 → "无法定位元素：登录按钮"

## 主要 API

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | /api/login | 登录，返回 token |
| POST | /api/change-password | 修改密码 |
| POST | /api/logout | 退出登录 |
| GET | /api/verify | 校验 token |
| GET/POST | /api/servers | 测试环境列表 / 新增 |
| PUT/DELETE | /api/servers/{id} | 编辑 / 删除测试环境 |
| POST | /api/servers/env-check | 本机执行环境检查 |
| POST | /api/servers/env-init | 本机执行环境初始化 |
| POST | /api/testcases/import | Excel 导入用例 |
| GET | /api/testcases/template | 下载 Excel 模板（仅表头） |
| GET/POST | /api/testcases | 用例列表 / 新增用例 |
| GET/PUT/DELETE | /api/testcases/{id} | 用例详情 / 编辑 / 删除 |
| POST | /api/runs | 创建单个执行任务 |
| POST | /api/runs/batch | 批量创建执行任务 |
| GET | /api/runs | 执行记录列表 |
| GET | /api/runs/{id} | 执行记录详情 |
| POST | /api/runs/batch-delete | 批量删除执行记录 |
| POST | /api/runs/export | 导出 PDF 报告 |

## 数据存储

- 默认使用 SQLite，数据库文件：`backend/ui_test_platform.db`。
- 失败步骤截图存放于 `screenshots/`。
- 可通过环境变量 `DATABASE_URL` 切换为 MySQL / PostgreSQL。

## 常见问题

**Q：执行时报"无法定位元素：XX"？**
元素自动定位失败。请检查操作对象描述是否与页面上的文字一致（如按钮文字、输入框标签），Ant Design 表单建议用标签文字（如"客户名称"）作为操作对象。

**Q：有头模式下浏览器窗口弹出很频繁？**
批量执行建议使用「无头模式」，后台静默执行不弹窗。

**Q：其他电脑无法访问平台？**
确认后端以 `0.0.0.0` 启动（或使用本机局域网 IP），并确保防火墙放行 8000 端口。
