import asyncio
from collections import OrderedDict, deque
from contextlib import asynccontextmanager
import json
import logging
import os
from pathlib import Path
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from webapp.agent import research_events
from webapp.backtest import run_backtest
from webapp.models import CheckRequest, DataRequest, ResearchRequest
from webapp.providers import (LLMClient, ServiceError, TushareClient, allowed_bases,
                              date_string, evidence, http_client, now, records)

logger = logging.getLogger("fin_agent.web")


class RequestGuard:
    """Bound bodies before JSON parsing; rate-limit anonymous access per process/IP."""
    def __init__(self, app):
        self.app = app
        self.hits = OrderedDict()

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        request_id = uuid.uuid4().hex
        scope.setdefault("state", {})["request_id"] = request_id

        async def secure_send(message):
            if message["type"] == "http.response.start":
                message.setdefault("headers", []).extend([
                    (b"x-request-id", request_id.encode()), (b"x-content-type-options", b"nosniff"),
                    (b"referrer-policy", b"no-referrer"), (b"x-frame-options", b"DENY"),
                    (b"cache-control", b"no-store"),
                    (b"content-security-policy", b"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"),
                ])
            await send(message)

        if scope["path"].startswith("/api/") and scope["method"] == "POST":
            ip = (scope.get("client") or ("unknown",))[0]
            current = time.monotonic()
            times = self.hits.setdefault(ip, deque())
            self.hits.move_to_end(ip)
            while times and current-times[0] > 60:
                times.popleft()
            if len(times) >= int(os.getenv("REQUESTS_PER_MINUTE", "60")):
                return await JSONResponse({"error": "请求过于频繁，请稍后再试。"}, status_code=429)(scope, receive, secure_send)
            times.append(current)
            if len(self.hits) > 10000:
                self.hits.popitem(last=False)
            body = bytearray()
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    return
                body.extend(message.get("body", b""))
                if len(body) > 180000:
                    return await JSONResponse({"error": "请求过大，请缩短历史记录。"}, status_code=413)(scope, receive, secure_send)
                if not message.get("more_body"):
                    break
            replayed = False

            async def replay():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": bytes(body), "more_body": False}
                return await receive()

            return await self.app(scope, replay, secure_send)
        return await self.app(scope, receive, secure_send)


@asynccontextmanager
async def lifespan(app):
    app.state.capacity = asyncio.Semaphore(int(os.getenv("MAX_CONCURRENT_REQUESTS", "8")))
    yield


app = FastAPI(title="Fin-Agent Web", version="1.0.0", lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
app.add_middleware(RequestGuard)


@app.exception_handler(ServiceError)
async def service_error(request: Request, exc: ServiceError):
    return JSONResponse({"error": exc.message, "request_id": request.state.request_id}, status_code=exc.status)


@app.exception_handler(RequestValidationError)
async def invalid_request(request: Request, exc: RequestValidationError):
    # Never serialize Pydantic errors' input/ctx: they may contain credentials.
    fields = [".".join(map(str, e["loc"])) for e in exc.errors()][:8]
    return JSONResponse({"error": "请求参数无效，请检查填写内容。", "fields": fields}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception):
    logger.error("Request failed id=%s type=%s", request.state.request_id, type(exc).__name__)
    return JSONResponse({"error": "服务处理失败，请稍后重试。", "request_id": request.state.request_id}, status_code=500)


@asynccontextmanager
async def capacity():
    try:
        await asyncio.wait_for(app.state.capacity.acquire(), timeout=1)
    except TimeoutError:
        raise ServiceError("服务繁忙，请稍后重试。", 503) from None
    try:
        yield
    finally:
        app.state.capacity.release()


@app.get("/healthz")
async def health():
    return {"status": "ok", "version": "1.0.0", "mode": "live-byok"}


@app.get("/api/config")
async def config():
    return {"allowed_base_urls": allowed_bases(), "mode": "live-byok",
            "price_type": "最近交易日收盘价", "max_tool_calls": 12, "max_research_seconds": 240,
            "credentials_storage": "页面内存；服务端仅在当前请求使用，不落盘",
            "background_monitoring": False}


@app.post("/api/check")
async def check(body: CheckRequest):
    async with capacity(), http_client() as http:
        if body.service == "tushare":
            frame = await TushareClient(body.credentials, http).query("trade_cal", "cal_date,is_open", exchange="SSE", start_date=date_string(7), end_date=date_string())
            if frame.empty:
                raise ServiceError("交易日历接口返回空数据，请检查权限或稍后重试。")
            return {"ok": True, "message": "Tushare 交易日历调用成功；其他接口权限需分别验证。", "checked_at": now().isoformat()}
        message, usage = await LLMClient(body.credentials, http).chat([{"role": "user", "content": "请只回答 OK，用于确认接口连通。"}], max_tokens=32)
        if not message.get("content"):
            raise ServiceError("模型已响应但没有返回文本，请检查模型设置。")
        return {"ok": True, "message": "模型文本调用成功；研究时还需支持工具调用。", "usage": usage, "checked_at": now().isoformat()}


@app.post("/api/data")
async def data(body: DataRequest):
    try:
        async with capacity(), asyncio.timeout(180), http_client() as http:
            client = TushareClient(body.credentials, http)
            if body.action == "market":
                return await client.market()
            if body.action == "search":
                if not body.keyword:
                    raise ServiceError("请输入股票名称或代码。", 400)
                return await client.search(body.keyword)
            if body.action == "quotes":
                return await client.quotes(body.codes)
            if body.action == "history":
                if len(body.codes) != 1:
                    raise ServiceError("历史行情每次需要一个股票代码。", 400)
                return evidence(records(await client.daily(body.codes[0], body.days)), "daily", adjustment="不复权")
            if body.action == "news":
                return await client.news()
            if body.action == "backtest":
                if body.backtest is None:
                    raise ServiceError("缺少回测参数。", 400)
                if body.backtest.end_date > now().date():
                    raise ServiceError("回测结束日期不能晚于今天。", 400)
                return await run_backtest(client, body.backtest)
    except TimeoutError:
        raise ServiceError("数据请求超时，请缩小查询范围后重试。", 504) from None


@app.post("/api/research")
async def research(body: ResearchRequest):
    # Fail before opening a stream for missing configuration.
    if not body.credentials.tushare_token.get_secret_value():
        raise ServiceError("请先在连接设置填写 Tushare Token。", 400)
    LLMClient(body.credentials, None)

    async def stream():
        try:
            async with capacity(), http_client() as http:
                async for event in research_events(body, http):
                    yield json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n"
        except ServiceError as exc:
            yield json.dumps({"type": "error", "message": exc.message}, ensure_ascii=False) + "\n"
        except TimeoutError:
            yield json.dumps({"type": "error", "message": "研究达到 240 秒时限，已停止。可缩小问题范围重试。"}, ensure_ascii=False) + "\n"
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("Research failed type=%s", type(exc).__name__)
            yield json.dumps({"type": "error", "message": "研究执行失败；已停止，不生成替代分析。"}, ensure_ascii=False) + "\n"
    return StreamingResponse(stream(), media_type="application/x-ndjson", headers={"X-Accel-Buffering": "no"})


app.mount("/", StaticFiles(directory=Path(__file__).parent / "static", html=True), name="web")
