from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class Credentials(StrictModel):
    tushare_token: SecretStr = Field(default=SecretStr(""), max_length=512)
    api_key: SecretStr = Field(default=SecretStr(""), max_length=2048)
    base_url: str = Field(default="https://api.deepseek.com/v1", max_length=250)
    model: str = Field(default="deepseek-chat", min_length=1, max_length=120)


Code = Annotated[str, Field(pattern=r"^\d{6}\.(SH|SZ|BJ)$")]


class Position(StrictModel):
    code: Code
    amount: int = Field(gt=0, le=100_000_000)
    cost: float = Field(gt=0, le=1_000_000)


class StockArgs(StrictModel):
    ts_code: Code


class SearchArgs(StrictModel):
    keyword: str = Field(min_length=1, max_length=50)


class HistoryArgs(StockArgs):
    days: int = Field(default=180, ge=5, le=1800)


class ScreenArgs(StrictModel):
    pe_max: float = Field(default=15, gt=0, le=1000)
    pb_max: float | None = Field(default=None, gt=0, le=100)
    market_cap_min_yi: float = Field(default=0, ge=0, le=1_000_000)
    industry: str | None = Field(default=None, max_length=50)
    limit: int = Field(default=20, ge=1, le=50)


class NewsArgs(StrictModel):
    hours: int = Field(default=24, ge=1, le=72)


class EmptyArgs(StrictModel):
    pass


class BacktestArgs(StockArgs):
    strategy: Literal["ma_cross", "macd", "rsi"] = "ma_cross"
    start_date: date
    end_date: date
    initial_capital: float = Field(default=100_000, ge=1000, le=100_000_000)
    commission: float = Field(default=0.0003, ge=0, le=0.01)
    slippage: float = Field(default=0.001, ge=0, le=0.02)
    fast: int = Field(default=5, ge=2, le=120)
    slow: int = Field(default=20, ge=3, le=250)
    rsi_lower: int = Field(default=30, ge=1, le=49)
    rsi_upper: int = Field(default=70, ge=51, le=99)

    @model_validator(mode="after")
    def validate_range(self):
        if self.end_date <= self.start_date:
            raise ValueError("结束日期必须晚于开始日期")
        if (self.end_date - self.start_date).days > 1826:
            raise ValueError("单次回测区间最多五年")
        if self.fast >= self.slow:
            raise ValueError("短周期必须小于长周期")
        return self


class DataRequest(StrictModel):
    credentials: Credentials
    action: Literal["market", "search", "quotes", "history", "news", "backtest"]
    keyword: str = Field(default="", max_length=50)
    codes: list[Code] = Field(default_factory=list, max_length=30)
    days: int = Field(default=90, ge=5, le=1800)
    backtest: BacktestArgs | None = None


class CheckRequest(StrictModel):
    credentials: Credentials
    service: Literal["tushare", "llm"]


class ChatMessage(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=16000)


class ResearchRequest(StrictModel):
    credentials: Credentials
    question: str = Field(min_length=1, max_length=4000)
    history: list[ChatMessage] = Field(default_factory=list, max_length=8)
    positions: list[Position] = Field(default_factory=list, max_length=30)
    risk: Literal["稳健型", "平衡型", "进取型"] = "稳健型"
    horizon: Literal["短期", "中长期", "长期"] = "中长期"

    @field_validator("history")
    @classmethod
    def history_budget(cls, value):
        if sum(len(m.content) for m in value) > 32000:
            raise ValueError("历史内容过长，请开启新研究")
        return value
