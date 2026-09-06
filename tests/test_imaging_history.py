"""相法特征存档回归测试（round 17）。

核心语义（用户之问的答案）：原图即焚不变；派生特征经确认后入库，
让相法信号无需每次传图即可参与预测闭环、积累已验证样本。
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest
from sqlmodel import Session, SQLModel, create_engine
from sqlmodel.pool import StaticPool

import app.models.core  # noqa: F401
from app.core.base import AdapterQuery
from app.schemas.signal import Domain, TimeScale, TimeWindow
from app.services import imaging


@pytest.fixture(name="sesh")
def _sesh():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False},
                        poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def _features(detected=True):
    return {
        "detected": detected,
        "life_line": {"length_ratio": 0.6, "continuity": 0.6, "curvature": 0.1},
        "head_line": {"length_ratio": 0.5, "continuity": 0.5, "curvature": 0.1},
        "heart_line": {"length_ratio": 0.5, "continuity": 0.5, "curvature": 0.0},
        "palm_width_ratio": 0.8,
    }


def test_save_and_list_roundtrip(sesh):
    rid = imaging.save_record(sesh, 1, "palm", _features(), detected=True)
    rid2 = imaging.save_record(sesh, 1, "palm", _features(), detected=True)
    assert rid and rid2 and rid != rid2
    items = imaging.list_records(sesh, 1, "palm")
    assert len(items) == 2
    # 解读由特征确定性重生成
    assert items[0]["reading"] and any("生命线" in x for x in items[0]["reading"])
    # 未检出也能存（诚实降级档案）
    imaging.save_record(sesh, 1, "palm", {"detected": False}, detected=False)
    items2 = imaging.list_records(sesh, 1, "palm")
    assert any(not it["detected"] for it in items2)


def test_privacy_no_image_path_stored(sesh):
    """隐私铁律：存档只含特征数值，原图路径/二进制绝不入库。"""
    imaging.save_record(sesh, 1, "face", {"detected": False}, detected=False)
    from app.models.metaphysical import FaceFeature
    from sqlmodel import select

    rows = sesh.exec(select(FaceFeature)).all()
    assert len(rows) == 1
    assert rows[0].local_image_path is None
    blob = repr(rows[0].features)
    assert "base64" not in blob.lower() and ".jpg" not in blob.lower()


def test_purge(sesh):
    imaging.save_record(sesh, 1, "palm", _features(), detected=True)
    imaging.save_record(sesh, 1, "face", {"detected": True}, detected=True)
    imaging.save_record(sesh, 2, "palm", _features(), detected=True)
    assert imaging.purge_records(sesh, 1, "palm") == 1
    assert imaging.list_records(sesh, 1, "palm") == []
    assert len(imaging.list_records(sesh, 1, "face")) == 1
    assert len(imaging.list_records(sesh, 2, "palm")) == 1  # 不误伤他人
    assert imaging.purge_records(sesh, 1) == 1


def test_adapters_fall_back_to_stored_features(sesh):
    """无现传照片时，掌/面适配器回退最近一次存档特征 → 信号持续参与闭环。"""
    from app.models.core import BirthProfile

    sesh.add(BirthProfile(user_id=1, solar_birth_date=date(2003, 12, 20),
                          solar_birth_time="15:03", birth_time_known=True, gender="male"))
    sesh.commit()
    imaging.save_record(sesh, 1, "palm", _features(), detected=True)

    start = datetime(2026, 9, 6)
    q = AdapterQuery(user_id=1, domain=Domain.CAREER, target_event="career.x",
                     time_scale=TimeScale.DAY,
                     window=TimeWindow(start=start, end=start + timedelta(hours=24)),
                     target_date=date(2026, 9, 6), target_time="10:00", session=sesh)
    from app.core.base import registry

    palm = registry.get("palm")
    chart = palm.compute_chart(q)
    assert chart.get("from_store") is True
    sigs = palm.signals(q)
    assert sigs and not sigs[0].degraded, "存档特征必须产出真实信号"

    # 无存档的其他用户 → 仍诚实弃权
    q2 = q.model_copy(update={"user_id": 42})
    assert palm.compute_chart(q2) == {}
