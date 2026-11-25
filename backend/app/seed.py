from datetime import datetime, timezone

from app.db import 连接

建表 = """
CREATE TABLE IF NOT EXISTS 抽取命运账表 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  营业日 TEXT, 批号 TEXT, 命运码 TEXT, 指纹 TEXT, 时刻 TEXT,
  时刻 TEXT NOT NULL DEFAULT '',
  载荷 TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS 台参 (
  键 TEXT PRIMARY KEY,
  值 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 账户 (
  账户键 TEXT PRIMARY KEY,
  户名 TEXT NOT NULL,
  币种 TEXT NOT NULL DEFAULT 'CNY',
  开户营业日 TEXT NOT NULL,
  状态 TEXT NOT NULL DEFAULT '正常'
);
CREATE TABLE IF NOT EXISTS 变动流水 (
  流水号 TEXT PRIMARY KEY,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  发生时 TEXT NOT NULL,
  变动额 INTEGER NOT NULL,
  摘要 TEXT NOT NULL DEFAULT '',
  批次编号 TEXT NOT NULL DEFAULT '',
  命运 TEXT NOT NULL DEFAULT '已入账'
);
CREATE TABLE IF NOT EXISTS 抽取批次 (
  编号 TEXT PRIMARY KEY,
  营业日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}',
  状态 TEXT NOT NULL DEFAULT '成功',
  失败原因 TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS 周期快照 (
  快照键 TEXT PRIMARY KEY,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  余额 INTEGER NOT NULL,
  抽取批次编号 TEXT NOT NULL,
  来源 TEXT NOT NULL DEFAULT '日终抽取',
  状态 TEXT NOT NULL DEFAULT '有效',
  UNIQUE(账户键, 营业日)
);
-- 半截抽取回滚（补偿悬挂口径）：失败批已写户的悬挂镜登记
CREATE TABLE IF NOT EXISTS 悬挂镜 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  失败批次编号 TEXT NOT NULL,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  快照键 TEXT NOT NULL,
  余额 INTEGER NOT NULL,
  时刻 TEXT NOT NULL,
  补偿批次编号 TEXT NOT NULL DEFAULT '',
  处置 TEXT NOT NULL DEFAULT '待补偿',
  是否新插 INTEGER NOT NULL DEFAULT 1,
  旧余额 INTEGER,
  旧抽取批次编号 TEXT NOT NULL DEFAULT '',
  UNIQUE(账户键, 营业日)
);
-- 同日抽取并发闸：同一营业日同时只许一个在途批次
CREATE TABLE IF NOT EXISTS 抽取闸 (
  营业日 TEXT PRIMARY KEY,
  批次编号 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 回放批次 (
  编号 TEXT PRIMARY KEY,
  营业日 TEXT NOT NULL,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS 差额行 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  回放批次编号 TEXT NOT NULL,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  快照余额 INTEGER,
  回放余额 INTEGER,
  差额 INTEGER NOT NULL,
  原因 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 断档记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  账户键 TEXT NOT NULL,
  缺营业日 TEXT NOT NULL,
  检出批次编号 TEXT NOT NULL,
  处置 TEXT NOT NULL DEFAULT '待处理',
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 跳变记录 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  账户键 TEXT NOT NULL,
  营业日 TEXT NOT NULL,
  前日余额 INTEGER,
  当日余额 INTEGER,
  流水净额 INTEGER,
  偏离 INTEGER NOT NULL,
  检出批次编号 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 旁置簿 (
  编号 INTEGER PRIMARY KEY AUTOINCREMENT,
  原因 TEXT NOT NULL,
  账户键 TEXT NOT NULL DEFAULT '',
  营业日 TEXT NOT NULL DEFAULT '',
  载荷 TEXT NOT NULL,
  时刻 TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS 装载批次 (
  编号 TEXT PRIMARY KEY,
  时刻 TEXT NOT NULL,
  报告 TEXT NOT NULL DEFAULT '{}'
);
"""

空桩模块 = [
    "开户首日无前日",
    "同日重复抽取幂等",
    "流水迟到补抽",
    "销户终镜",
    "负余额闸",
    "币种隔离",
    "快照封印写闸",
    "回放权威择定",
    "断档补齐",
    "断档旁置",
    "跳变阈值",
    "账户分片抽取",
    "抽取时钟漂移",
    "流水序缺口",
    "并户继承镜",
    "子户汇总镜",
    "假期跳日日历",
    "差额分桶",
    "三日连环守恒",
    "抽取重试预算",
    "跨日切点策略",
]


def 此刻() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _迁移(conn) -> None:
    """对已存在的旧库补列/补表（CREATE TABLE IF NOT EXISTS 只覆盖新库）。"""
    列 = {r["name"] for r in conn.execute("PRAGMA table_info(抽取批次)")}
    if "状态" not in 列:
        conn.execute("ALTER TABLE 抽取批次 ADD COLUMN 状态 TEXT NOT NULL DEFAULT '成功'")
    if "失败原因" not in 列:
        conn.execute("ALTER TABLE 抽取批次 ADD COLUMN 失败原因 TEXT NOT NULL DEFAULT ''")
    悬挂列 = {r["name"] for r in conn.execute("PRAGMA table_info(悬挂镜)")}
    if 悬挂列 and "是否新插" not in 悬挂列:
        conn.execute("ALTER TABLE 悬挂镜 ADD COLUMN 是否新插 INTEGER NOT NULL DEFAULT 1")
        conn.execute("ALTER TABLE 悬挂镜 ADD COLUMN 旧余额 INTEGER")
        conn.execute(
            "ALTER TABLE 悬挂镜 ADD COLUMN 旧抽取批次编号 TEXT NOT NULL DEFAULT ''"
        )


def 初始化库() -> None:
    conn = 连接()
    conn.executescript(建表)
    _迁移(conn)
    if conn.execute("SELECT COUNT(*) AS c FROM 台参").fetchone()["c"] == 0:
        for k, v in {
            "站名": "日镜账",
            "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
            "跳变阈值分": "0",
            "断档处置": "记断档并继续",
            "缺前日策略": "以零为开端",
            "重复抽取": "盖写同日快照",
        }.items():
            conn.execute("INSERT INTO 台参(键, 值) VALUES (?, ?)", (k, v))
    if conn.execute("SELECT COUNT(*) AS c FROM 账户").fetchone()["c"] == 0:
        for 键, 名, 日 in [
            ("户甲", "甲收单户", "2026-11-01"),
            ("户乙", "乙结算户", "2026-11-01"),
            ("户丙", "丙备用户", "2026-11-05"),
        ]:
            conn.execute(
                "INSERT INTO 账户(账户键, 户名, 币种, 开户营业日, 状态) VALUES (?,?,?,?,?)",
                (键, 名, "CNY", 日, "正常"),
            )
    conn.commit()
    conn.close()
