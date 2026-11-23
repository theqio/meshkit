# MeshKit 工具栏按钮（ANSYS Mechanical）

Mechanical 顶部 **`Automation`** 标签 → 右侧 **`User Buttons`** 组，有三个按钮。

---

## 一、三个按钮现在做什么

| 按钮 | 作用 | 什么时候点 |
|---|---|---|
| **MeshKit_Check** | 加载/重载桥接代理（`app/bridge_agent.py`）| **每次重启 Mechanical 后必须点一次** |
| **MeshKit_Run** | 打开控制台网页 `http://127.0.0.1:8765` | 网页被关掉时 |
| **MeshKit_Report** | 打开质量报告页 `/report` | 想看报告时 |

> 早期的 `MeshKit_Run` 是"一键划网格"，现在**已改为打开网页**：
> 在 Mechanical 自己的按钮里回调 Mechanical 会卡住界面（同一个 UI 线程），
> 所以所有操作都放在网页里做，按钮只负责加载代理 / 打开页面。

---

## 二、脚本存在哪

```
%APPDATA%\Ansys\v221\Applets\DSApplet\UserRibbons\
    UserRibbons.xml              ← 按钮定义（按钮名 → 脚本文件）
    scripts\MeshKit_Check.py     ← 加载桥接代理
    scripts\MeshKit_Run.py       ← 打开控制台网页
    scripts\MeshKit_Report.py    ← 打开报告页
    images\MeshKit_*_ur.png      ← 按钮图标
```

属于**用户级配置**：这台机器上用任何工程都能看到这三个按钮，重启 Mechanical 后仍在。

---

## 三、按钮背后的逻辑

```python
# MeshKit_Check.py
import os
execfile(r"D:\ai\meshkit\app\bridge_agent.py")

# MeshKit_Run.py / MeshKit_Report.py
import webbrowser
webbrowser.open("http://127.0.0.1:8765")          # 或 /report
```

好处：逻辑都在 `D:\ai\meshkit` 里，改代码不用重建按钮；按钮只做入口。

---

## 四、判断代理是否已加载

网页右上角「桥接」状态点：

- 🟢 绿 + `已连接 Mechanical` → 已加载
- 🔴 未连接 → 去 Mechanical 点一次 `MeshKit_Check`

也可以用接口查当前代理版本：

```
http://127.0.0.1:8765/api/op  (POST)
{"op":"ping","payload":{}}
→ {"ok":true,"version":"2026-12-01.6", ...}
```

版本号对不上说明 Mechanical 里跑的还是旧代理，需要重新点 `MeshKit_Check`。

---

## 五、换台电脑怎么迁移

1. 复制 `%APPDATA%\Ansys\v221\Applets\DSApplet\UserRibbons\` 整个文件夹到新机器同一路径
2. 复制 `D:\ai\meshkit` 到新机器（**路径保持 `D:\ai\meshkit` 最省事**）
3. 重启 Mechanical

若新机器路径不同，改 `scripts\MeshKit_Check.py` 里的 `AGENT` 常量。

---

## 六、按钮失效怎么办

| 现象 | 原因 | 处理 |
|---|---|---|
| 点按钮弹错误 / 没反应 | 脚本指向的文件不存在 | 用文本编辑器检查 `scripts\MeshKit_*.py` 里的路径 |
| `MeshKit_Check` 点了但网页仍显示未连接 | 模型还没打开，或代理报错 | 先在 Mechanical 里打开 Model，再看 Mechanical 的 Messages |
| 按钮整组消失 | `UserRibbons.xml` 被改坏 | 恢复该 xml，重启 Mechanical |

> 注意：本机装了 DLP（Esafenet）时，这些脚本在磁盘上是**加密**的。
> 用记事本打开会看到乱码，属正常现象；要看真实内容请用 Python 读，
> 或者直接删掉脚本让按钮报错再重写。

---

## 七、附：早期 ACT 扩展（未启用）

`%APPDATA%\Ansys\v221\ACT\extensions\_MeshKit_unused\` 是最早按 ACT 规范写的扩展
（带 XML 清单 + 图标），因为 User Buttons 更简单可靠已停用。
想试的话把文件夹名改回 `MeshKit` 并重启 Mechanical，在 `Extensions → Manage Extensions` 里勾选。
