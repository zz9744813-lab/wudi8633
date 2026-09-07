"""pytest 配置：用内存 SQLite 隔离测试。

工程方案第 44 节：V1 用 SQLite。
测试环境使用内存库，避免污染 data/xuanmirror.db。
"""

from __future__ import annotations

import importlib

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.database import get_session
from app.main import app

# 触发全部模型注册到 SQLModel.metadata。
# 注意：不能用 `import app.models` —— 那会把局部名字 `app` 重新绑定为
# 包模块，覆盖上面 `from app.main import app` 导入的 FastAPI 实例。
importlib.import_module("app.models")


@pytest.fixture(autouse=True)
def _mock_llm(monkeypatch):
    """测试默认用 MockProvider（ABSTAIN），保证快速、确定性、不依赖外部网络。

    真实 LLM 冒烟测试在 tests/test_live_llm.py（默认跳过，手动运行）。
    """
    from app.providers.base import MockProvider

    monkeypatch.setattr(
        "app.agents.base.get_provider", lambda tier="reasoning": MockProvider()
    )


@pytest.fixture(autouse=True)
def _clear_almanac_cache():
    """今日锦囊进程内缓存（round-22 P3）跨测试清零，避免同 (用户,日期) 键互吃。"""
    from app.services import cross_engine

    cross_engine._ALMANAC_CACHE.clear()
    yield
    cross_engine._ALMANAC_CACHE.clear()


@pytest.fixture(autouse=True)
def _disable_edge_gate(monkeypatch):
    """测试环境默认关闭预测质量门槛（MIN_PREDICTION_EDGE=0）。

    测试用 MockProvider，术式信号无真实预测力，edge 门槛会导致 0 预测，
    无法测「预测数据完整性」（可证伪/概率/窗口/null 基线）。
    唯一例外：PRED-01 单独覆盖回真实门槛，专门测「门槛生效」。
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "MIN_PREDICTION_EDGE", 0.0)


@pytest.fixture(autouse=True)
def _disable_calibration_gate(monkeypatch):
    """测试环境默认关闭校准阶段门槛（MIN_CALIBRATION_SAMPLES / MIN_FORMAL_SAMPLES = 0）。

    测试用 MockProvider + 内存库，无已验证样本，校准门槛会导致全部
    产出 RESEARCH 研究样本而非 FROZEN 正式预测，破坏现有「正式预测数据完整性」断言。
    唯一例外：冷启动专项测试单独覆盖回真实门槛，测「研究样本产出 / 三阶段切换」。
    """
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "MIN_CALIBRATION_SAMPLES", 0)
    monkeypatch.setattr(get_settings(), "MIN_FORMAL_SAMPLES", 0)


@pytest.fixture()
def client():
    """带内存数据库的测试客户端。

    StaticPool 是必须的：SQLite 内存库默认每条连接一个独立实例，
    而 TestClient 在 portal 线程执行请求 —— 建表会发生在一个连接上，
    查询却落在另一条空连接上，报 "no such table"。
    StaticPool 强制所有连接复用同一个内存库。
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def _override():
        with Session(engine) as s:
            yield s

    app.dependency_overrides[get_session] = _override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
    engine.dispose()


@pytest.fixture()
def user_id(client: TestClient) -> int:
    """创建一个带出生档案的测试用户。"""
    resp = client.post(
        "/api/users",
        json={
            "user_key": "smoke-user",
            "display_name": "冒烟测试用户",
            "birth_profile": {
                "solar_birth_date": "1990-05-15",
                "solar_birth_time": "14:30",
                "birth_time_known": True,
                "gender": "male",
                "birth_place": "北京",
                "longitude": 116.4,
                "latitude": 39.9,
            },
        },
    )
    assert resp.status_code == 200, resp.text
    return int(resp.json()["user_id"])
