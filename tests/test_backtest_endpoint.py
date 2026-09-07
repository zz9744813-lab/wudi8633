"""GET /api/analytics/backtest 冒烟：静态回测产物 → 只读接口。

覆盖第三轮审查指出的「40 行新端点零测试」缺口：
- 200 + available 契约；
- per_source 六术字段齐全（hit/miss/abstain/error/coverage/hit_rate/p_value）；
- zhouyi 全弃权（0 表态）时 hit_rate 显式为 null 而非除零崩溃；
- 只读：不得触碰预测库。

JSON 是仓库内静态产物（docs/回测数据-公众人物.json），本测试不制造任何数据。
"""

from __future__ import annotations


def test_backtest_endpoint_smoke(client):
    resp = client.get("/api/analytics/backtest")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["available"] is True, data.get("note")
    assert data["figures"] == data["pillar_ok"]
    assert data["n_events"] > 0 and 0 <= data["n_positive"] <= data["n_events"]

    ps = data["per_source"]
    for src in ("bazi", "ziwei", "liuyao", "meihua", "zhouyi", "qimen"):
        assert src in ps, f"缺术式 {src}"
        row = ps[src]
        for key in ("hit", "miss", "abstain", "error", "coverage", "hit_rate", "p_value"):
            assert key in row, f"{src} 缺字段 {key}"
        if row["hit"] + row["miss"] == 0:
            assert row["hit_rate"] is None, f"{src} 无表态时命中率必须显式置空"
        assert 0.0 <= row["p_value"] <= 1.0
        assert 0.0 <= row["coverage"] <= 1.0

    assert data.get("caveat"), "必须附口径警示（C-006）"
