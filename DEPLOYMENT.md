# Fin-Agent Web 交付与部署

本版本是真实接口驱动的免注册 Web 应用。前端和 Python 后端由同一服务提供，可部署至公网 HTTPS。按照本次确认，未替你实际部署或开通云资源。

## 公网部署：云服务器

1. 准备已安装 Docker Compose 的 Linux 服务器，将域名 A/AAAA 记录指向服务器，开放 80、443 端口。
2. 将交付包解压，进入包含 Dockerfile 的目录。
3. 创建 `.env`，只填写部署参数（不填写用户密钥）：

```dotenv
DOMAIN=research.your-domain.com
```

4. 执行 `docker compose up -d --build`。Caddy 自动申请 HTTPS 证书。访问 `https://research.your-domain.com`。
5. 访问 `/healthz` 检查服务。用户在网页「连接与偏好」填写自己的 Tushare Token、模型 API Key，并分别检测。

不要将 app 的 8000 端口直接暴露公网。默认限流在反向代理后可能按代理 IP 合并计数，适合小规模初版；规模扩大前应在可信入口增加按客户端的限流，并正确限定代理可信范围，不信任任意用户提供的转发头。

## 云托管：Render

将本目录作为仓库根目录提交到你自己的 Git 仓库，在 Render 创建 Blueprint，选择 `render.yaml`。它创建付费 starter Web Service，不需要数据库；实际费用以平台确认页为准。部署成功后使用平台 HTTPS 地址。若仓库根目录在上一级，应在平台设置 Root Directory 为本目录。

## 开发验证

需要 Python 3.11 或 3.12 稳定版：

```sh
python -m venv .venv
# Linux: source .venv/bin/activate
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
pip install -r requirements-web.txt
uvicorn webapp.main:app --host 127.0.0.1 --port 8000 --no-access-log
```

打开服务地址，不要直接打开 HTML 文件。开发服务器只用于验证；公网使用上面的 HTTPS 部署方式。

## 真实功能

- Tushare 股票搜索、日线/指数行情、估值筛选、财报、现金流、技术指标、财经资讯。
- 模型执行真实工具调用循环，界面显示执行状态、工具证据、数据缺口和 Token 使用量。
- 自选及模拟持仓本地保存，真实收盘价估值；缺失报价不会按零计算组合盈亏。
- 三种首版策略：双均线、MACD、RSI。复用原仓库策略类，真实计算权益曲线；前日信号、次日开盘成交。
- 研究保存、继续追问、Markdown 连同证据导出，回测 JSON 导出。

## 凭据与安全边界

免注册，未实现云端账户或跨设备同步。用户密钥仅在页面内存和服务端当前请求中使用，不写入 localStorage、数据库或应用日志。浏览器研究记录和持仓保存在用户设备；清除浏览器数据会删除记录。

密钥会通过 HTTPS 发送至本服务后端，后端调用用户指定的允许服务。用户需要信任部署运营者及模型服务商。问题和必要的金融数据会发送至所选模型。不要在问题中填写密钥。代码不提供匿名后台定时监控，关闭页面后不持续轮询。

模型地址采用服务端精确允许列表。部署管理员可通过 `LLM_ALLOWED_BASE_URLS` 添加逗号分隔的可信 HTTPS Base URL。不要加入内网、云元数据、回环地址或不可信代理。不开启任意 URL 代理或重定向。

每次研究最多 8 轮、12 次工具调用、240 秒；取消后已完成的上游调用仍可能收费。每进程默认 8 个并发请求和每 IP 每分钟 60 次请求。凭据和上游原始错误不写日志。运营者也应避免在代理/APM中记录请求体。

## 数据及回测限制

行情为最近交易日收盘价，非逐笔实时行情。各 Tushare 接口需要用户账号对应权限；交易日历检测通过不代表 news、财报、stk_limit 等全部可用。无权限、网络错误或空数据会显示实际缺口，不返回虚拟替代结果。

回测需要 daily、adj_factor、stk_limit 权限；采用前复权价格，整手、佣金、滑点和开盘涨跌停过滤。未单独模拟印花税、最低佣金、过户费、盘中流动性或实际公司行动的股份变化。回测不能保证真实成交或未来收益。研究结论需人工复核。

用户自备 Token 不替代数据供应商许可和产品运营合规核查。

## 源码关系

原始 `fin_agent/` 业务代码保留。新增 `webapp/` 使用独立请求客户端，不调用原版全局 Config 写密钥，不使用 ts.set_token，避免跨用户覆盖。复用 `fin_agent/tools/technical_indicators.py` 的计算函数和 `fin_agent/backtest/strategies/` 的策略；公网安全工具白名单没有暴露原 CLI 的配置重置、邮件设置或文件清理工具。

## 测试

```sh
pip install pytest
python -m pytest tests_web -q
```

测试使用隔离的上游传输替身验证程序行为，不冒充真实账号的权限验证。你填入自己的有效密钥后，应验证连接、一次带工具证据的研究和一次真实回测。模型投研质量评测尚未进行。
