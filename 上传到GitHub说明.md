# 上传到 GitHub（三种方式，选一种）

> 仓库已经准备好：分支 `main`，已清理掉 75 个开发脚手架/临时文件，`.gitignore` 已排除运行数据与密钥。

---

## 方式 A：命令行（推荐，最干净）

### 1. 在 GitHub 上先建一个空仓库

打开 https://github.com/new
- Repository name：`meshkit`
- 可见性：Private（推荐，里面有你的业务规则）或 Public
- **不要**勾选 "Add a README / .gitignore / license"（保持空仓库）

### 2. 在本机推送

```bat
cd /d D:\ai\meshkit
git remote add origin https://github.com/<你的账号>/meshkit.git
git push -u origin main
```

第一次推送会弹窗要账号：用户名填 GitHub 账号，密码位置填空仓库的 **Personal Access Token**（不是登录密码）。

> 生成 Token：GitHub → Settings → Developer settings → Personal access tokens → Tokens (classic) → Generate new token → 勾选 `repo` → 复制保存。

---

## 方式 B：让我来推（需要你提供两项信息）

1. 仓库地址，例如 `https://github.com/yourname/meshkit`
2. 一个**只对这一个仓库有效**的 Token（classic，勾 `repo`，用完可以立刻删除）

我拿到后会：

```bat
git remote add origin <仓库地址>
git push -u origin main
git remote -v          # 确认
```

推完你把那个 Token 删掉即可。

---

## 方式 C：用压缩包（不走 Git）

如果只是想拷到另一台电脑，直接用打包好的：

```
D:\ai\meshkit_dist\MeshKitAI_v1.0.zip   （2.9 MB）
```

解压到目标电脑的 `D:\ai\meshkit`，然后按 `README.md` 里的「一、在新电脑上安装」做即可。

---

## 推送前的检查清单

- [x] `app/config.json`（含 API Key）已被 .gitignore 排除
- [x] `app/ipc/`（运行时命令/结果文件）已排除
- [x] `app/_patch_*.py`、`*.bak_*` 等开发脚手架已从版本库移除
- [x] `app/model_flags__*.json`、`presets_kb.json`（本机模型标记/知识包）已排除
- [x] README 已包含新机器安装步骤
- [ ] 确认仓库里没有你不想公开的业务文件名（`part_rules.json` 含部位名称，如不想公开请设 Private 仓库）
