import React from "react";
import { createPortal } from "react-dom";
import { Check, Crown, Edit3, Landmark, Loader2, Lock, MessageSquare, Plus, ScrollText, Send, Star, Trash2, Undo2, X } from "lucide-react";
import { api } from "../api";
import { ExtractionView } from "./extraction";
import { FullscreenModal, MinisterPortrait, cacheBust } from "./hud";
import { formatClosedEffect } from "../format";
import { ManualIssueEditor } from "./situation";
import type { ChatDisplayMessage, ChatMessage, ClosedIssue, Directive, EndingPayload, GameState, HistoryDetail, HistoryTurnItem, Minister, SecretOrder, StructuredDirective, StructuredDirectiveTemplate, Suggestion } from "../types";

const PROGRESS_LEVEL_CN: Record<string, string> = {
  verygood: "大进",
  good: "有进",
  normal: "平常",
  bad: "受挫",
  verybad: "大挫",
  resolved: "已成",
  failed: "败坏",
};

export function formatReportText(text: string): string {
  return String(text || "").replace(/(进展：\s*)([A-Za-z_]+)/g, (_, prefix: string, raw: string) => {
    const key = raw.toLowerCase();
    return `${prefix}${PROGRESS_LEVEL_CN[key] || raw}`;
  });
}

export function renderInlineBoldText(text: string): React.ReactNode {
  const source = String(text || "");
  const nodes: React.ReactNode[] = [];
  const boldPattern = /\*\*([^*]+?)\*\*/g;
  let cursor = 0;
  let match: RegExpExecArray | null;

  while ((match = boldPattern.exec(source)) !== null) {
    if (match.index > cursor) {
      nodes.push(source.slice(cursor, match.index));
    }
    nodes.push(<strong key={`bold-${match.index}`}>{match[1]}</strong>);
    cursor = match.index + match[0].length;
  }

  if (!nodes.length) return source;
  if (cursor < source.length) {
    nodes.push(source.slice(cursor));
  }
  return nodes;
}

export function renderReportText(text: string): React.ReactNode {
  return renderInlineBoldText(formatReportText(text));
}

// 史册详文：把当月奏章里对玩家隐去的内幕（<secret>…</secret>）显出来，标成「【密】…」便于
// 一眼看出哪些是当时未上达御前的暗线；把喂 extractor 的技术前缀（圣意亲裁/作弊强制）换成入戏
// 的小标题，保留亲裁裁断正文（「陛下御断…朱批…」本就入戏）。再走一遍进展档位中文化。
export function formatDetailNarrative(text: string): string {
  let s = String(text || "");
  // 圣意亲裁前缀：technical 指令段换成入戏标题，保留其后的逐条裁断。
  s = s.replace(
    /【圣意亲裁·结算优先】[^\n]*\n/,
    "── 本月圣意亲裁 ──\n",
  );
  // 作弊强制前缀（若有）：同样去技术腔。
  s = s.replace(/【[^】]*强制结算[^】]*】[^\n]*\n/, "── 钦定强制项 ──\n");
  s = s.replace(
    /<secret>\s*([\s\S]*?)\s*<\/secret>/g,
    (_, inner: string) => `【密】${inner}`,
  ).replace(/<\/?secret>/g, "");
  return formatReportText(s);
}

export function ReportModal({ report, onClose }: { report: string; onClose: () => void }) {
  return (
    <FullscreenModal title="月末奏疏" subtitle="推演结果" bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document modal-scroll">
        <div className="document-section">
          <pre className="memorial-text">{renderReportText(report)}</pre>
        </div>
      </article>
    </FullscreenModal>
  );
}

export function EndingModal({ ending, onClose }: { ending: EndingPayload; onClose: () => void }) {
  const lastTimeline = ending.timeline?.[ending.timeline.length - 1];
  const endingDate = lastTimeline ? `${lastTimeline.year}年${lastTimeline.period}月` : "终局";
  const timelineCount = ending.timeline?.length ?? 0;

  return (
    <FullscreenModal
      title="终章定论"
      subtitle="崇祯一朝，盖棺论定"
      bgClass="modal-bg-state modal-bg-ending"
      onClose={onClose}
    >
      <article className="state-document ending-document modal-scroll">
        <div className="ending-hero">
          <div className="ending-seal" aria-hidden="true">
            <Crown size={34} />
          </div>
          <div className="ending-hero-copy">
            <p>大明国史馆录</p>
            <h2>{ending.label}</h2>
            <span>{endingDate} · 第 {timelineCount || 1} 卷</span>
          </div>
        </div>

        <section className="ending-verdict-card" aria-label="结局总评">
          <div className="ending-section-kicker">
            <ScrollText size={17} />
            <span>国史编纂官总评</span>
          </div>
          <pre className="ending-summary-text">{renderInlineBoldText(ending.summary || "（无总评）")}</pre>
        </section>

        {ending.timeline && ending.timeline.length > 0 && (
          <section className="ending-chronicle" aria-label="逐月历程">
            <div className="ending-section-kicker">
              <Landmark size={17} />
              <span>崇祯一朝逐月历程</span>
            </div>
            <ol className="ending-timeline">
              {ending.timeline.map((it) => (
                <li key={it.turn} className="ending-timeline-item">
                  <div className="ending-timeline-date">
                    <b>{it.year}</b>
                    <span>{it.period}月</span>
                  </div>
                  <div className="ending-timeline-body">
                    {it.chapter ? (
                      <p className="ending-timeline-chapter">{it.chapter}</p>
                    ) : null}
                    {it.decree_brief ? (
                      <p className="ending-timeline-decree">诏：{it.decree_brief}</p>
                    ) : null}
                    {it.effect_brief ? (
                      <p className="ending-timeline-effect">效：{it.effect_brief}</p>
                    ) : null}
                  </div>
                </li>
              ))}
            </ol>
          </section>
        )}
      </article>
    </FullscreenModal>
  );
}

export function SecretOrdersModal({
  orders,
  onClose,
  onOpenMinister,
  onDelete,
}: {
  orders: SecretOrder[];
  onClose: () => void;
  onOpenMinister: (name: string) => void;
  onDelete: (order: SecretOrder) => void | Promise<void>;
}) {
  const [tab, setTab] = React.useState<"active" | "pending_review" | "done" | "failed" | "all">("active");
  const [selectedOrder, setSelectedOrder] = React.useState<SecretOrder | null>(null);
  const statusLabel: Record<string, string> = {
    active: "进行中",
    pending_review: "待核议",
    done: "已完成",
    failed: "已失败",
    cancelled: "已撤销",
  };
  const statusCls: Record<string, string> = {
    active: "so-active",
    pending_review: "so-pending",
    done: "so-done",
    failed: "so-failed",
    cancelled: "so-cancelled",
  };
  const tabs: { key: typeof tab; label: string }[] = [
    { key: "active",         label: `进行中 (${orders.filter(o => o.status === "active").length})` },
    { key: "pending_review", label: `待核议 (${orders.filter(o => o.status === "pending_review").length})` },
    { key: "done",           label: `已完成 (${orders.filter(o => o.status === "done").length})` },
    { key: "failed",         label: `已失败 (${orders.filter(o => o.status === "failed").length})` },
    { key: "all",            label: `全部 (${orders.length})` },
  ];
  const visible = tab === "all" ? orders : orders.filter(o => o.status === tab);
  return (
    <FullscreenModal title="密令进度" subtitle={`共 ${orders.length} 条密令记录`} bgClass="modal-bg-edict" onClose={onClose}>
      <article className="state-document modal-scroll">
        <div className="so-tabs">
          {tabs.map(t => (
            <button key={t.key} className={`so-tab${tab === t.key ? " so-tab-active" : ""}`} onClick={() => setTab(t.key)}>
              {t.label}
            </button>
          ))}
        </div>
        <div className="secret-orders-list">
          {visible.length === 0 && <p className="so-empty">暂无此类密令。</p>}
          {visible.map((o) => (
            <button
              key={o.id}
              type="button"
              className={`secret-order-card secret-order-card-button ${statusCls[o.status] || ""}`}
              onClick={() => setSelectedOrder(o)}
            >
              <div className="so-header">
                <span className="so-title"><Lock size={13} />{o.title}</span>
                <span className={`so-status ${statusCls[o.status] || ""}`}>{statusLabel[o.status] || o.status}</span>
                <button
                  className="so-delete"
                  title="删除此密令"
                  aria-label={`删除密令 ${o.title}`}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (window.confirm(`确定删除密令〔${o.title}〕？此操作不可撤销。`)) {
                      void onDelete(o);
                    }
                  }}
                >
                  <Trash2 size={13} />
                </button>
              </div>
              <div className="so-meta">第 {o.year_issued} 年 {o.period_issued} 月下令 · 承办：{o.minister_name}</div>
              <div className="so-open-hint">点击查看密令详情</div>
              {o.status === "active" && (
                <button
                  className="secondary-action so-goto"
                  onClick={(event) => {
                    event.stopPropagation();
                    onClose();
                    onOpenMinister(o.minister_name);
                  }}
                >
                  <MessageSquare size={13} />
                  召见 {o.minister_name}
                </button>
              )}
            </button>
          ))}
        </div>
      </article>
      {selectedOrder ? (
        <SecretOrderDetailDialog
          order={selectedOrder}
          statusLabel={statusLabel}
          statusCls={statusCls}
          onClose={() => setSelectedOrder(null)}
          onOpenMinister={(name) => {
            setSelectedOrder(null);
            onClose();
            onOpenMinister(name);
          }}
          onDelete={(order) => {
            setSelectedOrder(null);
            void onDelete(order);
          }}
        />
      ) : null}
    </FullscreenModal>
  );
}

export function SecretOrderDetailDialog({
  order,
  statusLabel,
  statusCls,
  onClose,
  onOpenMinister,
  onDelete,
}: {
  order: SecretOrder;
  statusLabel: Record<string, string>;
  statusCls: Record<string, string>;
  onClose: () => void;
  onOpenMinister: (name: string) => void;
  onDelete: (order: SecretOrder) => void;
}) {
  const deadlineText = order.due_turn
    ? `第 ${order.due_turn} 回合核议${order.due_turn <= order.turn_issued ? "" : `（限 ${order.due_turn - order.turn_issued} 个月）`}`
    : "无硬期限";
  const detailRows = [
    ["编号", `#${order.id}`],
    ["承办", order.minister_name],
    ["下令", `第 ${order.year_issued} 年 ${order.period_issued} 月 · 回合 ${order.turn_issued}`],
    ["期限", deadlineText],
    ["重要", String(order.importance || 0)],
    ["标签", order.tags?.length ? order.tags.join("、") : "无"],
  ];
  return (
    <div className="so-detail-layer" role="dialog" aria-modal="true" aria-label={`密令详情：${order.title}`}>
      <div className="so-detail-scrim" onClick={onClose} />
      <section className="so-detail-dialog">
        <header className="so-detail-header">
          <div>
            <span className={`so-status ${statusCls[order.status] || ""}`}>{statusLabel[order.status] || order.status}</span>
            <h2>{order.title}</h2>
          </div>
          <button className="icon-button" aria-label="关闭密令详情" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="so-detail-body">
          <dl className="so-detail-grid">
            {detailRows.map(([label, value]) => (
              <div key={label}>
                <dt>{label}</dt>
                <dd>{value}</dd>
              </div>
            ))}
          </dl>
          <SecretOrderDetailBlock title="密令正文" text={order.content || "未记正文。"} />
          {order.sim_note ? <SecretOrderDetailBlock title="月度动向" text={order.sim_note} tone="green" /> : null}
          {order.result ? (
            <SecretOrderDetailBlock title={order.status === "active" ? "承办回报" : "执行结果"} text={order.result} tone="green" />
          ) : null}
        </div>
        <footer className="so-detail-actions">
          {order.status === "active" ? (
            <button className="secondary-action" onClick={() => onOpenMinister(order.minister_name)}>
              <MessageSquare size={15} />
              召见 {order.minister_name}
            </button>
          ) : null}
          <button
            className="secondary-action so-detail-delete"
            onClick={() => {
              if (window.confirm(`确定删除密令〔${order.title}〕？此操作不可撤销。`)) {
                onDelete(order);
              }
            }}
          >
            <Trash2 size={15} />
            删除此密令
          </button>
          <button className="secondary-action" onClick={onClose}>返回列表</button>
        </footer>
      </section>
    </div>
  );
}

export function SecretOrderDetailBlock({ title, text, tone = "default" }: { title: string; text: string; tone?: "default" | "green" }) {
  return (
    <section className={`so-detail-block so-detail-block-${tone}`}>
      <h3>{title}</h3>
      <p>{renderInlineBoldText(text)}</p>
    </section>
  );
}

export function ClosedIssuesModal({ items, onClose }: { items: ClosedIssue[]; onClose: () => void }) {
  const resolved = items.filter((i) => i.status === "resolved");
  const failed = items.filter((i) => i.status === "failed");
  const dropped = items.filter((i) => i.status === "dropped");
  return (
    <FullscreenModal title="局势了结" subtitle={`本月共 ${items.length} 条局势了结`} bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document modal-scroll">
        {resolved.length ? <ClosedGroup title="已结案" items={resolved} cls="resolved" /> : null}
        {failed.length ? <ClosedGroup title="已崩坏" items={failed} cls="failed" /> : null}
        {dropped.length ? <ClosedGroup title="已撤旨" items={dropped} cls="dropped" /> : null}
      </article>
    </FullscreenModal>
  );
}

export function ClosedGroup({ title, items, cls }: { title: string; items: ClosedIssue[]; cls: string }) {
  return (
    <div className="document-section">
      <h3 className={`closed-group-title ${cls}`}>{title}</h3>
      <ul className="closed-list">
        {items.map((it) => (
          <li key={it.id} className={`closed-card ${cls}`}>
            <div className="closed-card-head">
              <b>#{it.id} {it.title}</b>
              <span>{cls === "resolved" ? it.bar_good_meaning : it.bar_bad_meaning}</span>
            </div>
            {it.stage_text ? <p className="closed-card-stage">{it.stage_text}</p> : null}
            <div className="closed-card-effect">{formatClosedEffect(it.effect)}</div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HistoryModal({ onClose }: { onClose: () => void }) {
  const [turns, setTurns] = React.useState<HistoryTurnItem[]>([]);
  const [listLoading, setListLoading] = React.useState(true);
  const [listError, setListError] = React.useState("");
  const [selectedTurn, setSelectedTurn] = React.useState<number | null>(null);
  const [detail, setDetail] = React.useState<HistoryDetail | null>(null);
  const [detailLoading, setDetailLoading] = React.useState(false);
  const [detailError, setDetailError] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const data = await api<{ turns: HistoryTurnItem[] }>("/api/history/turns");
        if (!alive) return;
        const list: HistoryTurnItem[] = data.turns || [];
        setTurns(list);
        if (list.length) setSelectedTurn(list[list.length - 1].turn);
      } catch (e: any) {
        if (alive) setListError(e?.message || "加载失败");
      } finally {
        if (alive) setListLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  React.useEffect(() => {
    if (selectedTurn == null) return;
    let alive = true;
    setDetailLoading(true);
    setDetailError("");
    (async () => {
      try {
        const data = await api<HistoryDetail>(`/api/history/turn/${selectedTurn}`);
        if (alive) setDetail(data);
      } catch (e: any) {
        if (alive) setDetailError(e?.message || "加载失败");
      } finally {
        if (alive) setDetailLoading(false);
      }
    })();
    return () => { alive = false; };
  }, [selectedTurn]);

  const subtitle = turns.length ? `共 ${turns.length} 月存档` : "尚无存档";

  return (
    <FullscreenModal title="史册：历代奏报与诏书" subtitle={subtitle} bgClass="modal-bg-state" onClose={onClose}>
      <div className="history-modal-body">
        <aside className="history-turn-list">
          {listLoading ? <p className="long-copy">加载中…</p> : null}
          {listError ? <p className="long-copy">加载失败：{listError}</p> : null}
          {!listLoading && !listError && turns.length === 0 ? (
            <p className="long-copy">尚无存档回合。</p>
          ) : null}
          <ul>
            {turns.slice().reverse().map((t) => {
              const active = t.turn === selectedTurn;
              const tags: string[] = [];
              if (t.has_report) tags.push("奏报");
              if (t.has_directive) tags.push("诏");
              if (t.has_extraction) tags.push("册");
              return (
                <li key={t.turn}>
                  <button
                    className={`history-turn-item ${active ? "active" : ""}`}
                    onClick={() => setSelectedTurn(t.turn)}
                  >
                    <b>{t.year} 年 {t.period} 月</b>
                    <small>第 {t.turn} 回合 · {tags.join(" / ") || "—"}</small>
                  </button>
                </li>
              );
            })}
          </ul>
        </aside>
        <article className="history-detail modal-scroll">
          <HistoryDetailView
            loading={detailLoading}
            error={detailError}
            detail={detail}
            selectedTurn={selectedTurn}
          />
        </article>
      </div>
    </FullscreenModal>
  );
}

export function HistoryDetailView({
  loading,
  error,
  detail,
  selectedTurn,
}: {
  loading: boolean;
  error: string;
  detail: HistoryDetail | null;
  selectedTurn: number | null;
}) {
  if (selectedTurn == null) return <div className="document-section"><p className="long-copy">请从左侧择月。</p></div>;
  if (loading) return <div className="document-section"><p className="long-copy">加载中…</p></div>;
  if (error) return <div className="document-section"><p className="long-copy">加载失败：{error}</p></div>;
  if (!detail || !detail.exists) return <div className="document-section"><p className="long-copy">该回合无存档。</p></div>;

  return (
    <>
      {detail.decree_text ? (
        <section className="document-section">
          <h3 className="extraction-section-title">本月诏书</h3>
          <pre className="memorial-text">{detail.decree_text}</pre>
        </section>
      ) : null}

      {detail.directives.length ? (
        <section className="document-section">
          <h3 className="extraction-section-title">已颁草案（{detail.directives.length} 道）</h3>
          <ul className="history-directive-list">
            {detail.directives.map((d) => (
              <li key={d.id} className="history-directive-item">
                <div className="history-directive-head">
                  <b>#{d.id}</b>
                  {d.event_title ? <span>事项：{d.event_title}</span> : null}
                  {d.actor ? <span>主官：{d.actor}</span> : null}
                  {d.skill_id ? <span>技能：{d.skill_id}</span> : null}
                  <span className="history-directive-source">{d.source}</span>
                </div>
                <pre className="memorial-text">{d.text}</pre>
                {d.notes ? <div className="history-directive-notes">备注：{d.notes}</div> : null}
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      {detail.report ? (
        <section className="document-section">
          <h3 className="extraction-section-title">月末邸报奏报</h3>
          <pre className="memorial-text">{renderReportText(detail.report)}</pre>
        </section>
      ) : null}

      {detail.extraction && detail.extraction.exists && detail.extraction.narrative
        && detail.extraction.narrative.trim() !== (detail.report || "").trim() ? (
        <section className="document-section">
          <h3 className="extraction-section-title">邸报详文 · 起居注（含当时未上达御前的内幕）</h3>
          <pre className="memorial-text">{renderInlineBoldText(formatDetailNarrative(detail.extraction.narrative))}</pre>
        </section>
      ) : null}

      {detail.extraction && detail.extraction.exists ? (
        <section className="document-section">
          <h3 className="extraction-section-title">邸报详明（extractor 解析）</h3>
          <ExtractionView data={detail.extraction} loading={false} error="" />
        </section>
      ) : null}
    </>
  );
}

export function PreviousSummary({ summary }: { summary: string }) {
  if (!summary) {
    return <p className="long-copy">登基伊始，尚无上月回奏。</p>;
  }
  const lines = summary.split("\n").map((line) => line.trim()).filter(Boolean);
  const rows = lines
    .map((line) => {
      const idx = line.indexOf("：");
      if (idx <= 0) return null;
      return { label: line.slice(0, idx), value: line.slice(idx + 1) };
    })
    .filter((row): row is { label: string; value: string } => !!row && !!row.value);

  if (!rows.length) {
    return <p className="long-copy">{summary}</p>;
  }

  return (
    <table className="summary-table">
      <tbody>
        {rows.map((row) => (
          <tr key={row.label}>
            <th>{row.label}</th>
            <td>{row.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function StateModal({ state }: { state: GameState }) {
  const report = state.last_report || state.previous_summary;
  return (
    <article className="state-document modal-scroll">
      <section className="document-section">
        {report
          ? <pre className="memorial-text">{renderReportText(report)}</pre>
          : <div className="empty-note">尚无上月奏报。</div>}
      </section>
    </article>
  );
}

export function BriefReport({ title, items }: { title: string; items: string[] }) {
  return (
    <article>
      <h2>{title}</h2>
      <ul className="brief-list">
        {items.map((item) => <li key={`${title}-${item}`}>{item}</li>)}
      </ul>
    </article>
  );
}


export function ChatModal({
  minister,
  portraitPrefix,
  chat,
  suggestions,
  pendingDirectives,
  pendingUserMessage,
  streamingMinisterMessage,
  chatNotice,
  composerHint,
  input,
  busy,
  error,
  secretOrders,
  onInput,
  onSend,
  onHint,
  onFavorite,
  onConfirmDirective,
  onRejectDirective,
  onUndoLast,
  onOpenEdict,
  onArchive,
  onClose,
}: {
  minister: Minister;
  portraitPrefix: string;
  chat: ChatMessage[];
  suggestions: Suggestion[];
  pendingDirectives: Directive[];
  pendingUserMessage: string;
  streamingMinisterMessage: string;
  chatNotice: string;
  composerHint: string;
  input: string;
  busy: string;
  error: string;
  secretOrders: SecretOrder[];
  onInput: (value: string) => void;
  onSend: (text?: string) => void;
  onHint: (value: string) => void;
  onFavorite: () => void;
  onConfirmDirective: (directiveId: number) => void;
  onRejectDirective: (directiveId: number) => void;
  onUndoLast: () => void;
  onOpenEdict: () => void;
  onArchive?: () => void;
  onClose: () => void;
}) {
  const isCustom = minister.portrait_id?.startsWith("custom:");
  const portraitPrimary = isCustom
    ? `/portraits/custom/${encodeURIComponent(minister.name)}?t=${cacheBust(minister.portrait_id!)}`
    : `/portraits/${portraitPrefix}${minister.id ?? minister.name}.png`;
  const portraitFallback = !isCustom && minister.portrait_id
    ? `/portraits/${minister.portrait_id}.png`
    : undefined;
  const chatLogRef = React.useRef<HTMLDivElement | null>(null);
  const inputRef = React.useRef<HTMLTextAreaElement | null>(null);
  const displayMessages: ChatDisplayMessage[] = [...chat];
  const chatDisabled = minister.status === "dead" || minister.status === "offstage";
  const canArchive = minister.origin === "runtime" && minister.status !== "active";

  if (pendingUserMessage) {
    displayMessages.push({ role: "user", content: pendingUserMessage, pending: true });
  }
  if (streamingMinisterMessage) {
    displayMessages.push({ role: "minister", content: streamingMinisterMessage, pending: true });
  }

  React.useEffect(() => {
    inputRef.current?.focus();
  }, [minister.name]);

  React.useEffect(() => {
    const node = chatLogRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [minister.name, chat, pendingUserMessage, streamingMinisterMessage, chatNotice, busy, error]);

  const handleSend = () => {
    if (chatDisabled) return;
    onSend(input);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.nativeEvent.isComposing || event.keyCode === 229) return;
    if (chatDisabled) return;
    if (event.key !== "Enter" || event.shiftKey) return;
    event.preventDefault();
    onSend(input);
  };

  const sendSuggestion = (suggestion: Suggestion) => {
    if (suggestion.prefix) {
      // 填前缀到输入框，不直接发送，光标跟到末尾
      onInput(suggestion.text);
      setTimeout(() => inputRef.current?.focus(), 0);
    } else {
      if (chatDisabled) return;
      onSend(suggestion.text);
    }
  };

  return (
    <div className="chat-full-grid">
      <aside className="modal-pane minister-side">
        <div className="minister-profile">
          <div>
            <h2>{minister.name}</h2>
            <p>
              {minister.status !== "active" && (
                <span className={`minister-status status-${minister.status}`}>{minister.status_label}</span>
              )}
              {minister.office && <span className="profile-office">{minister.office}</span>}
            </p>
          </div>
          <button className="icon-button" aria-label="收藏大臣" onClick={onFavorite}>
            <Star size={16} fill={minister.favorite ? "currentColor" : "none"} />
          </button>
        </div>
        <p className="profile-copy">{minister.summary}</p>
        <button className="secondary-action" onClick={onOpenEdict}>
          <ScrollText size={15} />
          转入诏书草案
        </button>
        {canArchive && onArchive && (
          <button className="secondary-action archive-character-action" onClick={onArchive}>
            <Trash2 size={15} />
            归档人物
          </button>
        )}
        <div className="chat-portrait-wrap">
          <MinisterPortrait primary={portraitPrimary} fallback={portraitFallback} name={minister.name} />
        </div>
        {secretOrders.length > 0 && (
          <div className="chat-secret-orders">
            <div className="secret-orders-label"><Lock size={12} />密令</div>
            {secretOrders.map((o) => (
              <div key={o.id} className="secret-order-item">
                <div className="secret-order-title">{o.title}</div>
                <div className="secret-order-meta">第 {o.year_issued} 年 {o.period_issued} 月下令</div>
                {o.content && <div className="secret-order-content">{renderInlineBoldText(o.content)}</div>}
                {o.sim_note && <div className="secret-order-content"><b>月度动向：</b>{renderInlineBoldText(o.sim_note)}</div>}
                {o.result && <div className="secret-order-content"><b>承办回报：</b>{renderInlineBoldText(o.result)}</div>}
              </div>
            ))}
          </div>
        )}
      </aside>

      <section className="modal-pane chat-main">
        <div className="chat-log" ref={chatLogRef}>
          {displayMessages.map((message, index) => (
            <div className={`chat-message ${message.role} ${message.pending ? "pending" : ""}`} key={`${message.role}-${index}-${message.content}`}>
              <span>{message.role === "user" ? "朕" : minister.name}</span>
              <p>{renderInlineBoldText(message.content)}</p>
            </div>
          ))}
          {busy && !streamingMinisterMessage && (
            <div className="chat-message minister thinking">
              <span>{minister.name}</span>
              <p><Loader2 size={14} />大臣思索中...</p>
            </div>
          )}
          {chatNotice && <div className="chat-system-note">{chatNotice}</div>}
          {error && <div className="chat-system-note danger" role="alert">{error}</div>}
        </div>
        {pendingDirectives.length > 0 && (
          <div className="chat-pending-directives" role="region" aria-label="待朱批大臣拟旨">
            {pendingDirectives.map((directive) => (
              <div className="chat-pending-item" key={directive.id}>
                <p><b>#{directive.id}</b> {directive.text}</p>
                <div className="chat-pending-tools">
                  <button className="vermilion-yes" onClick={() => onConfirmDirective(directive.id)} disabled={!!busy}><Check size={14} />准奏</button>
                  <button className="vermilion-no" onClick={() => onRejectDirective(directive.id)} disabled={!!busy}><X size={14} />驳</button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="chat-composer">
          <div className="hitl-bar">
            {suggestions.map((suggestion) => (
              <button
                key={`${suggestion.label}-${suggestion.text}`}
                onClick={() => sendSuggestion(suggestion)}
                disabled={!!busy}
                title={suggestion.prefix ? `填入前缀：${suggestion.text}` : suggestion.text}
                className={suggestion.prefix ? "hitl-prefix" : ""}
              >
                {suggestion.label}
              </button>
            ))}
          </div>
          <label className="chat-input">
            <span>问话</span>
            <textarea
              ref={inputRef}
              value={input}
              onChange={(event) => {
                onInput(event.target.value);
                if (composerHint) onHint("");
              }}
              onKeyDown={handleKeyDown}
              disabled={chatDisabled}
              placeholder={chatDisabled ? "此人当前不可召对，但仍可查看旧话与取消收藏。" : "问大臣军情、钱粮、地方，或要求他拟旨... Enter 发送，Shift+Enter 换行"}
            />
          </label>
          <div className="composer-actions">
            <button className={`primary-action ${!input.trim() ? "is-empty" : ""}`} onClick={handleSend} disabled={!!busy || chatDisabled}>
              <Send size={15} />
              发送
            </button>
            <button
              className="secondary-action composer-undo"
              onClick={onUndoLast}
              disabled={!!busy || chatDisabled || !chat.some((m) => m.role === "minister")}
              title="撤回最后一轮召对（删除上一问一答，并清掉大臣对应记忆）"
            >
              <Undo2 size={15} />
              撤回
            </button>
            <button className="secondary-action composer-exit" onClick={onClose}>
              <X size={15} />
              退出召对
            </button>
            {composerHint && <div className="composer-hint">{composerHint}</div>}
          </div>
        </div>
      </section>
    </div>
  );
}

export function EdictModal({
  state,
  ministers,
  directiveText,
  editingDirectiveId,
  editingDirectiveText,
  decree,
  report,
  busy,
  error,
  structuredDirectiveTemplates,
  onDirectiveTextChange,
  onEditingTextChange,
  onCreateDirective,
  onStartEdit,
  onCancelEdit,
  onSaveDirective,
  onDeleteDirective,
  onWriteDecree,
  onRewriteDecree,
  onSaveDecree,
  onResetDecree,
  onIssueDecree,
  onConfirmDirective,
  onRejectDirective,
  onConfirmAllDirectives,
  onSaveStructuredDirective,
  onDeleteStructuredDirective,
  onGoToCourtChat,
  onIssueCreated,
}: {
  state: GameState;
  ministers: Minister[];
  directiveText: string;
  editingDirectiveId: number | null;
  editingDirectiveText: string;
  decree: string;
  report: string;
  busy: string;
  error: string;
  structuredDirectiveTemplates: StructuredDirectiveTemplate[];
  onDirectiveTextChange: (value: string) => void;
  onEditingTextChange: (value: string) => void;
  onCreateDirective: () => void;
  onStartEdit: (directive: Directive) => void;
  onCancelEdit: () => void;
  onSaveDirective: (directive: Directive) => void;
  onDeleteDirective: (directiveId: number) => void;
  onWriteDecree: () => void;
  onRewriteDecree: () => void;
  onSaveDecree: (text: string) => void;
  onResetDecree: () => void;
  onIssueDecree: () => void;
  onConfirmDirective: (directiveId: number) => void;
  onRejectDirective: (directiveId: number) => void;
  onConfirmAllDirectives: () => void;
  onSaveStructuredDirective: (directive: StructuredDirective | null, templateId: string, fields: Record<string, string>) => Promise<void>;
  onDeleteStructuredDirective: (directiveId: number) => void;
  onGoToCourtChat: () => void;
  onIssueCreated: () => void | Promise<void>;
}) {
  const pendingDirectives = React.useMemo(() => state.directives.filter((d) => d.status === "pending"), [state.directives]);
  const draftDirectives = React.useMemo(() => state.directives.filter((d) => d.status !== "pending"), [state.directives]);
  const allDirectives = React.useMemo(() => [...pendingDirectives, ...draftDirectives], [pendingDirectives, draftDirectives]);
  const structuredDirectives = state.structured_directives || [];
  const hasPending = pendingDirectives.length > 0;
  const [decreeDraft, setDecreeDraft] = React.useState(decree);
  const [dialogDirectiveId, setDialogDirectiveId] = React.useState<number | null>(null);
  const [structuredEditorTarget, setStructuredEditorTarget] = React.useState<StructuredDirective | null | undefined>(undefined);
  const [issueEditorOpen, setIssueEditorOpen] = React.useState(false);
  const decreeIssueCount = React.useMemo(
    () => (state.issues || []).filter((i) => (i.origin_kind === "decree" || (!i.origin_kind && i.is_manual)) && (i.kind === "situation" || i.kind === "initiative")).length,
    [state.issues],
  );
  const maxDecreeIssues = state.max_decree_issues ?? 10;
  const decreeIssueFull = decreeIssueCount >= maxDecreeIssues;
  const activeIssues = React.useMemo(
    () => (state.issues || []).filter((i) => !i.status || i.status === "active"),
    [state.issues],
  );
  const assignedActiveIssueCount = React.useMemo(
    () => activeIssues.filter((i) => (i.assignee || "").trim()).length,
    [activeIssues],
  );
  const dialogDirective = allDirectives.find((d) => d.id === dialogDirectiveId) || null;
  const openDirectiveDialog = (directive: Directive) => {
    setDialogDirectiveId(directive.id);
    onStartEdit(directive);
  };
  const closeDirectiveDialog = () => {
    setDialogDirectiveId(null);
    onCancelEdit();
  };
  React.useEffect(() => {
    setDecreeDraft(decree);
  }, [decree]);
  React.useEffect(() => {
    if (dialogDirectiveId && !allDirectives.some((d) => d.id === dialogDirectiveId)) {
      setDialogDirectiveId(null);
    }
  }, [dialogDirectiveId, allDirectives]);

  // 分幕：随 decree/report 态切。无诏文=御案理政；有诏文未结算=诏书御览；已结算=颁诏奏章。
  const phase: "desk" | "review" | "issued" = report ? "issued" : decree ? "review" : "desk";

  if (phase === "issued") {
    return (
      <div className="edict-stage edict-stage-issued">
        {error && <div className="error-line" role="alert">{error}</div>}
        <DecreeScroll text={decree} sealed />
        {report ? (
          <section className="edict-gazette">
            <h2>月末奏章</h2>
            <pre>{renderReportText(report)}</pre>
          </section>
        ) : null}
      </div>
    );
  }

  if (phase === "review") {
    return (
      <div className="edict-stage edict-stage-review">
        {busy && <div className="busy-line"><Loader2 size={15} />{busy}...</div>}
        {error && <div className="error-line" role="alert">{error}</div>}
        <DecreeScroll text={decreeDraft} editable onChange={setDecreeDraft} />
        <div className="edict-review-bar">
          <button
            className="seal-btn-ghost"
            onClick={onResetDecree}
            disabled={!!busy}
          >
            <Edit3 size={15} />返工改稿
          </button>
          <button
            className="seal-btn-ghost"
            onClick={onRewriteDecree}
            disabled={!!busy}
            title="重新调用拟诏生成，会覆盖当前诏文"
          >
            <Undo2 size={15} />重写拟旨
          </button>
          {decreeDraft !== decree && (
            <button
              className="seal-btn-save"
              onClick={() => onSaveDecree(decreeDraft)}
              disabled={!!busy || !decreeDraft.trim()}
            >
              <Check size={15} />存改
            </button>
          )}
          <button
            className="seal-btn-issue"
            onClick={onIssueDecree}
            disabled={!!busy || decreeDraft !== decree}
            title={decreeDraft !== decree ? "请先存改诏文" : "盖玉玺，诏告天下"}
          >
            盖玺颁布
          </button>
        </div>
      </div>
    );
  }

  // phase === "desk"：御案理政
  return (
    <div className="edict-stage edict-stage-desk">
      <div className="desk-columns">
        <section className="desk-pane desk-memorials">
          <div className="directive-list-head">
            <h2>本月指令{(allDirectives.length + structuredDirectives.length) ? ` · ${allDirectives.length + structuredDirectives.length} 道` : ""}</h2>
            {pendingDirectives.length > 1 && (
              <button className="vermilion-yes pending-confirm-all" onClick={onConfirmAllDirectives} disabled={!!busy}>
                <Check size={14} />全部准奏
              </button>
            )}
          </div>
          <div className="directive-list">
            {structuredDirectives.map((directive) => (
              <div className="directive-list-row structured" key={`structured-${directive.id}`}>
                <button
                  type="button"
                  className="directive-list-main"
                  onClick={() => setStructuredEditorTarget(directive)}
                >
                  <span className="directive-list-no">令#{directive.id}</span>
                  <span className="directive-list-text">{directive.compiled_text}</span>
                  <span className="directive-list-status draft">指令</span>
                  <span className="directive-list-source">{directive.category || directive.title}</span>
                </button>
                <div className="directive-list-actions">
                  <button onClick={() => setStructuredEditorTarget(directive)} disabled={!!busy}><Edit3 size={14} />改</button>
                  <button onClick={() => onDeleteStructuredDirective(directive.id)} disabled={!!busy}><Trash2 size={14} />删</button>
                </div>
              </div>
            ))}
            {allDirectives.map((directive) => (
              directive.status === "pending" ? (
                <div className={`directive-list-row pending${dialogDirectiveId === directive.id ? " selected" : ""}`} key={directive.id}>
                  <button
                    type="button"
                    className="directive-list-main"
                    onClick={() => openDirectiveDialog(directive)}
                  >
                    <span className="directive-list-no">#{directive.id}</span>
                    <span className="directive-list-text">{directive.text}</span>
                    <span className="directive-list-status pending">待批</span>
                    <span className="directive-list-source">{directive.source}</span>
                  </button>
                  <div className="directive-list-actions">
                    <button className="vermilion-yes" onClick={() => { setDialogDirectiveId(null); onConfirmDirective(directive.id); }} disabled={!!busy}><Check size={14} />准</button>
                    <button className="vermilion-no" onClick={() => { setDialogDirectiveId(null); onRejectDirective(directive.id); }} disabled={!!busy}><X size={14} />驳</button>
                  </div>
                </div>
              ) : (
                <div className={`directive-list-row${dialogDirectiveId === directive.id ? " selected" : ""}`} key={directive.id}>
                  <button
                    type="button"
                    className="directive-list-main"
                    onClick={() => openDirectiveDialog(directive)}
                  >
                    <span className="directive-list-no">#{directive.id}</span>
                    <span className="directive-list-text">{directive.text}</span>
                    <span className="directive-list-status draft">草案</span>
                    <span className="directive-list-source">{directive.source}</span>
                  </button>
                  <div className="directive-list-actions">
                    <button onClick={() => openDirectiveDialog(directive)} disabled={!!busy}><Edit3 size={14} />改</button>
                    <button onClick={() => onDeleteDirective(directive.id)} disabled={!!busy}><Trash2 size={14} />删</button>
                  </div>
                </div>
              )
            ))}
            {!allDirectives.length && !structuredDirectives.length && (
              <div className="empty-note">
                无新指令。本月可直接结算，由各局势承办人照前旨推进{assignedActiveIssueCount ? ` · ${assignedActiveIssueCount} 件有承办` : ""}。
              </div>
            )}
          </div>
        </section>

        <section className="desk-pane desk-compose">
          <h2>固定指令</h2>
          <button className="desk-add-btn fixed-directive-add" onClick={() => setStructuredEditorTarget(null)} disabled={!!busy || !structuredDirectiveTemplates.length}>
            <Plus size={14} />新增固定指令
          </button>
          <h2>御笔自拟</h2>
          <textarea
            value={directiveText}
            onChange={(event) => onDirectiveTextChange(event.target.value)}
            placeholder="例如：命毕自严核拨关宁、山海关、蓟镇辽饷一百五十二万两..."
          />
          <button className="desk-add-btn" onClick={onCreateDirective} disabled={!!busy || !directiveText.trim()}>
            <Edit3 size={14} />新增草案
          </button>
          <div className="desk-manual-issue">
            <button
              type="button"
              className="desk-add-issue-btn"
              onClick={() => setIssueEditorOpen(true)}
              disabled={!!busy || decreeIssueFull}
              title={decreeIssueFull ? `decree 局势已达上限（${maxDecreeIssues}），可在主菜单游戏设置调高` : "新建一条可追踪的长期圣旨/局势"}
            >
              <Landmark size={14} />＋ 固定长期圣旨
            </button>
            <small className="desk-manual-issue-hint">本质即新建局势 · {decreeIssueCount} / {maxDecreeIssues}</small>
          </div>
          {busy && <div className="busy-line"><Loader2 size={15} />{busy}...</div>}
          {error && <div className="error-line" role="alert">{error}</div>}
        </section>
      </div>

      {dialogDirective ? (
        <div className="directive-edit-layer" role="dialog" aria-modal="true" aria-label={`编辑指令 #${dialogDirective.id}`}>
          <div className="directive-edit-scrim" onClick={closeDirectiveDialog} />
          <section className="directive-edit-dialog">
            <header className="directive-edit-dialog-head">
              <div>
                <span className={`directive-list-status ${dialogDirective.status === "pending" ? "pending" : "draft"}`}>
                  {dialogDirective.status === "pending" ? "待批" : "草案"}
                </span>
                <h3>#{dialogDirective.id} {dialogDirective.source}</h3>
              </div>
              <button className="icon-button" aria-label="关闭编辑弹窗" onClick={closeDirectiveDialog}><X size={18} /></button>
            </header>
            {dialogDirective.notes ? <small className="directive-edit-note">{dialogDirective.notes}</small> : null}
            <textarea value={editingDirectiveText} onChange={(event) => onEditingTextChange(event.target.value)} />
            <footer className="directive-edit-dialog-actions">
              {dialogDirective.status === "pending" ? (
                <>
                  <button className="vermilion-yes" onClick={() => { setDialogDirectiveId(null); onConfirmDirective(dialogDirective.id); }} disabled={!!busy}><Check size={14} />准</button>
                  <button className="vermilion-no" onClick={() => { setDialogDirectiveId(null); onRejectDirective(dialogDirective.id); }} disabled={!!busy}><X size={14} />驳</button>
                </>
              ) : null}
              <button className="seal-btn-save" onClick={() => { setDialogDirectiveId(null); onSaveDirective(dialogDirective); }} disabled={!!busy || !editingDirectiveText.trim()}><Check size={15} />保存</button>
              <button className="seal-btn-ghost" onClick={closeDirectiveDialog} disabled={!!busy}>取消</button>
            </footer>
          </section>
        </div>
      ) : null}

      {issueEditorOpen ? (
        <ManualIssueEditor
          editing={null}
          regions={(state.regions || []).filter((r) => (r.controlled_by ?? "ming") === "ming").map((r) => ({ id: r.id, name: r.name }))}
          ministers={ministers}
          presetTrees={state.preset_trees}
          onClose={() => setIssueEditorOpen(false)}
          onSaved={async () => {
            setIssueEditorOpen(false);
            await onIssueCreated();
          }}
        />
      ) : null}

      {structuredEditorTarget !== undefined ? (
        <StructuredDirectiveEditor
          editing={structuredEditorTarget}
          templates={structuredDirectiveTemplates}
          state={state}
          onClose={() => setStructuredEditorTarget(undefined)}
          onSaved={async (editing, templateId, fields) => {
            await onSaveStructuredDirective(editing, templateId, fields);
            setStructuredEditorTarget(undefined);
          }}
        />
      ) : null}

      <div className="desk-footer">
        {hasPending && <small className="pending-hint">尚有 {pendingDirectives.length} 道大臣拟旨待朱批（准/驳），核定后方可结算。</small>}
        <button className="seal-btn-ghost" onClick={onGoToCourtChat} disabled={!!busy}>
          <MessageSquare size={15} />去庭议
        </button>
        <button
          className="seal-btn-compose"
          onClick={draftDirectives.length ? onWriteDecree : onIssueDecree}
          disabled={!!busy || hasPending}
          title={draftDirectives.length ? "拟写正式诏书" : structuredDirectives.length ? "无新诏，按固定指令结算本月" : "无新诏，结算本月承办推进"}
        >
          {draftDirectives.length ? "拟诏 →" : structuredDirectives.length ? "核销指令 →" : "结束本月"}
        </button>
      </div>
    </div>
  );
}


function StructuredDirectiveEditor({
  editing,
  templates,
  state,
  onClose,
  onSaved,
}: {
  editing: StructuredDirective | null;
  templates: StructuredDirectiveTemplate[];
  state: GameState;
  onClose: () => void;
  onSaved: (editing: StructuredDirective | null, templateId: string, fields: Record<string, string>) => Promise<void>;
}) {
  const initialTemplateId = editing?.template_id || templates[0]?.id || "";
  const [templateId, setTemplateId] = React.useState(initialTemplateId);
  const [fields, setFields] = React.useState<Record<string, string>>(() => editing?.fields || {});
  const [pickerFieldKey, setPickerFieldKey] = React.useState<string>("");
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const template = templates.find((t) => t.id === templateId) || templates[0] || null;
  const isFieldRequired = React.useCallback((field: StructuredDirectiveTemplate["fields"][number]) => {
    if (field.required) return true;
    const cond = field.required_when;
    if (!cond) return false;
    return (cond.values || []).includes(fields[cond.field] || "");
  }, [fields]);
  const isFieldVisible = React.useCallback((field: StructuredDirectiveTemplate["fields"][number]) => {
    if (template?.id === "tax_reform" && field.key === "scope") {
      return REGIONAL_FISCAL_LINES.has(fields.tax_type || "");
    }
    return true;
  }, [fields.tax_type, template?.id]);

  React.useEffect(() => {
    if (!template) return;
    setFields((current) => {
      const next: Record<string, string> = {};
      for (const field of template.fields || []) {
        next[field.key] = current[field.key] || "";
      }
      return next;
    });
  }, [template?.id]);

  React.useEffect(() => {
    if (template?.id !== "tax_reform") return;
    if (!fields.tax_type || REGIONAL_FISCAL_LINES.has(fields.tax_type)) return;
    if (!fields.scope) return;
    setFields((current) => ({ ...current, scope: "" }));
  }, [fields.scope, fields.tax_type, template?.id]);

  const save = async () => {
    if (!template) {
      setErr("没有可用模板");
      return;
    }
    for (const field of template.fields || []) {
      if (!isFieldVisible(field)) continue;
      if (isFieldRequired(field) && !String(fields[field.key] || "").trim()) {
        setErr(`字段「${field.label}」不能为空`);
        return;
      }
    }
    setBusy(true);
    setErr("");
    try {
      await onSaved(editing, template.id, fields);
    } catch (e: any) {
      setErr(e?.message || "保存失败");
      setBusy(false);
    }
  };
  const pickerField = template?.fields.find((field) => field.key === pickerFieldKey) || null;

  return createPortal(
    <div className="situation-detail-backdrop" onClick={onClose}>
      <div className="situation-detail manual-issue-editor structured-directive-editor" onClick={(e) => e.stopPropagation()}>
        <div className="situation-detail-head">
          <span>{editing ? "编辑固定指令" : "新建固定指令"}</span>
          <button className="situation-detail-close" onClick={onClose} aria-label="关闭">×</button>
        </div>
        <div className="manual-issue-form">
          {err ? <div className="manual-issue-err">{err}</div> : null}
          <label>
            指令类型
            <select value={templateId} onChange={(e) => setTemplateId(e.target.value)} disabled={!!editing}>
              {templates.map((t) => <option key={t.id} value={t.id}>{t.label}</option>)}
            </select>
            {template?.settlement_hint ? <small className="manual-issue-hint">{template.settlement_hint}</small> : null}
          </label>
          {template?.fields.map((field) => {
            if (!isFieldVisible(field)) return null;
            const clearField = () => setFields((cur) => ({
              ...cur,
              [field.key]: "",
              ...(template.id === "tax_reform" && field.key === "tax_type" ? { scope: "" } : {}),
            }));
            return (
              <label key={field.key}>
                {field.label}{isFieldRequired(field) ? " *" : ""}
                {field.type === "textarea" ? (
                  <textarea
                    rows={3}
                    value={fields[field.key] || ""}
                    placeholder={field.placeholder || ""}
                    onChange={(e) => setFields((cur) => ({ ...cur, [field.key]: e.target.value }))}
                  />
                ) : field.option_source ? (
                  <div className="directive-picker-field">
                    <input
                      type="text"
                      value={fields[field.key] || ""}
                      placeholder={field.placeholder || ""}
                      readOnly
                      onClick={() => setPickerFieldKey(field.key)}
                    />
                    <button type="button" onClick={() => setPickerFieldKey(field.key)}>选择</button>
                    {fields[field.key] ? <button type="button" className="ghost" onClick={clearField}>清空</button> : null}
                  </div>
                ) : field.type === "select" ? (
                  <select value={fields[field.key] || ""} onChange={(e) => setFields((cur) => ({ ...cur, [field.key]: e.target.value }))}>
                    <option value="">请选择</option>
                    {(field.options || []).map((option) => <option key={option} value={option}>{option}</option>)}
                  </select>
                ) : (
                  <input
                    type={field.type === "number" ? "number" : "text"}
                    value={fields[field.key] || ""}
                    placeholder={field.placeholder || ""}
                    onChange={(e) => setFields((cur) => ({ ...cur, [field.key]: e.target.value }))}
                  />
                )}
              </label>
            );
          })}
          <div className="manual-issue-actions">
            <button onClick={onClose} disabled={busy}>取消</button>
            <button className="primary" onClick={save} disabled={busy}>{busy ? "保存中…" : "保存"}</button>
          </div>
        </div>
        {pickerField ? (
          <DirectiveObjectPicker
            field={pickerField}
            state={state}
            selected={fields[pickerField.key] || ""}
            onSelect={(value) => setFields((cur) => ({
              ...cur,
              [pickerField.key]: value,
              ...(template?.id === "tax_reform" && pickerField.key === "tax_type" && !REGIONAL_FISCAL_LINES.has(value) ? { scope: "" } : {}),
            }))}
            onClose={() => setPickerFieldKey("")}
          />
        ) : null}
      </div>
    </div>,
    document.body
  );
}

type PickerRow = {
  value: string;
  cells: string[];
  search: string;
};

const REGIONAL_FISCAL_LINES = new Set(["田赋", "辽饷", "盐税", "商税", "人头税"]);
function fiscalScopeKind(name: string): "regional" | "national" {
  if (REGIONAL_FISCAL_LINES.has(name)) return "regional";
  return "national";
}

function fiscalScopeLabel(name: string): string {
  const kind = fiscalScopeKind(name);
  if (kind === "regional") return "省级";
  return "全国";
}

function directivePickerRows(field: StructuredDirectiveTemplate["fields"][number], state: GameState): { headers: string[]; rows: PickerRow[]; empty: string } {
  const source = field.option_source || "";
  const mingRegionIds = new Set((state.regions || []).filter((region) => (region.controlled_by || "ming") === "ming").map((region) => region.id));
  if (source === "armies") {
    const armies = (state.armies || []).filter((army) => (army.owner_power || "ming") === "ming");
    return {
      headers: ["军队", "驻地", "战区", "统帅", "兵力", "士气"],
      rows: armies.map((army) => ({
        value: army.name,
        cells: [army.name, army.station, army.theater, army.commander, String(army.manpower), String(army.morale)],
        search: [army.name, army.station, army.theater, army.commander, army.troop_type].join(" "),
      })),
      empty: "没有可调度的大明军队。",
    };
  }
  if (source === "regions" || source === "regions_or_all") {
    const regionRows = (state.regions || []).filter((region) => (region.controlled_by || "ming") === "ming").map((region) => ({
      value: region.name,
      cells: [region.name, region.kind, String(region.population), String(region.public_support), String(region.unrest), region.status],
      search: [region.name, region.kind, region.status, region.natural_disaster, region.human_disaster].join(" "),
    }));
    return {
      headers: ["地区", "类型", "人口", "民心", "民变", "状态"],
      rows: source === "regions_or_all"
        ? [{ value: "全国", cells: ["全国", "范围", "-", "-", "-", "全部地区"], search: "全国" }, ...regionRows]
        : regionRows,
      empty: "没有可选的大明辖治地区。",
    };
  }
  if (source === "weapons") {
    const weapons = (state.arms_stock || []).filter((weapon) => weapon.unlocked && weapon.qty > 0);
    return {
      headers: ["武器", "品阶", "库存", "前置科技"],
      rows: weapons.map((weapon) => ({
        value: weapon.name,
        cells: [weapon.name, weapon.tier, String(weapon.qty), weapon.requires_tech || "-"],
        search: [weapon.name, weapon.tier, weapon.requires_tech].join(" "),
      })),
      empty: "没有可拨武器：未解锁或库存为 0 的型号不会出现在军备调度里。",
    };
  }
  if (source === "people") {
    const people = [...(state.ministers || []), ...(state.archived_ministers || []), ...(state.consorts || [])]
      .filter((person) => (person.power_id || "ming") === "ming");
    return {
      headers: ["人物", "官职/位号", "类别", "派系", "状态"],
      rows: people.map((person) => ({
        value: person.name,
        cells: [person.name, person.office || person.office_type || "-", person.office_type || "-", person.faction || "-", person.status_label || person.status],
        search: [person.name, person.office, person.office_type, person.faction, person.status_label].join(" "),
      })),
      empty: "没有可选的大明人物。",
    };
  }
  if (source === "buildings") {
    const buildings = (state.buildings || []).filter((building) => mingRegionIds.has(building.region_id));
    return {
      headers: ["建筑", "地区", "类别", "等级", "状态"],
      rows: buildings.map((building) => {
        const regionName = (state.regions || []).find((region) => region.id === building.region_id)?.name || building.region_id;
        return {
          value: building.name,
          cells: [building.name, regionName, building.category, String(building.level), building.status],
          search: [building.name, regionName, building.category, building.status].join(" "),
        };
      }),
      empty: "没有可选的大明辖内建筑。",
    };
  }
  if (source === "departments") {
    const base = ["内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部", "司礼监", "锦衣卫", "东厂", "边镇", "后宫"];
    const seen = new Set<string>();
    const rows: PickerRow[] = [];
    for (const name of base) {
      seen.add(name);
      rows.push({ value: name, cells: [name, "官署", "-", "-"], search: name });
    }
    for (const dept of state.departments || []) {
      if (seen.has(dept.name)) continue;
      rows.push({
        value: dept.name,
        cells: [dept.name, "已设衙门", dept.authority_scope || "-", String(dept.power ?? "-")],
        search: [dept.name, dept.authority_scope].join(" "),
      });
    }
    return { headers: ["部门", "类型", "职权", "权力"], rows, empty: "没有可选部门。" };
  }
  if (source === "taxes") {
    const rows: PickerRow[] = [];
    const add = (name: string, kind: string, account: string, note: string) => {
      if (rows.some((row) => row.value === name)) return;
      const scope = fiscalScopeLabel(name);
      rows.push({ value: name, cells: [name, kind, scope, account, note], search: [name, kind, scope, account, note].join(" ") });
    };
    add("田赋", "动态收入", "国库", "按本省官民田与亩率计算");
    add("辽饷", "动态收入", "国库", "按本省官民田摊派");
    add("盐税", "动态收入", "国库", "按本省盐课基数计算");
    add("商税", "动态收入", "国库", "按本省商税基数计算");
    add("人头税", "动态收入", "国库", "按本省人口与税率计算");
    add("皇庄", "动态收入", "内库", "全国科目，按皇庄地租与收益率汇总");
    for (const accountName of ["国库", "内库"] as const) {
      const account = state.budget?.[accountName];
      for (const item of [...(account?.income || []), ...(account?.expense || [])]) {
        if (!item.base_key || !item.rate_key) continue;
        add(item.name, "固定财政科目", accountName, item.note || item.formula || "");
      }
    }
    return { headers: ["财政科目", "类型", "范围", "账户", "说明"], rows, empty: "没有可选财政科目。" };
  }
  return { headers: ["名称"], rows: [], empty: "没有可选项。" };
}

function DirectiveObjectPicker({
  field,
  state,
  selected,
  onSelect,
  onClose,
}: {
  field: StructuredDirectiveTemplate["fields"][number];
  state: GameState;
  selected: string;
  onSelect: (value: string) => void;
  onClose: () => void;
}) {
  const [query, setQuery] = React.useState("");
  const { headers, rows, empty } = React.useMemo(() => directivePickerRows(field, state), [field, state]);
  const visible = React.useMemo(() => {
    const q = query.trim();
    return rows.filter((row) => !q || row.search.includes(q) || row.cells.some((cell) => cell.includes(q)));
  }, [rows, query]);
  return createPortal(
    <div className="assignee-picker-layer" role="dialog" aria-modal="true" aria-label={`选择${field.label}`}>
      <div className="assignee-picker-scrim" onClick={onClose} />
      <section className="assignee-picker directive-object-picker">
        <header className="assignee-picker-head">
          <div>
            <h2>选择{field.label}</h2>
            <span>共 {visible.length} 项</span>
          </div>
          <button type="button" className="assignee-picker-close" onClick={onClose}>×</button>
        </header>
        <div className="assignee-picker-tools">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder={`搜索${field.label}`} autoFocus />
          <button type="button" onClick={() => { onSelect(""); onClose(); }}>清空</button>
        </div>
        <div className="assignee-picker-table-wrap">
          <table className="assignee-picker-table directive-object-table">
            <thead>
              <tr>
                {headers.map((header) => <th key={header}>{header}</th>)}
              </tr>
            </thead>
            <tbody>
              {visible.map((row) => {
                const picked = selected === row.value;
                return (
                  <tr key={row.value} className={picked ? "selected" : ""} onClick={() => { onSelect(row.value); onClose(); }}>
                    {row.cells.map((cell, idx) => <td key={idx} className={idx === 0 ? "name-col" : ""}>{idx === 0 ? <b>{cell}</b> : cell}</td>)}
                  </tr>
                );
              })}
              {!visible.length ? (
                <tr><td colSpan={headers.length} className="assignee-empty">{empty}</td></tr>
              ) : null}
            </tbody>
          </table>
        </div>
      </section>
    </div>,
    document.body,
  );
}


// 现代横排诏书预览：可读优先，editable 时点开改稿。
export function DecreeScroll({
  text,
  editable,
  sealed,
  onChange,
}: {
  text: string;
  editable?: boolean;
  sealed?: boolean;
  onChange?: (value: string) => void;
}) {
  const [editing, setEditing] = React.useState(false);
  return (
    <section className={`decree-scroll${sealed ? " sealed" : ""}`}>
      <header className="decree-doc-head">
        <span>{sealed ? "正式诏书" : "诏书预览"}</span>
        {editable ? <small>点击正文可修改</small> : null}
      </header>
      <div className="decree-scroll-paper">
        {editable && editing ? (
          <textarea
            className="decree-scroll-edit"
            value={text}
            autoFocus
            onChange={(event) => onChange?.(event.target.value)}
            onBlur={() => setEditing(false)}
          />
        ) : (
          <div
            className="decree-scroll-body"
            onClick={editable ? () => setEditing(true) : undefined}
            title={editable ? "点此朱笔改稿" : undefined}
          >
            {text || "（诏文待拟）"}
          </div>
        )}
        {sealed ? <div className="decree-seal-mark" aria-hidden="true">已颁</div> : null}
      </div>
    </section>
  );
}


// 官职品级权重，数字越小品级越高（排越前）
export function officeRank(office: string): number {
  if (/首辅/.test(office)) return 1;
  if (/次辅/.test(office)) return 2;
  if (/大学士/.test(office)) return 3;
  if (/尚书/.test(office)) return 4;
  if (/侍郎/.test(office)) return 5;
  if (/都御史|巡抚|总督/.test(office)) return 6;
  if (/郎中/.test(office)) return 8;
  return 9;
}

export function filterMinisters(ministers: Minister[], group: string) {
  const courtMinisters = ministers.filter((m) => (m.power_id || "ming") === "ming");
  const archivedMinisters = courtMinisters.filter((m) => m.archived);
  const liveMinisters = courtMinisters.filter((m) => !m.archived);
  if (group === "已归档") return archivedMinisters;
  if (group === "内阁+六部" || group === "内阁" || group === "六部") {
    return liveMinisters
      .filter((m) =>
        (m.office_type === "内阁" || ["吏部", "户部", "礼部", "兵部", "刑部", "工部"].includes(m.office_type))
        && m.status === "active"
        && !!(m.office || "").trim()
        && !/前|罢|致仕/.test(m.office || "")  // 无实职不排朝班
      )
      .sort((a, b) => officeRank(a.office || "") - officeRank(b.office || ""));
  }
  if (group === "在职") return liveMinisters.filter((m) => m.status === "active");
  if (group === "收藏") return liveMinisters.filter((minister) => minister.favorite);
  return courtMinisters;
}

export function filterConsorts(consorts: Minister[], group: string) {
  const mingConsorts = consorts.filter((c) => (c.power_id || "ming") === "ming");
  if (group === "收藏") return mingConsorts.filter((c) => c.favorite);
  return mingConsorts;
}
