import asyncio
import json
from datetime import date

from fastapi.testclient import TestClient
import httpx
import pandas as pd
import pytest

from webapp.main import app
from webapp.models import Credentials, BacktestArgs, ResearchRequest
from webapp.providers import TushareClient, LLMClient, ServiceError
from webapp.agent import research_events
from webapp.backtest import calculate_backtest


def test_health_static_and_no_secret_validation_echo():
    with TestClient(app) as client:
        assert client.get('/healthz').json()['mode'] == 'live-byok'
        assert 'Fin-Agent' in client.get('/').text
        assert client.get('/').headers['x-frame-options'] == 'DENY'
        result = client.post('/api/check', json={'credentials': {'api_key': 'SECRET-MARKER', 'unknown': 'SECRET-MARKER'}, 'service': 'llm'})
        assert result.status_code == 422
        assert 'SECRET-MARKER' not in result.text


def test_no_credential_no_fake_data():
    with TestClient(app) as client:
        result = client.post('/api/research', json={'credentials': {}, 'question': '分析贵州茅台'})
        assert result.status_code == 400
        assert 'Token' in result.json()['error']


def test_url_allowlist():
    for url in ['http://127.0.0.1', 'https://169.254.169.254', 'https://api.deepseek.com.evil.test/v1', 'https://api.deepseek.com/v1?redirect=x']:
        with pytest.raises(ServiceError):
            LLMClient(Credentials(api_key='test', base_url=url), None)


def test_request_local_tokens():
    async def run():
        seen=[]
        async def handler(request):
            body=json.loads(request.content)
            seen.append(body['token'])
            return httpx.Response(200,json={'code':0,'data':{'fields':['cal_date'],'items':[['20261120']]}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            a=TushareClient(Credentials(tushare_token='user-A'),http)
            b=TushareClient(Credentials(tushare_token='user-B'),http)
            await asyncio.gather(a.query('trade_cal'),b.query('trade_cal'))
        assert sorted(seen)==['user-A','user-B']
    asyncio.run(run())


def test_upstream_errors_do_not_echo_credentials():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r:httpx.Response(200,json={'code':-1,'msg':'TOKEN_SECRET'}))) as http:
            with pytest.raises(ServiceError) as error:
                await TushareClient(Credentials(tushare_token='TOKEN_SECRET'),http).query('daily')
            assert 'TOKEN_SECRET' not in str(error.value)
    asyncio.run(run())


def test_research_executes_tool_then_answers():
    async def run():
        calls=[]
        def handler(request):
            body=json.loads(request.content);calls.append(body)
            if len(calls)==1:
                msg={'role':'assistant','content':None,'tool_calls':[{'id':'call1','type':'function','function':{'name':'get_current_time','arguments':'{}'}}]}
            else:
                assert body['messages'][-1]['role']=='tool'
                assert 'E1' in body['messages'][-1]['content']
                msg={'role':'assistant','content':'时间依据 [E1]；尚未查询行情。'}
            return httpx.Response(200,json={'choices':[{'message':msg}],'usage':{'prompt_tokens':10,'completion_tokens':5}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            request=ResearchRequest(credentials=Credentials(api_key='key',tushare_token='token'),question='现在日期？')
            events=[e async for e in research_events(request,http)]
        assert any(e['type']=='evidence' for e in events)
        assert events[-1]['type']=='answer'
        assert events[-1]['usage']['prompt_tokens']==20
    asyncio.run(run())


def test_backtest_uses_next_day_and_equity_matches():
    values=[10,9,8,9,12,13,12,10,8,7]
    frame=pd.DataFrame([{'trade_date':f'202601{i+1:02d}','open':v,'raw_open':v,'close':v,'high':v+1,'low':v-1,'vol':1000,'up_limit':100,'down_limit':1} for i,v in enumerate(values)])
    args=BacktestArgs(ts_code='600519.SH',start_date=date(2026,1,3),end_date=date(2026,1,10),fast=2,slow=3)
    result=calculate_backtest(frame,args)
    assert result['trades']
    assert all(t['signal_date']<t['date'] for t in result['trades'])
    assert result['final_value']==result['equity_curve'][-1]['value']
    assert result['total_return_pct']==result['equity_curve'][-1]['return_pct']
