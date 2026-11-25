# 交给 Antigravity 的修改指南

> 目的：让外部 AI 编程助手（Antigravity）知道**代码在哪、怎么改、改完怎么验证、怎么交回**。

---

## 一、文件夹位置（照这个来）

| 用途 | 绝对路径 |
|---|---|
| **正式程序（唯一需要改的地方）** | `D:\ai\meshkit` |
| 服务入口 | `D:\ai\meshkit\app\server.py` |
| Mechanical 内代理 | `D:\ai\meshkit\app\bridge_agent.py` |
| 网页界面（单文件） | `D:\ai\meshkit\app\static\index.html` |
| 几何识别 | `D:\ai\meshkit\app\geo_recognize.py` |
| 类别规则表 | `D:\ai\meshkit\app\part_rules.json` |
| 自检脚本 | `D:\ai\meshkit\app\check_ui.py`、`check_pipeline.py` |
| 运行时目录（**不要改、不要提交**） | `D:\ai\meshkit\app\ipc\` |
| 报告输出 | `D:\ai\meshkit\reports\` |
| 上一轮审阅用的副本（可丢弃） | `C:\Users\qiyuqiang\.gemini\antigravity\scratch\meshkit` |
| 上一轮的补丁输出 | `C:\Users\qiyuqiang\.gemini\antigravity\scratch\meshkit-fix.patch` |
| GitHub 仓库 | `https://github.com/theqio/meshkit` |

**只在 `D:\ai\meshkit` 上改**。改完若要我合并，请在该目录提交到分支（见第四节）。

---

## 二、改哪个文件 → 怎么生效

| 改的文件 | 生效方式 | 是否需要 Mechanical 配合 |
|---|---|---|
| `app/server.py`、`app/static/index.html`、`app/geo_recognize.py`、`part_rules.json` | 重启服务 + 浏览器 `Ctrl+F5` | 否 |
| `app/bridge_agent.py` | 在 Mechanical 的 Scripting 面板按 `Ctrl+F5`；或调 `POST /api/op {"op":"exec","payload":{"script":"<安装目录>/app/reload_self.py"}}` | **是** |
| `app/config.json`、`app/ipc/*`、`model_config__*.json`、`model_flags__*.json` | **不要改** | — |

**启动服务**

```bat
cd /d D:\ai\meshkit\app
python server.py
```

页面地址：<http://127.0.0.1:8765/>

---

## 三、硬约束（违反会直接炸）

1. `bridge_agent.py` 必须是 **IronPython 2.7** 语法：
   - 不能用 f-string / 类型注解 / `async` / `dataclass`
   - 字符串用 `u"..."`，文件保存为 **UTF-8 with BOM**
2. 代理入口必须幂等（放到文件末尾）：
   ```python
   try:
       if not globals().get("_keep"):
           start()
   except Exception:
       pass
   ```
   多次 Ctrl+F5 不能叠加定时器，否则 Mechanical 会闪退。
3. 不要写死安装路径；代理用 `_resolve_root()`（环境变量 `MESHKIT_ROOT` → `__file__` → 当前目录）。
4. 同一实例同一时刻只能有一条桥接命令在飞（`_PID_LOCKS` + `call_bridge`）。
5. `其他` / `未分组` 里的体**永远不改名**。
6. 识别**不看体名**，只看几何（尺寸签名/填充率/质心/接触/朝向）。

---

## 四、标准工作流（Antigravity 请照做）

```bat
cd /d D:\ai\meshkit

:: 1. 拉最新主线
git checkout main
git pull

:: 2. 建工作分支（不要直接改 main）
git checkout -b fix/<你这次的主题>

:: 3. 改代码（只改运行必需的文件）

:: 4. 语法自检
python -c "import py_compile,sys; py_compile.compile(r'app/server.py', doraise=True); print('server ok')"
python -c "import py_compile; py_compile.compile(r'app/geo_recognize.py', doraise=True); print('geo ok')"
:: 代理是 IronPython 2.7，用 py3 只能粗检，重点看有没有用新语法

:: 5. 重启服务后跑页面/流水线自检
taskkill /F /IM python.exe /FI "WINDOWTITLE eq *server*" 2>nul
start "" /min cmd /c "cd /d D:\ai\meshkit\app && python server.py"
timeout /t 5 >nul
python app\check_ui.py
python app\check_pipeline.py

:: 6. 提交并告诉我分支名
git add -A
git commit -m "fix: <一句话>"
git rev-parse --abbrev-ref HEAD
git rev-parse --short HEAD
```

**交回方式（任选一种）**

- 把分支名 + 提交号发我（我在本机 `git fetch` 后 cherry-pick / merge）
- 或导出补丁：
  ```bat
  git format-patch main --stdout > D:\ai\meshkit\_incoming_<主题>.patch
  ```

---

## 五、改完必须自测的 4 件事

```bat
:: ① 服务在线
curl http://127.0.0.1:8765/api/ping

:: ② 实例与代理版本
curl http://127.0.0.1:8765/api/instances

:: ③ 类别（灯色不能全红/全灰）
curl http://127.0.0.1:8765/api/categories

:: ④ 只读 op 冒烟
curl -X POST http://127.0.0.1:8765/api/op -H "Content-Type: application/json" ^
     -d "{\"op\":\"list_bodies\",\"payload\":{}}"
```

页面自检（会真的用无头浏览器打开页面）：

```bat
python app\check_ui.py        :: 期望输出「校验通过」
python app\check_pipeline.py  :: 期望「全部检查通过」
```

---

## 六、当前待改清单（按优先级，含位置）

| # | 问题 | 位置 | 说明 |
|---|---|---|---|
| 1 | `apply` 首次仍可能 60 秒+ | `app/bridge_agent.py` `op_apply` / `_apply_methods` | 已在代理 .12 做差异写入；确认代理确实重载到 .12，再测一次耗时 |
| 2 | 代理自载偶尔失败 | `app/reload_self.py` | 已改不停定时器；可加"重载后 ping 校验失败则提示手动 Ctrl+F5" |
| 3 | 薄板判定偏几何 | `server.py` `_plate` 判定 | 波纹板（45mm 厚）判不出薄板，需要"壁厚/面积"组合判据 |
| 4 | `server.py` 过大（约 5450 行） | `app/server.py` | 拆 `http_routes.py` / `bridge_io.py` / `report.py`，保持单文件可运行 |
| 5 | `index.html` 过大（约 2450 行） | `app/static/index.html` | 用原生 ES module 拆，不要引入打包器 |
| 6 | 缺自动化测试 | 仓库根 `tests/` | 给 `geo_recognize.recognize`、`_match_size`、`merge_custom_groups` 写纯函数单测 |
| 7 | `app/` 里有一次性脚本 | `app/apply_plan_a.py` 等 | 移到 `_archive/`，只留运行必需 |
| 8 | 报告堆积 | `reports/` | 加"保留最近 N 份"清理 |

**不要动**：`app/part_rules.json` 里已有的类别与尺寸签名（删条目会让识别率下降）；`config.json`（含用户 API Key）。

---

## 七、直接粘给 Antigravity 的提示词

```
项目在 D:\ai\meshkit（ANSYS Mechanical 网格自动化工具），先读：
- D:\ai\meshkit\AGENTS.md（架构、硬约束、已知问题）
- D:\ai\meshkit\README.md（安装与使用）
- D:\ai\meshkit\交给Antigravity_修改指南.md（工作流与验证方式）

这次任务：按该指南第六节，优先处理第 1、3、6 项（apply 耗时、薄板判据、自动化测试）。

硬性要求：
1) 在 main 上新建分支 fix/<主题>，不要直接改 main
2) bridge_agent.py 保持 IronPython 2.7 语法（无 f-string/类型注解），UTF-8 with BOM
3) 代理入口必须幂等（if not globals().get("_keep"): start()）
4) 不要写死安装路径；不要提交 app/ipc、config.json、model_config__*、model_flags__*
5) 改完必须跑：python app\check_ui.py 和 python app\check_pipeline.py，贴出输出
6) 最后给我：分支名、提交号、改动文件清单、每个文件改了什么（各一行）、自检输出

如果你改了 bridge_agent.py，请把 AGENT_VERSION 加 0.01（例如 2026-12-02.13），
并提醒我需要在 Mechanical 里 Ctrl+F5 重载。
```

---

## 八、常见坑

| 现象 | 原因 | 处理 |
|---|---|---|
| 点「② 重命名+分组」报 500 | `scan_groups` 缺失（历史回归） | 已修；改这块后必须跑一次 ② 验证 |
| 分组全被并成「未分组」 | 扫描结果异常仍落地 | 已有安全闸：分组数<2 或覆盖体数不符会中止 |
| 页面全红/全灰 | 控制项校验拿不到（Mechanical 忙） | 等 3 秒自动重试；或点「解除卡住」 |
| 点应用半天没反应 | 代理旧版（无差异写入） | 确认 ping 版本为 `2026-12-02.12+`，否则 Ctrl+F5 |
| Mechanical 闪退 | 同一窗口重复执行代理 → 多个定时器 | 只执行一次；新版入口已幂等 |

---

## 九、亿赛通（DLP）加密：已改为**自动同步**（不用手动导出导入）

**问题**：项目目录 `D:\ai\meshkit` 里的文件在磁盘上是**密文**，只有白名单进程（Python）能透明解密。
Antigravity / VS Code 等外部编辑器直接打开会是乱码。

**实测**

```
cmd type D:\ai\meshkit\app\geo_recognize.py   → 乱码（Esafenet 头）
python 读同一文件                                  → 明文
%TEMP% 目录                                       → 不加密，任何程序都是明文
```

### 现在的用法：零操作

双击 `加载桥接.bat` 会同时启动一个后台进程 `MeshKit DLP Sync`（窗口最小化，别关）：

```
项目 D:\ai\meshkit  ⇄  %TEMP%\meshkit_work     每 2 秒双向同步
```

- 我改了项目文件 → 2 秒内自动出现在 `%TEMP%\meshkit_work`（Antigravity 能看到）
- Antigravity 改了 `%TEMP%\meshkit_work` → 2 秒内自动写回项目（Python 写，内容正确）
- 两边同时改同一文件 → 以"修改时间较新"的一方为准，并记录冲突日志

日志：`%TEMP%\meshkit_work\.dlp_sync.log`
手动检查：`python app\tools\dlp_sync.py --status`
手动同步一次：`python app\tools\dlp_sync.py --once`

> 旧的 `工具_DLP导出.bat` / `工具_DLP导入.bat` 仍保留，作为应急手段；日常不需要点。

可选自检（确认导出副本能被外部程序读取）：

```bat
python D:\ai\meshkit\app\tools\dlp_bridge.py status
:: 期望：明文 N 个 / 疑似密文 0 个 → 可用
```

### 给 Antigravity 的补充要求

```
不要读写 D:\ai\meshkit（亿赛通加密，你读到的是乱码）。
请只在 C:\Users\qiyuqiang\AppData\Local\Temp\meshkit_work 里修改代码。
改完只报告改动文件清单，不要自己拷回；由 Python 脚本 import 回项目。
该目录下 .git 与 reports/ 已剔除，不要尝试 git 操作。
```

### 已知限制

- `reports/`、`app/ipc/`、`config.json`、`model_config__*.json`、`model_flags__*.json` **不导出**（运行时数据/含密钥）
- 导入时只覆盖"导出清单里已存在"的文件，以及副本里**新增**的文件
- 导出副本不含 `.git`，因此在那里不能提交；提交仍在 `D:\ai\meshkit` 由我执行
