# AGENTS.md — 给代码审阅助手（Antigravity / Cursor / Claude Code 等）

本文件面向“外部 AI 编程助手”。读完这一页即可开始修改代码，不需要先问我。

---

## 1. 这是什么

ANSYS Mechanical（2022 R1 / v221）的网格自动化工具。三层结构：

```
浏览器页面  ⇄  本地 HTTP 服务(Python 3)  ⇄  Mechanical 内代理(IronPython 2.7)
 app/static/index.html   app/server.py                app/bridge_agent.py
```

- **页面**：单文件 HTML（内联 JS/CSS），无构建步骤
- **服务**：标准库 `ThreadingHTTPServer`，端口 8765（`MESHKIT_PORT` 可改）
- **代理**：在 Mechanical 的 Scripting 面板里 `execfile` 运行，靠**文件队列**通信

通信协议（关键）：

```
app/ipc/cmd_<pid>.json      服务写 → 代理 1 秒轮询取走并删除
app/ipc/result_<pid>.json   代理写 → 服务轮询读取（按 seq 匹配）
app/ipc/agents/<pid>.json   代理每秒刷新（心跳，用于判断在线）
app/ipc/progress.json       代理写进度（页面显示）
app/ipc/cancel.flag         服务写，代理检测后中止长任务
```

---

## 2. 运行与调试

```bat
:: 启动服务（任意目录安装均可，路径自动定位）
cd <安装目录>\app
python server.py

:: 或双击 <安装目录>\加载桥接.bat （自动复制连接命令 + 启服务 + 开页面）
```

在 Mechanical 里执行（命令由 `加载桥接.bat` / `app/connect_cmd.py` 自动生成）：

```python
import os;os.environ["MESHKIT_ROOT"]=r"<安装目录>";execfile(r"<安装目录>/app/bridge_agent.py")
```

**改动后如何生效**

| 改的文件 | 生效方式 |
|---|---|
| `app/server.py`、`app/static/index.html` | 重启服务 + 浏览器 Ctrl+F5 |
| `app/bridge_agent.py` | 在 Mechanical 里 Ctrl+F5 重载；或调 `POST /api/op {"op":"exec","payload":{"script":"<安装目录>/app/reload_self.py"}}` |
| `app/geo_recognize.py`、规则 JSON | 重启服务即可 |

**自检脚本**

```bat
python app/check_ui.py        :: 页面 JS 语法 + DOM id 一致性 + 无控制台报错（需服务在跑）
python app/check_pipeline.py  :: 后端流水线自检
python app/eval_recognition.py:: 识别率评估（需要快照）
```

API 冒烟（服务在跑时）：

```
GET  /api/ping  /api/status  /api/instances  /api/categories
POST /api/op    {"op":"list_bodies","payload":{}}
```

---

## 3. 目录地图

```
app/server.py            HTTP 路由 + 桥接调度 + 缓存 + 锁 + 识别/报告逻辑（单文件，约 5270 行）
app/bridge_agent.py      Mechanical 内代理，命令分发 __main__ 入口（约 5970 行，IronPython 2.7）
app/static/index.html    整个前端（约 2440 行，内联 JS）
app/geo_recognize.py     纯几何识别：尺寸/填充率/质心/接触/朝向
app/experience.py        经验库（问题→动作→效果）
app/sizing_advisor.py    尺寸推荐（特征 + 单元预算）
app/part_rules.json      类别 → 尺寸签名表（识别依据，勿随意删条目）
app/reload_self.py       代理自我重载（沿调用栈找模块 globals，不动旧定时器）
app/connect_cmd.py       生成"加载桥接"命令并复制剪贴板
app/check_ui.py          前端自检
```

---

## 4. 硬约束（改了会炸）

1. **`bridge_agent.py` 必须是 IronPython 2.7 语法**
   - 不能用 f-string、`async`、类型注解、`dataclass`
   - 字符串统一 `u"..."`；文件保存为 **UTF-8 with BOM**
2. **代理入口必须幂等**
   ```python
   try:
       if not globals().get("_keep"):
           start()
   except Exception:
       pass
   ```
   多次执行不能再叠加定时器（叠加会造成 Mechanical 闪退）。
3. **不要写死安装路径**
   代理用 `MESHKIT_ROOT` 环境变量 / `__file__` / 当前目录 依次定位（见 `_resolve_root()`）。
4. **桥接调用要串行**
   同一实例同一时刻只允许一条命令在飞，否则 COM 并发会让 Mechanical 崩。
   现有实现：`_PID_LOCKS` + `call_bridge()`（读操作等 12 秒、写操作等 180 秒）。
5. **`其他` / `未分组` 里的体永远不改名**（用户明确要求）。
6. **识别不看名字**：分组必须来自几何特征，不能用体名做判断依据。

---

## 5. 目前已知问题 / 待改进（按优先级）

| # | 问题 | 位置 | 建议 |
|---|---|---|---|
| 1 | ~~`apply` 耗时 60~140 秒~~ **已优化**：设置/尺寸/方法改为差异写入，只改有变化的项 | `server.py` apply 分支 + 代理 `op_apply/_apply_methods` | 只更新与当前值不同的控制项；批量写属性；把 scan 结果复用 |
| 2 | ~~长操作期间页面只能等~~ **已改**：`/api/op` 支持 `async:true` → 立即返回 `job_id`，页面轮询 `/api/job` | `index.html` `setBusy()` | 改成异步任务：`POST /api/op` 立即返回 job id，页面轮询 `/api/job` |
| 3 | 代理自载偶尔失败（旧定时器被停） | `app/reload_self.py` | 已改为不停定时器；可增加"重载后 3 秒内 ping 校验失败则回滚" |
| 4 | ~~尺寸/方法全局共享~~ **已改**：`model_config__<模型>.json` 存 sizes/methods/settings/advanced，全局文件不再保留 | `server.py` `load_config()` | 参照 `group_overrides__<model>.json`、`model_flags__<model>.json` 做成分模型 |
| 5 | 薄板判定偏几何阈值，个别类别（波纹板 45mm 厚）判不出来 | `server.py` `_plate` 判定、`geo_recognize` | 引入"壁厚/面积"组合判据，或允许每模型保存判据阈值 |
| 6 | `server.py` 单文件过大（约 5270 行） | `server.py` | 拆分：`http_routes.py` / `bridge_io.py` / `recognition_service.py` / `report.py` |
| 7 | 前端 `index.html` 单文件约 2440 行 | `index.html` | 拆成模块（原生 ES module 即可，不要引入打包器） |
| 8 | 缺少自动化测试 | 仓库根 | 为 `geo_recognize`、`_match_size`、`merge_custom_groups` 写纯函数级单测 |
| 9 | 多个一次性脚本残留在 `app/`（`apply_plan_a.py` 等） | `app/` | 移到 `_archive/`，只保留运行必需文件 |
| 10 | 报告目录文件较多 | `reports/` | 增加"保留最近 N 份"清理策略 |

---

## 6. 风格约定

- 注释与用户可见文案用中文；变量名英文
- 错误处理：对外返回 `{"ok": False, "error": "..."}`，内部不要 `raise` 到 HTTP 层
- 新增 API 时同步更新 `app/check_ui.py` 用到的 id 列表（否则自检失败）
- 提交信息用英文短句；不要提交 `app/config.json`、`app/ipc/`、`model_flags__*.json`、`presets_kb.json`

---

## 7. 安全

- `app/config.json` 里可能有大模型 API Key，**不要**提交或打印
- 所有数据在本机；不要引入任何会把模型/网格数据上传的依赖
- 依赖只用 Python 标准库（服务端）与 Mechanical 自带 .NET（代理），避免安装依赖

---

## 8. 建议的审阅顺序

1. `app/geo_recognize.py` — 识别算法本身（可独立单测）
2. `app/server.py` 中 `call_bridge / 缓存 / 锁` — 稳定性核心
3. `app/bridge_agent.py` 的 `op_*` 函数 — 每个都是 Mechanical API 封装，注意异常吞噬
4. `app/static/index.html` — 事件绑定与忙碌状态机（容易出"卡住"）
5. `README.md` — 与实现是否一致

---

## 9. 起手提示词（可直接粘给审阅助手）

```
这是一个 ANSYS Mechanical 网格自动化工具，先读 AGENTS.md 与 README.md，
再按第 8 节顺序快速浏览代码。任务：
1) 找出会导致"功能失效/数据错乱"的真实缺陷（不要风格意见）
2) 对每个问题给出：文件:行号、触发条件、最小修复方案
3) 优先处理 AGENTS.md 第 5 节清单里的 1/2/4 项
4) 改动前先建分支，不要直接改 main；bridge_agent.py 必须保持 IronPython 2.7 语法
输出格式：按严重程度排序的表格 + 每项一句修复建议。
```

---

## 10. 交接前的当前状态（2026-11-25）

- 服务、页面、代理三层均可用；识别 2000c4 模型 143 体 → 20~23 类
- 代理版本 `2026-12-02.11`（可移植路径版）
- 已修：重复控制项、失败体未从类别控制项摘除、薄板层数未写入、目标实例被抢、页面卡死
- 待修：见第 5 节

---

## 11. 外部审阅已合并（2026-11-25）

Antigravity 提交 `033deca` 已合并（本地 `review/antigravity-p0` → main），内容：

- `apply` 差异写入（设置/尺寸/方法只改变更项）+ 无变更不重划
- 异步任务通道 `/api/job`（apply / mesh / fix_failed / set_bodies_size）
- 分模型配置 `model_config__<model>.json`（sizes/methods/settings/advanced）
- `_call_bridge_impl` 去掉全局 `_ipc_lock`（改为按实例锁；`call_bridge_pid` 也已加同一把锁）
- `geo_recognize`：上梁仅限梁体；`remap_overrides` 不再遍历中改字典
- `check_ui.py` 路径改为相对
- 代理 `2026-12-02.12`：`_keep/_busy/_hb` 重载保持幂等；方法/尺寸差异更新；`fix_failed` 无变化不重划
