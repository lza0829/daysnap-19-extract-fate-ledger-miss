"""抽取命运账 · 二次同日抽取写闸自动化校验（命运前置拒绝口径）。

断言口径（本库择定并贯彻「命运前置拒绝」）：
1. 首次抽取（成功/失败）与命运行同票落库：营业日、批号、成败、有效镜指纹齐备；
   成功回执绝不允许命运空号。
2. 抹掉命运行后再发起同日抽取：整票拒绝，有效镜余额/批号/状态一律不变。
3. 失败批命运在册时同日再抽必拒（不得半截镜落、不得覆盖有效镜）。
4. 邻日命运不串用：另一日命运行不得改变本日是否可抽。
5. 成功命运在册时可正常同日重抽，重抽重钉命运、旧命运降为历史。
6. 抽写与命运补登抢锁：补登见在途批即失败闭；同一日唯一有效由库层兜底。
7. 命运闸接入回放：命运空号而有效镜在 / 命运=失败 / 指纹不符 → 回放整票拒绝。
8. 只读核证接口输出 日、批号、命运、valid。
"""
import sqlite3

import pytest

from app.db import 连接, 库路径
from app.services.失败注入 import 配置注入, 清除注入
from app.services.抽取 import 补偿悬挂, 日终抽取, 核证
from app.services.错误 import 半截抽取失败, 回放拒绝, 抽取冲突
from app.services.回放 import 回放核对


营业日 = "2026-11-20"
邻日 = "2026-11-21"


def _装流水(日: str = 营业日, 键: str = "fz"):
    from app.services.装载 import 装载流水

    装载流水(
        [
            {"流水号": f"{键}-甲", "账户键": "户甲", "营业日": 日, "变动额": 1000},
            {"流水号": f"{键}-乙", "账户键": "户乙", "营业日": 日, "变动额": 2000},
            {"流水号": f"{键}-丙", "账户键": "户丙", "营业日": 日, "变动额": 3000},
        ],
        f"夹具-{键}",
    )


def _镜(日: str = 营业日) -> dict[str, dict]:
    conn = 连接()
    try:
        return {
            r["账户键"]: dict(r)
            for r in conn.execute(
                "SELECT 账户键, 余额, 抽取批次编号, 状态 FROM 周期快照 WHERE 营业日=?",
                (日,),
            )
        }
    finally:
        conn.close()


def _命运行(日: str = 营业日) -> list[dict]:
    conn = 连接()
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM 抽取命运账表 WHERE 营业日=? ORDER BY 编号", (日,)
            )
        ]
    finally:
        conn.close()


def _有效命运(日: str = 营业日):
    conn = 连接()
    try:
        from app.modules import 抽取命运账 as f

        return f.取有效命运(conn, 日)
    finally:
        conn.close()


def _删命运(日: str = 营业日):
    conn = 连接()
    try:
        conn.execute("DELETE FROM 抽取命运账表 WHERE 营业日=?", (日,))
        conn.commit()
    finally:
        conn.close()


def test_成功抽取同票写命运且指纹相符(干净库):
    _装流水()
    抽 = 日终抽取(营业日, "批-命成-1")
    assert 抽["状态"] == "成功"
    assert 抽["命运"]["命运码"] == "成功" and 抽["命运"]["指纹"]

    行 = _命运行()
    assert len(行) == 1
    命 = 行[0]
    assert (命["营业日"], 命["批号"], 命["命运码"], 命["有效"]) == (
        营业日,
        "批-命成-1",
        "成功",
        1,
    )
    assert 命["指纹"]

    from app.modules import 抽取命运账 as f

    conn = 连接()
    try:
        现镜 = [
            dict(r)
            for r in conn.execute(
                "SELECT 账户键, 余额, 抽取批次编号 FROM 周期快照 "
                "WHERE 营业日=? AND 状态='有效'",
                (营业日,),
            )
        ]
        assert f.计算指纹(现镜, "批-命成-1") == 命["指纹"]
    finally:
        conn.close()


def test_失败批同票写失败命运(干净库):
    _装流水()
    配置注入("", "模拟第三户写入抛错", 3)
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-命败-1")
    finally:
        清除注入()

    命 = _有效命运()
    assert 命 is not None
    assert 命["命运码"] == "失败" and 命["批号"] == "批-命败-1"
    assert 命["有效"] == 1


def test_抹掉命运行后再抽_整票拒绝且有效镜不变(干净库):
    """夹具：抽成功后删命运再抽——甲整拒镜不变（命运前置拒绝口径）。"""
    _装流水()
    日终抽取(营业日, "批-删命-1")
    前 = _镜()
    assert set(前) == {"户甲", "户乙", "户丙"}

    _删命运()
    assert _命运行() == []

    with pytest.raises(抽取冲突):
        日终抽取(营业日, "批-删命-补")

    # 有效镜（余额、批号、状态）一律不动
    后 = _镜()
    assert 后 == 前
    assert all(r["状态"] == "有效" for r in 后.values())
    assert {r["抽取批次编号"] for r in 后.values()} == {"批-删命-1"}
    # 失败闭：不得留下「回执成功命运空且镜已改」
    conn = 连接()
    try:
        assert conn.execute(
            "SELECT COUNT(*) c FROM 抽取批次 WHERE 营业日=? AND 状态='成功'",
            (营业日,),
        ).fetchone()["c"] == 1
    finally:
        conn.close()


def test_失败命运在册时再抽必拒(干净库):
    _装流水()
    配置注入("", "模拟第三户写入抛错", 3)
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-失命-1")
    finally:
        清除注入()
    # 悬挂未补偿本已拒；这里断言即便悬挂被清，失败命运仍拦截
    assert _有效命运()["命运码"] == "失败"
    conn = 连接()
    try:
        conn.execute("DELETE FROM 悬挂镜 WHERE 营业日=?", (营业日,))
        conn.execute("UPDATE 周期快照 SET 状态='有效' WHERE 营业日=?", (营业日,))
        conn.commit()
    finally:
        conn.close()

    前 = _镜()
    with pytest.raises(抽取冲突) as ei:
        日终抽取(营业日, "批-失命-抢")
    assert "失败" in str(ei.value)
    assert _镜() == 前


def test_邻日命运不串用(干净库):
    """营业日 A 有成功命运，不影响营业日 B 的首抽判定。"""
    _装流水(营业日, "fzA")
    日终抽取(营业日, "批-邻-A")
    # 邻日尚无镜、无命运 → 允许首抽
    _装流水(邻日, "fzB")
    抽 = 日终抽取(邻日, "批-邻-B")
    assert 抽["状态"] == "成功"
    assert _有效命运(营业日)["批号"] == "批-邻-A"
    assert _有效命运(邻日)["批号"] == "批-邻-B"
    # 删掉 B 日命运后 B 日拒抽，A 日命运行毫发无损
    _删命运(邻日)
    with pytest.raises(抽取冲突):
        日终抽取(邻日, "批-邻-B2")
    assert _有效命运(营业日)["批号"] == "批-邻-A"


def test_成功命运在册可重抽且重钉命运(干净库):
    _装流水()
    日终抽取(营业日, "批-重-1")
    _装流水(营业日, "fz2")  # 追加流水
    抽 = 日终抽取(营业日, "批-重-2")
    assert 抽["状态"] == "成功" and 抽["命运"]["命运码"] == "成功"

    行 = _命运行()
    assert len(行) == 2
    有效 = [r for r in 行 if r["有效"] == 1]
    assert len(有效) == 1 and 有效[0]["批号"] == "批-重-2"
    历史 = [r for r in 行 if r["有效"] == 0]
    assert len(历史) == 1 and 历史[0]["批号"] == "批-重-1"


def test_命运闸接入回放_空号失败指纹不符皆拒(干净库):
    _装流水()
    日终抽取(营业日, "批-闸-1")
    # 正常放行
    assert 回放核对(营业日, "回放-ok")["通过"] is True

    # 1) 命运空号而有效镜在 → 回放拒绝
    _删命运()
    with pytest.raises(回放拒绝):
        回放核对(营业日, "回放-空号")

    # 2) 命运=失败 → 回放拒绝
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 抽取命运账表(营业日, 批号, 命运码, 指纹, 性质, 有效, 时刻) "
            "VALUES (?,?,?,?,?,1,?)",
            (营业日, "批-伪失败", "失败", "", "首抽", "t"),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(回放拒绝):
        回放核对(营业日, "回放-失败")

    # 3) 有效镜被改写 → 指纹不符 → 回放拒绝
    conn = 连接()
    try:
        conn.execute("DELETE FROM 抽取命运账表 WHERE 营业日=?", (营业日,))
        conn.execute(
            "UPDATE 周期快照 SET 余额=余额+1 WHERE 营业日=? AND 账户键='户甲'", (营业日,)
        )
        conn.commit()
    finally:
        conn.close()
    from app.modules import 抽取命运账 as f

    conn = 连接()
    try:
        现镜 = [
            dict(r)
            for r in conn.execute(
                "SELECT 账户键, 余额, 抽取批次编号 FROM 周期快照 "
                "WHERE 营业日=? AND 状态='有效'",
                (营业日,),
            )
        ]
        conn.execute(
            "INSERT INTO 抽取命运账表(营业日, 批号, 命运码, 指纹, 性质, 有效, 时刻) "
            "VALUES (?,?,?,?,?,1,?)",
            (营业日, "批-旧指纹", "成功", f.计算指纹(现镜, "批-旧指纹"), "首抽", "t"),
        )
        conn.commit()
    finally:
        conn.close()
    # 用旧批号重新钉正确指纹后，篡改余额会使计算指纹不匹配
    conn = 连接()
    try:
        conn.execute(
            "UPDATE 抽取命运账表 SET 指纹='deadbeef' WHERE 营业日=? AND 批号='批-旧指纹'",
            (营业日,),
        )
        conn.commit()
    finally:
        conn.close()
    with pytest.raises(回放拒绝):
        回放核对(营业日, "回放-指纹")


def test_抽写与命运补登抢锁_失败闭无双写(干净库):
    """在途批存在时，任何同日抽写/命运补登都进不来；不会出现命运空而镜已改。"""
    _装流水()
    c1 = sqlite3.connect(库路径)
    c1.row_factory = sqlite3.Row
    c1.execute("PRAGMA foreign_keys = ON")
    try:
        with c1:
            c1.execute(
                "INSERT INTO 抽取闸(营业日, 批次编号, 时刻) VALUES (?,?,?)",
                (营业日, "批-抢锁", "t"),
            )
        with pytest.raises(抽取冲突):
            日终抽取(营业日, "批-抢-补")
        # 镜未被写、命运未写
        assert _镜() == {}
        assert _命运行() == []
    finally:
        c1.close()


def test_命运唯一有效库层兜底(干净库):
    """同一营业日不允许两条有效命运（部分唯一索引失败闭）。"""
    conn = 连接()
    try:
        conn.execute(
            "INSERT INTO 抽取命运账表(营业日, 批号, 命运码, 指纹, 性质, 有效, 时刻) "
            "VALUES (?,?,?,?,?,1,?)",
            (营业日, "批-u1", "成功", "x", "首抽", "t"),
        )
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO 抽取命运账表(营业日, 批号, 命运码, 指纹, 性质, 有效, 时刻) "
                "VALUES (?,?,?,?,?,1,?)",
                (营业日, "批-u2", "成功", "y", "首抽", "t"),
            )
    finally:
        conn.rollback()
        conn.close()


def test_补偿后命运重钉为已补偿可首抽(干净库):
    _装流水()
    配置注入("", "模拟第三户写入抛错", 3)
    try:
        with pytest.raises(半截抽取失败):
            日终抽取(营业日, "批-补命-1")
    finally:
        清除注入()
    assert _有效命运()["命运码"] == "失败"

    报 = 补偿悬挂(营业日, "补偿-命-1")
    assert 报["命运"]["命运码"] == "已补偿"
    assert _有效命运()["命运码"] == "已补偿"

    抽 = 日终抽取(营业日, "批-补命-2")
    assert 抽["状态"] == "成功"
    assert _有效命运()["命运码"] == "成功"


def test_命运补登路径_空号修复且与抽写抢锁失败闭(干净库):
    """抹掉命运后可用补登修复：空号拒抽 → 补登成功 → 可再抽；在途/已有命运时补登闭。"""
    from app.services.抽取 import 补登命运

    _装流水()
    日终抽取(营业日, "批-补修-1")
    批号 = _有效命运()["批号"]
    _删命运()

    with pytest.raises(抽取冲突):
        日终抽取(营业日, "批-补修-抢")

    报 = 补登命运(营业日)
    assert 报["有效镜数"] == 3
    assert _有效命运()["性质"] == "补登"
    assert _有效命运()["批号"] == 批号
    # 补登后回放放行
    assert 回放核对(营业日, "回放-补修")["通过"] is True
    # 已有有效命运 → 重复补登失败闭
    with pytest.raises(抽取冲突):
        补登命运(营业日, "补登-重复")

    # 在途批存在 → 补登失败闭
    _删命运()
    c1 = sqlite3.connect(库路径)
    c1.row_factory = sqlite3.Row
    try:
        with c1:
            c1.execute(
                "INSERT INTO 抽取闸(营业日, 批次编号, 时刻) VALUES (?,?,?)",
                (营业日, "批-在途-补", "t"),
            )
        with pytest.raises(抽取冲突):
            补登命运(营业日, "补登-抢锁")
        assert _有效命运() is None
    finally:
        with c1:
            c1.execute("DELETE FROM 抽取闸 WHERE 营业日=?", (营业日,))
        c1.close()


def test_路由_命运核证只读接口(干净库, 客户端):
    r = 客户端.post("/api/装载夹具", json={"名": "三日连环样例.json"})
    assert r.status_code == 200
    assert 客户端.post("/api/抽取", json={"营业日": 营业日}).status_code == 200

    r = 客户端.get("/api/核证/抽取命运", params={"营业日": 营业日})
    assert r.status_code == 200
    v = r.json()
    assert v["营业日"] == 营业日
    assert v["有效命运"]["命运"] == "成功"
    assert v["有效命运"]["批号"]
    assert v["valid"] is True
    assert v["指纹相符"] is True

    # 指定批号
    r = 客户端.get(
        "/api/核证/抽取命运", params={"营业日": 营业日, "批号": v["有效命运"]["批号"]}
    )
    assert r.json()["指定批"]["批号"] == v["有效命运"]["批号"]


def test_路由_删命运再抽409且镜不变(干净库, 客户端):
    r = 客户端.post("/api/装载夹具", json={"名": "三日连环样例.json"})
    assert r.status_code == 200
    assert 客户端.post("/api/抽取", json={"营业日": 营业日}).status_code == 200
    前 = 客户端.get("/api/快照", params={"营业日": 营业日}).json()

    conn = 连接()
    try:
        conn.execute("DELETE FROM 抽取命运账表 WHERE 营业日=?", (营业日,))
        conn.commit()
    finally:
        conn.close()

    r = 客户端.post("/api/抽取", json={"营业日": 营业日})
    assert r.status_code == 409
    后 = 客户端.get("/api/快照", params={"营业日": 营业日}).json()
    assert 后 == 前

    r = 客户端.post("/api/回放", json={"营业日": 营业日})
    assert r.status_code == 409
