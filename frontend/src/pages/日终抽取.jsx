import React, { useEffect, useState } from "react";
import { api } from "../api.js";

export default function 日终抽取() {
  const [日, set日] = useState("2026-11-20");
  const [批, set批] = useState([]);
  const [悬挂, set悬挂] = useState([]);
  const [证, set证] = useState(null);
  const [讯, set讯] = useState("");
  const [注入扫描序, set注入扫描序] = useState(3);

  const 刷新 = () =>
    Promise.all([
      api.抽取批次(),
      api.悬挂镜("?处置=待补偿"),
      api.核证(`?营业日=${encodeURIComponent(日)}`),
    ]).then(([批次行, 悬挂行, 证行]) => {
      set批(批次行);
      set悬挂(悬挂行);
      set证(证行);
    });

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  const 显错 = (e) => {
    try {
      const d = JSON.parse(e.message);
      if (d && d.报告) return `批次失败（已悬挂 ${d.报告.悬挂数} 户）：${d.原因}`;
      return String(d?.detail || e.message);
    } catch {
      return String(e.message || e);
    }
  };

  return (
    <div>
      <h1 className="题">日终抽取</h1>
      <p className="述">
        按营业日写周期快照：前日余额 + 当日流水净额。半截失败走补偿悬挂口径：已写户挂起而非有效，
        未补偿整票拒绝回放，补偿清除后方可重抽与回放。
      </p>
      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} placeholder="YYYY-MM-DD" />
        <button
          onClick={() =>
            api
              .抽取(日)
              .then((r) =>
                set讯(`批次 ${r.批次编号} 成功 写入=${r.写入} 盖写=${r.盖写} 断档=${r.断档}`)
              )
              .then(刷新)
              .catch((e) => {
                set讯(显错(e));
                刷新().catch(() => {});
              })
          }
        >
          执行抽取
        </button>
        <button
          className="水"
          onClick={() =>
            api
              .补偿(日)
              .then((r) => set讯(`补偿 ${r.补偿批次编号} 清除悬挂=${r.清除悬挂} 户：${r.悬挂户.join(",")}`))
              .then(刷新)
              .catch((e) => set讯(String(e.message || e)))
          }
        >
          补偿回滚悬挂
        </button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>
          刷新
        </button>
      </div>
      <div className="行">
        <span className="述" style={{ margin: 0 }}>
          失败注入（夹具）：扫描序第
        </span>
        <input
          style={{ width: "4.5rem" }}
          type="number"
          min={1}
          value={注入扫描序}
          onChange={(e) => set注入扫描序(Number(e.target.value))}
        />
        <span className="述" style={{ margin: 0 }}>户写入抛错</span>
        <button
          className="次"
          onClick={() =>
            api
              .失败注入({ 账户键: "", 扫描序: 注入扫描序, 原因: "模拟第三户写入抛错" })
              .then(() => set讯(`已配置失败注入：扫描序第 ${注入扫描序} 户（单次生效）`))
              .catch((e) => set讯(String(e.message || e)))
          }
        >
          配置注入
        </button>
        <button
          className="次"
          onClick={() => api.清除失败注入().then(() => set讯("已清除失败注入"))}
        >
          清除注入
        </button>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}

      {证 ? (
        <div className="段">
          <h2 className="段题">失败/悬挂核证 · {证.营业日}（口径：{证.口径}）</h2>
          <table>
            <tbody>
              <tr>
                <th>valid</th>
                <td>
                  <span className={`徽章 ${证.valid ? "通" : "败"}`}>{String(证.valid)}</span>
                </td>
                <th>可否回放</th>
                <td>
                  <span className={`徽章 ${证.可否回放 ? "通" : "败"}`}>
                    {证.可否回放 ? "允许" : "整票拒绝"}
                  </span>
                </td>
              </tr>
              <tr>
                <th>在途批次</th>
                <td>{证.在途批次编号 || "—"}</td>
                <th>待补偿悬挂户</th>
                <td>{证.悬挂户.length ? 证.悬挂户.join("、") : "无"}</td>
              </tr>
              <tr>
                <th>失败批次</th>
                <td colSpan={3}>
                  {证.失败批次.length
                    ? 证.失败批次
                        .map((b) => `${b.编号}（${b.失败原因} @ ${b.时刻}）`)
                        .join("；")
                    : "无"}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="段">
        <h2 className="段题">待补偿悬挂镜（{悬挂.length}）</h2>
        {!悬挂.length ? (
          <div className="空态">无悬挂镜</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>失败批号</th>
                <th>账户键</th>
                <th>营业日</th>
                <th>余额</th>
                <th>处置</th>
              </tr>
            </thead>
            <tbody>
              {悬挂.map((r) => (
                <tr key={r.编号}>
                  <td>{r.失败批次编号}</td>
                  <td>{r.账户键}</td>
                  <td>{r.营业日}</td>
                  <td>{r.余额}</td>
                  <td>
                    <span className="徽章 败">{r.处置}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="段">
        <h2 className="段题">抽取批次</h2>
        {!批.length ? (
          <div className="空态">尚无抽取</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>时刻</th>
                <th>状态</th>
                <th>报告</th>
              </tr>
            </thead>
            <tbody>
              {批.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td>
                  <td>{r.营业日}</td>
                  <td>{r.时刻}</td>
                  <td>
                    <span className={`徽章 ${r.状态 === "成功" ? "通" : "败"}`}>{r.状态}</span>
                  </td>
                  <td>{typeof r.报告 === "object" ? JSON.stringify(r.报告) : r.报告}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
