"""Request-local engine using upstream Fin-Agent strategy classes, next-bar execution."""
import math

import pandas as pd

from fin_agent.backtest.registry import get_strategy_class
import fin_agent.backtest.strategies  # noqa: F401 -- upstream strategy registration
from webapp.models import BacktestArgs
from webapp.providers import ServiceError, evidence


def calculate_backtest(frame: pd.DataFrame, args: BacktestArgs):
    if frame.empty:
        raise ServiceError("请求区间没有足够日线数据。", 404)
    config = {"short_window": args.fast, "long_window": args.slow,
              "fast_period": args.fast, "slow_period": args.slow,
              "lower": args.rsi_lower, "upper": args.rsi_upper}
    strategy = get_strategy_class(args.strategy)()
    data = strategy.enrich(frame.sort_values("trade_date").reset_index(drop=True).copy(), config)
    cash, shares, entry = float(args.initial_capital), 0, None
    trades, curve = [], []
    peak, drawdown, buy_hold_start = cash, 0.0, None
    start = args.start_date.strftime("%Y%m%d")
    for i in range(2, len(data)):
        row, previous, before_previous = data.iloc[i], data.iloc[i-1], data.iloc[i-2]
        if str(row.trade_date) < start:
            continue
        open_price, close_price = float(row.open), float(row.close)
        if not all(math.isfinite(v) and v > 0 for v in [open_price, close_price]):
            raise ServiceError("价格数据包含无效值，无法可靠回测。")
        context = {"position": shares, "entry_price": entry, "cash": cash}
        signal = strategy.risk_exit(previous, before_previous, config, context) if shares else 0
        if signal == 0:
            signal = strategy.signal(previous, before_previous, config, context)
        tradable = float(row.get("vol", 0)) > 0
        # Compare unadjusted opening price with actual daily exchange limits.
        raw_open = float(row.raw_open)
        can_buy = tradable and raw_open < float(row.up_limit) - 0.001
        can_sell = tradable and raw_open > float(row.down_limit) + 0.001
        if signal == 1 and shares == 0 and can_buy:
            price = open_price * (1 + args.slippage)
            fraction = max(0, min(1, float(strategy.position_fraction(config, {**context, "signal_row": previous}))))
            amount = math.floor(cash * fraction / (price * (1 + args.commission)) / 100) * 100
            if amount > 0:
                fee = amount * price * args.commission
                cash -= amount * price + fee
                shares, entry = amount, price
                trades.append({"date": str(row.trade_date), "signal_date": str(previous.trade_date), "action": "BUY", "price": round(price, 4), "shares": amount, "fee": round(fee, 2)})
        elif signal == -1 and shares > 0 and can_sell:
            price = open_price * (1 - args.slippage)
            fee = shares * price * args.commission
            cash += shares * price - fee
            trades.append({"date": str(row.trade_date), "signal_date": str(previous.trade_date), "action": "SELL", "price": round(price, 4), "shares": shares, "fee": round(fee, 2)})
            shares, entry = 0, None
        value = cash + shares * close_price
        peak = max(peak, value)
        drawdown = max(drawdown, (peak-value)/peak)
        if buy_hold_start is None:
            buy_hold_start = open_price
        curve.append({"trade_date": str(row.trade_date), "value": round(value, 2),
                      "return_pct": round((value/args.initial_capital-1)*100, 4),
                      "benchmark_return_pct": round((close_price/buy_hold_start-1)*100, 4)})
    if not curve:
        raise ServiceError("区间内没有可回测交易日。", 404)
    return {"ts_code": args.ts_code, "strategy": args.strategy, "initial_capital": args.initial_capital,
            "final_value": curve[-1]["value"], "total_return_pct": curve[-1]["return_pct"],
            "max_drawdown_pct": round(drawdown*100, 4), "trades_count": len(trades), "trades": trades,
            "equity_curve": curve, "parameters": args.model_dump(mode="json"),
            "assumptions": ["前复权日线；前一交易日收盘生成信号，下一交易日开盘成交。",
                            "使用实际涨跌停价过滤开盘触及涨跌停的交易，成交量为零不成交；不能模拟盘中流动性。",
                            "100 股整数手；显式佣金与滑点；未单独建模印花税、过户费和最低佣金。",
                            "复权价格为标准化模拟价格，未模拟实际公司行动的持股数量变化。",
                            "期末未强制平仓；基准为同一股票买入持有、不扣基准交易费用。",
                            "单标的历史回测，不代表未来收益；非历史时点股票池选股回测。"]}


async def run_backtest(client, args: BacktestArgs):
    from datetime import timedelta
    from starlette.concurrency import run_in_threadpool
    start = (args.start_date-timedelta(days=450)).strftime("%Y%m%d")
    end = args.end_date.strftime("%Y%m%d")
    daily = await client.query("daily", ts_code=args.ts_code, start_date=start, end_date=end)
    factors = await client.query("adj_factor", "ts_code,trade_date,adj_factor", ts_code=args.ts_code, start_date=start, end_date=end)
    limits = await client.query("stk_limit", "trade_date,up_limit,down_limit", ts_code=args.ts_code, start_date=start, end_date=end)
    if daily.empty or factors.empty or limits.empty:
        raise ServiceError("日线、复权因子或涨跌停数据缺失，无法完成回测。请检查接口权限。")
    data = daily.merge(factors[["trade_date", "adj_factor"]], on="trade_date", how="left").merge(limits, on="trade_date", how="left")
    data = data.sort_values("trade_date").drop_duplicates("trade_date")
    if data[["adj_factor", "up_limit", "down_limit"]].isna().any().any():
        raise ServiceError("部分交易日缺少复权或涨跌停数据，已停止回测。")
    data["raw_open"] = data["open"]
    factor = data.adj_factor / data.adj_factor.iloc[-1]
    if (factor <= 0).any():
        raise ServiceError("复权因子无效。")
    for column in ["open", "high", "low", "close"]:
        data[column] *= factor
    result = await run_in_threadpool(calculate_backtest, data, args)
    return evidence(result, "daily + adj_factor + stk_limit", as_of=result["equity_curve"][-1]["trade_date"])
