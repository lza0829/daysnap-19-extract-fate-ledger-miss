
"""半截抽取回滚（补偿悬挂）自动化校验。

断言口径（全局唯一：补偿悬挂）：
1. 扫描序第三户（户甲）写入抛错 → 前两户（户丙、户乙）本批镜转悬挂挂失败批号，
   无任何「失败批有效镜」残留；
2. 失败批可查；核证接口输出批号/悬挂户/可否回放=false/valid=false；
3. 未补偿当日回放整票 409 拒绝，悬挂镜不进发布口径（快照/差额读不到）；
4. 补偿（或整票清理）后重抽成功，核证翻真，回放放行；
5. 失败与同日重抽并发：悬挂未补偿重抽 409，在途闸拒绝并发，双批有效镜不并存；
6. 差额页不得把悬挂当已发布口径。
"""
import pytest

from app.db import 连接
from app.services.失败注入 import 配置注入, 清除注入
from app.services.抽取 import 补偿悬挂, 日终抽取, 核证
from app.services.错误 import 半截抽取失败, 回放拒绝, 抽取冲突
from app.services.回放 import 回放核对

营业日 = "2026-11-20"

扫描序 = ["户丙", "户乙", "户甲"]
前两户 = set(扫描序[:2])
第三户 = 扫描序[2]
全部户 = set(扫描序)


def _第三户注入():
    配置注入("", "模拟第三户写入抛错", 3)


def _装流水():
    from app.services.装载 import 装载流水

    装载流水(
        [
            {"流水号": "t-甲", "账户键": "户甲", "营业日": 营业日, "变动额": 1000},
            {"流水号": "t-乙", "账户键": "户乙", "营业日": 营业日, "变动额": 2000},
            {"流水号": "t-丙", "账户键": "户丙", "营业日": 营业日, "变动额": 3000},
        ],
        "夹具-半截失败",
    )


def _有效快照户(日: str) -> set[str]:
    conn = 连接()
    try:
        return {
            r["账户键"]
            for r in conn.execute(
                "SELECT 账户键 FROM 周期快照 WHERE 营业日=? AND 状态='有效'", (日,)
            )
        }
    finally:
        conn.close()


def _悬挂快照户(日: str) -> set[str]:
    conn = 连接()
    try:
        return {
            r["账户键"]
            for r in conn.execute(
                "SELECT 账户键 FROM 周期快照 WHERE 营业日=? AND 状态='悬挂'", (日,)
            )
        }
    finally:
        conn.close()


def test_失败后已写户悬挂且无有效镜残留(干净库):
    _装流水()
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败) as ei:
            日终抽取(营业日, "批-败-1")
        批 = ei.value.报告
        assert 批["状态"] == "失败"
        assert 批["失败户"] == 第三户
        assert set(批["悬挂户"]) == 前两户
    finally:
        清除注入()

    
    assert _有效快照户(营业日) == set()
    assert _悬挂快照户(营业日) == 前两户

    conn = 连接()
    try:
        
        行 = conn.execute(
            "SELECT 失败批次编号, 处置 FROM 悬挂镜 WHERE 营业日=?", (营业日,)
        ).fetchall()
        assert {r["失败批次编号"] for r in 行} == {"批-败-1"}
        assert all(r["处置"] == "待补偿" for r in 行)
        
        失败批 = conn.execute(
            "SELECT 状态, 失败原因 FROM 抽取批次 WHERE 编号='批-败-1'"
        ).fetchone()
        assert 失败批["状态"] == "失败" and 失败批["失败原因"]
        
        assert conn.execute(
            "SELECT COUNT(*) c FROM 抽取闸 WHERE 营业日=?", (营业日,)
        ).fetchone()["c"] == 0
    finally:
        conn.close()


def test_核证接口字段与口径(干净库):
    _装流水()
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-败-2")
    finally:
        清除注入()

    v = 核证(营业日, "批-败-2")
    assert v["口径"] == "补偿悬挂"
    assert set(v["悬挂户"]) == 前两户
    assert v["在途批次编号"] == ""
    assert v["可否回放"] is False
    assert v["valid"] is False
    assert len(v["失败批次"]) == 1 and v["失败批次"][0]["编号"] == "批-败-2"
    
    assert v["本批残留有效镜"] == []


def test_未补偿回放整票拒绝且悬挂不入发布口径(干净库):
    _装流水()
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-败-3")
    finally:
        清除注入()

    
    with pytest.raises(回放拒绝):
        回放核对(营业日)
    conn = 连接()
    try:
        
        assert conn.execute("SELECT COUNT(*) c FROM 回放批次").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) c FROM 差额行").fetchone()["c"] == 0
        
        assert _有效快照户(营业日) == set()
    finally:
        conn.close()


def test_补偿后重抽成功且回放放行(干净库):
    _装流水()
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-败-4")
    finally:
        清除注入()

    报 = 补偿悬挂(营业日, "补偿-1")
    assert 报["清除悬挂"] == 2
    assert set(报["悬挂户"]) == 前两户
    assert _悬挂快照户(营业日) == set()

    v0 = 核证(营业日)
    assert v0["valid"] is True and v0["可否回放"] is True

    
    抽 = 日终抽取(营业日, "批-成-1")
    assert 抽["状态"] == "成功"
    assert _有效快照户(营业日) == {"户甲", "户乙", "户丙"}
    conn = 连接()
    try:
        
        n = conn.execute(
            "SELECT COUNT(*) c FROM 周期快照 WHERE 营业日=? AND 状态='有效'", (营业日,)
        ).fetchone()["c"]
        assert n == 3
    finally:
        conn.close()

    
    回 = 回放核对(营业日, "回放-1")
    assert 回["通过"] is True and 回["差额"] == 0
    v1 = 核证(营业日, "批-成-1")
    assert v1["valid"] is True and v1["可否回放"] is True
    assert {s["账户键"] for s in v1["本批残留有效镜"]} == {"户甲", "户乙", "户丙"}


def test_悬挂未补偿重抽拒绝_并发闭(干净库):
    _装流水()
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-败-5")
    finally:
        清除注入()

    
    with pytest.raises(抽取冲突):
        日终抽取(营业日, "批-抢-1")
    
    conn = 连接()
    try:
        成功批 = conn.execute(
            "SELECT COUNT(*) c FROM 抽取批次 WHERE 营业日=? AND 状态='成功'", (营业日,)
        ).fetchone()["c"]
        assert 成功批 == 0
        assert _有效快照户(营业日) == set()
    finally:
        conn.close()


def test_在途闸拒绝真正并发_无双批有效镜(干净库):
    """两个连接同日同时抽取：持闸者跑，第二个立即在途冲突。"""
    import sqlite3

    _装流水()
    from app.db import 库路径

    c1 = sqlite3.connect(库路径)
    c1.row_factory = sqlite3.Row
    c1.execute("PRAGMA foreign_keys = ON")
    try:
        
        with c1:
            c1.execute(
                "INSERT INTO 抽取闸(营业日, 批次编号, 时刻) VALUES (?,?,?)",
                (营业日, "批-在途", "t"),
            )
        
        with pytest.raises(抽取冲突):
            日终抽取(营业日, "批-撞闸")
        with c1:
            c1.execute("DELETE FROM 抽取闸 WHERE 营业日=?", (营业日,))
        
        抽 = 日终抽取(营业日, "批-顺抽")
        assert 抽["状态"] == "成功"
        assert _有效快照户(营业日) == 全部户
    finally:
        c1.close()


def test_差额页不把悬挂当已发布口径(干净库):
    _装流水()
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-败-6")
    finally:
        清除注入()

    conn = 连接()
    try:
        
        发布 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 周期快照 WHERE 营业日=? AND 状态='有效'", (营业日,)
            )
        ]
        assert 发布 == []
        悬挂 = [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 周期快照 WHERE 营业日=? AND 状态='悬挂'", (营业日,)
            )
        ]
        assert {r["账户键"] for r in 悬挂} == 前两户
        assert all(r["抽取批次编号"] == "批-败-6" for r in 悬挂)
    finally:
        conn.close()


def test_盖写场景失败后补偿恢复旧镜(干净库):
    """失败批盖写了旧有效镜：补偿须恢复失败前口径而非误删。"""
    from app.services.装载 import 装载流水

    前日 = "2026-11-19"
    装载流水(
        [{"流水号": "p-甲", "账户键": "户甲", "营业日": 前日, "变动额": 100}],
        "前批",
    )
    日终抽取(前日, "批-前")
    assert _有效快照户(前日) == 全部户

    装载流水(
        [{"流水号": "d-甲", "账户键": "户甲", "营业日": 前日, "变动额": 50}],
        "后批",
    )
    _第三户注入()
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(前日, "批-盖败")
    finally:
        清除注入()
    
    assert _悬挂快照户(前日) == 前两户
    补偿悬挂(前日, "补偿-盖")
    
    assert _悬挂快照户(前日) == set()
    conn = 连接()
    try:
        镜 = {
            r["账户键"]: r["余额"]
            for r in conn.execute(
                "SELECT 账户键, 余额 FROM 周期快照 WHERE 营业日=? AND 状态='有效'",
                (前日,),
            )
        }
        assert 镜 == {"户甲": 100, "户乙": 0, "户丙": 0}
    finally:
        conn.close()



def test_路由端到端_失败409_回放409_补偿后放行(干净库, 客户端):
    r = 客户端.post("/api/装载夹具", json={"名": "半截抽取失败样例.json"})
    assert r.status_code == 200
    r = 客户端.post("/api/抽取", json={"营业日": 营业日})
    assert r.status_code == 409
    体 = r.json()["detail"]
    assert set(体["报告"]["悬挂户"]) == 前两户

    r = 客户端.get("/api/核证", params={"营业日": 营业日})
    assert r.status_code == 200
    v = r.json()
    assert v["可否回放"] is False and v["valid"] is False
    assert set(v["悬挂户"]) == 前两户

    
    r = 客户端.get("/api/快照", params={"营业日": 营业日})
    assert r.json() == []
    r = 客户端.get("/api/快照", params={"营业日": 营业日, "含悬挂": "true"})
    assert {x["账户键"] for x in r.json()} == 前两户

    
    r = 客户端.post("/api/回放", json={"营业日": 营业日})
    assert r.status_code == 409 and "悬挂" in r.json()["detail"]

    
    assert 客户端.post("/api/补偿", json={"营业日": 营业日}).status_code == 200
    assert 客户端.post("/api/抽取", json={"营业日": 营业日}).status_code == 200
    r = 客户端.post("/api/回放", json={"营业日": 营业日})
    assert r.status_code == 200 and r.json()["通过"] is True
    r = 客户端.get("/api/核证", params={"营业日": 营业日})
    assert r.json()["valid"] is True and r.json()["可否回放"] is True
