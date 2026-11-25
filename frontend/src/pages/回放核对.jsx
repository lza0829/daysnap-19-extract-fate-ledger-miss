import React, { useEffect, useState } from "react";
import { api } from "../api.js";

export default function 回放核对() {
  const [日, set日] = useState("2026-11-22");
  const [批, set批] = useState([]);
  const [证, set证] = useState(null);
  const [讯, set讯] = useState("");

  const 刷新 = () =>
    Promise.all([
      api.回放批次(),
      api.核证(`?营业日=${encodeURIComponent(日)}`),
    ]).then(([批次行, 证行]) => {
      set批(批次行);
      set证(证行);
    });

  useEffect(() => {
    刷新().catch((e) => set讯(String(e.message || e)));
  }, []);

  return (
    <div>
      <h1 className="题">回放核对</h1>
      <p className="述">
        用前日快照加当日流水重算，对照周期快照，写出差额行。补偿悬挂口径：营业日存在在途抽取或
        未补偿悬挂镜时整票拒绝，悬挂镜不进入发布口径。
      </p>
      <div className="行">
        <input value={日} onChange={(e) => set日(e.target.value)} />
        <button
          className="水"
          disabled={证 ? !证.可否回放 : false}
          onClick={() =>
            api
              .回放(日)
              .then((r) =>
                set讯(
                  `批次 ${r.批次编号} 通过=${r.通过} 相符=${r.相符} 差额=${r.差额} 无快照=${r.无快照}`
                )
              )
              .then(刷新)
              .catch((e) => set讯(String(e.message || e)))
          }
        >
          执行回放
        </button>
        <button className="次" onClick={() => 刷新().catch((e) => set讯(String(e.message || e)))}>
          刷新
        </button>
      </div>
      {讯 ? <p className="讯">{讯}</p> : null}

      {证 ? (
        <div className="段">
          <h2 className="段题">回放前置核证 · {证.营业日}</h2>
          <table>
            <tbody>
              <tr>
                <th>可否回放</th>
                <td>
                  <span className={`徽章 ${证.可否回放 ? "通" : "败"}`}>
                    {证.可否回放 ? "允许" : "整票拒绝"}
                  </span>
                </td>
                <th>valid</th>
                <td>
                  <span className={`徽章 ${证.valid ? "通" : "败"}`}>{String(证.valid)}</span>
                </td>
              </tr>
              <tr>
                <th>在途批次</th>
                <td>{证.在途批次编号 || "—"}</td>
                <th>待补偿悬挂户</th>
                <td>{证.悬挂户.length ? 证.悬挂户.join("、") : "无"}</td>
              </tr>
              {!证.可否回放 ? (
                <tr>
                  <th>拦截原因</th>
                  <td colSpan={3}>
                    {证.在途批次编号
                      ? `抽取批次 ${证.在途批次编号} 在途，闭批前禁止回放`
                      : `存在未补偿悬挂镜（${证.悬挂户.join("、")}），须先在日终抽取页补偿回滚`}
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
      ) : null}

      <div className="段">
        <h2 className="段题">回放批次</h2>
        {!批.length ? (
          <div className="空态">尚无回放</div>
        ) : (
          <table>
            <thead>
              <tr>
                <th>编号</th>
                <th>营业日</th>
                <th>时刻</th>
                <th>报告</th>
              </tr>
            </thead>
            <tbody>
              {批.map((r) => (
                <tr key={r.编号}>
                  <td>{r.编号}</td>
                  <td>{r.营业日}</td>
                  <td>{r.时刻}</td>
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
