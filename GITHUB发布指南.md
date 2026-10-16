# AAA端口大王 — 发布状态与后续操作

> 本文档记录仓库当前的发布状态、遇到过的坑，以及以后怎么更新。

---

## 一、当前状态（已完成）

| 项目 | 状态 |
| --- | --- |
| GitHub 仓库 | ✅ <https://github.com/Mj00102/AAA-PortKing>（公开） |
| 仓库内容 | ✅ 18 个文件全部就位，已逐一校验 sha 与本地一致 |
| 描述 | ✅ AAA端口大王 - Windows 端口进程一键查看工具: 看清端口被谁占用, Ctrl+左键开浏览器, 一键停服务 |
| 话题标签 | ✅ `windows` `port` `netstat` `port-manager` `tkinter` `python` `developer-tools` |
| Release | ✅ [v1.0](https://github.com/Mj00102/AAA-PortKing/releases/tag/v1.0)，挂了两个 exe 附件 |
| 提交身份 | ✅ `MJ00000` / `Mj00102@users.noreply.github.com`（不暴露真实邮箱） |
| CI | ✅ `.github/workflows/tests.yml`（Windows + Python 3.9/3.12） |

Release 附件（用英文名，避免中文文件名在各处被破坏）：

| 附件 | 大小 | 说明 |
| --- | --- | --- |
| `AAA-PortKing-v1.0-single.zip` | 12.12 MB | 免安装单文件版，解压后双击 exe |
| `AAA-PortKing-v1.0.exe` | 12.48 MB | 同上，未压缩 |

---

## 二、踩过的坑（记录一下，避免重复踩）

### 1. `git push` 连不上 github.com

现象：

```
fatal: unable to access 'https://github.com/Mj00102/AAA-PortKing.git/':
Failed to connect to github.com:443 after 21051 ms
```

但同一个网络下 `gh api repos/Mj00102/AAA-PortKing` 是**通的**，
`Test-NetConnection github.com -Port 443` 也显示**可达**。

结论：**这台机器上只有 git 自己的出网被拦**（curl 连不出去），
而 GitHub CLI 走的东西是被放行的。

**解决办法**：用 `gh api` 代替 `git push`，见 `tools/sync_github.py`。

### 2. 空仓库不能用 Git Data API

刚创建、还没有任何提交的仓库，调用 `git/blobs` 会返回：

```
409 Git Repository is empty.
```

空仓库必须用 **Contents API**（`PUT /repos/{owner}/{repo}/contents/{path}`）来创建第一个文件。

### 3. 已存在的文件必须带 `sha`

仓库创建时 GitHub 会自动生成 `README.md` / `.gitignore` / `LICENSE`。
覆盖这些文件时如果不带 `sha`，会返回：

```
422 Invalid request. "sha" wasn't supplied.
```

正确做法：先 `GET /contents/{path}` 拿到 `.sha`，再放进 PUT 的请求体。

### 4. 大文件的 base64 不能走命令行参数

12 MB 的 exe base64 后约 16 MB，超过 Windows 命令行长度上限：

```
FileNotFoundError: [WinError 206] 文件名或扩展名太长
```

必须把请求体写进 JSON 文件，用 `gh api --input 文件` 传。

### 5. 中文文件名在命令行里会被破坏

`gh release upload dist\AAA端口大王.exe` 上传后，远端文件名变成 `AAA.exe`。
所以 Release 附件改用英文名（这也是 GitHub 上的通行做法），
中文说明写在 Release 的正文里。

---

## 三、以后怎么更新

### 改了代码 / 文档

```powershell
cd "C:\Users\zjxMJ\Desktop\你D爷爷又有新项目了\AAA-PortKing"
python test_portking.py            # 先跑测试, 全绿再推
git add -A
git commit -m "fix: 说明改了什么"
git push                           # 如果网络允许
```

**如果 `git push` 又连不上**，用同步脚本代替：

```powershell
python tools\sync_github.py
```

它会遍历工作区文件，逐个通过 Contents API 推到 `main`（已存在的自动覆盖）。

### 改了 `portking.py`，要更新 exe

```powershell
.\打包exe.bat                       # 重新生成两个版本
```

更新 Release 附件：

```powershell
# 中文文件名经命令行会被破坏, 先复制成英文名再传
Copy-Item "dist\AAA端口大王-单文件版.zip" "AAA-PortKing-v1.1-single.zip"
gh release upload v1.1 "AAA-PortKing-v1.1-single.zip"
```

### 发新版本

```powershell
# tag 推送: git 不通时用 API
gh api --method POST repos/Mj00102/AAA-PortKing/git/refs `
  -f "ref=refs/tags/v1.1" -f "sha=$(gh api repos/Mj00102/AAA-PortKing/commits/main --jq .sha)"

gh release create v1.1 "AAA-PortKing-v1.1-single.zip" `
  --title "AAA端口大王 v1.1" --notes "本次更新: ..."
```

---

## 四、关于仓库体积

仓库里放了两个 exe（约 25 MB），好处是**别人 `git clone` 下来就能直接用**。

Git 历史会永久保存二进制文件，如果以后每次发版都提交新 exe，仓库会持续变大。
如果介意，可以改成只走 Releases：

```powershell
Add-Content .gitignore "`ndist/"
git rm -r --cached dist
git commit -m "chore: 二进制改由 Releases 分发"
```

---

## 五、检查清单

已完成：

- [x] 源码可运行（`python portking.py`）
- [x] 37 项自测全过（`python test_portking.py`）
- [x] README（含头图、截图、功能、快捷键、原理、打包说明）
- [x] LICENSE（MIT）
- [x] .gitignore
- [x] GitHub Actions CI
- [x] 应用图标（exe + 窗口）
- [x] 提交身份用 GitHub 隐私邮箱
- [x] 仓库公开、描述、话题标签
- [x] v1.0 Release + exe 附件

可以再做的（可选）：

- [ ] 开启 Issues 收集反馈
- [ ] 给仓库加社交预览图（Settings → Social preview）
- [ ] 补一个英文 README（`README.en.md`）方便国外用户
