# 免费静态版：手机随时看（GitHub Pages）

不需要电脑常开。GitHub Actions 每个交易日 **14:20（北京时间）** 在云端运行一次，生成报告并发布到 GitHub Pages；同时按 `config.json` 里的配置发邮件。

公开页面会**隐藏金额、份额、成本**，只保留：基金名称/代码、占比、每日建议、配置诊断、建议目标占比。

## 先知道这些限制

- 静态页只能看，**不能**在手机上添加/删除基金；改持仓要更新 GitHub Secret（见下文）。
- GitHub 的定时任务可能有几分钟延迟，不保证 14:20 整点；通常仍能在 15:00 前。
- 公开页面是公开可访问的；金额已隐藏，但基金代码/名称/占比会公开。
- 邮件是从 GitHub 的服务器发 QQ 邮箱；如果 QQ 限制海外 IP，邮件可能失败，但网页仍会更新。
- 云端不保存 SQLite 历史，所以“连续 2 天确认”的滞后规则不跨天生效；每日建议仍正常。
- 国内访问 `github.io` 有时较慢；如果打不开，建议改用云服务器方案。

## 一次性设置

### 1. 建仓库

登录 GitHub，新建一个 **Public** 仓库，例如 `fund-radar`。

### 2. 上传文件

推荐用 `fund-radar-github.zip`：

1. 解压 ZIP；
2. 在仓库页面点 **Add file → Upload files**；
3. 把解压出来的所有文件和文件夹拖进去，包括 `.github` 文件夹；
4. 提交。

**不要上传**：`config.json`、`data/`、`logs/`、`reports/`、`public/`。

如果你会用 Git，也可以直接 `git push`。

### 3. 添加 Secret（你的真实配置）

1. 仓库 **Settings → Secrets and variables → Actions**；
2. **New repository secret**；
3. Name 填：`FUNDRADAR_CONFIG`；
4. Value 粘贴你本地 `config.json` 的**完整内容**（包含基金、QQ 邮箱授权码等）；
5. 保存。

Secret 是加密的，不会出现在公开页面或日志里。

### 4. 开启 Pages

仓库 **Settings → Pages → Build and deployment**：

- Source 选择 **GitHub Actions**（如果找不到，先完成第 5 步再回来设置）。

### 5. 运行一次

1. 打开仓库的 **Actions** 标签；
2. 选择 **FundRadar Static**；
3. 点 **Run workflow** → **Run workflow**；
4. 等 1–3 分钟。

### 6. 手机打开

地址格式：

```
https://<你的GitHub用户名>.github.io/fund-radar/
```

例如用户名是 `abc`、仓库名是 `fund-radar`，就是 `https://abc.github.io/fund-radar/`。

## 每天怎么跑

- 定时：`20 6 * * 1-5`（UTC）= **14:20 北京时间，周一到周五**；
- 休市日 `run.py --scheduled` 会跳过，不发布新页，手机继续看上一期；
- 工作流每天提交一个 `.github/last-run.txt` 心跳，避免 GitHub 因 60 天无活动停用定时任务；
- 手动更新：Actions → FundRadar Static → Run workflow。

## 修改持仓后怎么同步

1. 在本地管理面板改好持仓和目标；
2. 打开本地 `config.json`，复制全文；
3. 到 GitHub **Settings → Secrets and variables → Actions → FUNDRADAR_CONFIG → Update**；
4. 粘贴新内容保存；
5. 想立刻生效，去 Actions 手动 Run workflow；否则等下一个交易日 14:20。

## 本地预览静态页

```powershell
cd "D:\我的文档\deepseek-harness\default-workspace\fund-radar"
D:\python\python.exe publish_static.py
```

打开 `public/index.html`，确认金额已隐藏、手机宽度下排版正常。

## 常见问题

**页面 404？**
Pages 还没部署完，或 Source 没选 GitHub Actions。等 1–2 分钟再刷新。

**Actions 报缺少 FUNDRADAR_CONFIG？**
第 3 步的 Secret 没加，或名字拼错。

**邮件没收到？**
看 Actions 日志里的 `推送 email` 状态；QQ 可能限制海外 IP。网页仍会更新，邮件问题不影响页面。

**页面没更新？**
看 Actions 是否失败/被禁用；GitHub 定时任务可能延迟，手动 Run workflow 可立即更新。

**基金接口报错？**
GitHub 出口 IP 访问东财/腾讯/新浪偶尔会限流；看 Actions 日志。持续失败建议改用国内云服务器。

**能加按钮吗？**
静态页不能。需要按钮（添加/删除/修改持仓）就用云服务器方案。
