import React from "react";
import { createRoot } from "react-dom/client";
import {
  Check,
  Crown,
  Edit3,
  Landmark,
  Loader2,
  Lock,
  LogOut,
  MapPinned,
  Menu,
  MessageSquare,
  Power,
  RotateCcw,
  Save,
  Send,
  Settings,
  ScrollText,
  Shield,
  Star,
  Trash2,
  Swords,
  Upload,
  X,
} from "lucide-react";
import "./styles.css";

type Metrics = Record<string, number>;

type Region = {
  id: string;
  name: string;
  kind: string;
  population: number;
  public_support: number;
  unrest: number;
  natural_disaster: string;
  human_disaster: string;
  registered_land: number;
  hidden_land: number;
  tax_per_turn: number;
  grain_security: number;
  gentry_resistance: number;
  military_pressure: number;
  status: string;
  controlled_by?: string;
};

type Army = {
  id: string;
  name: string;
  station: string;
  theater: string;
  commander: string;
  controller: string;
  troop_type: string;
  manpower: number;
  maintenance_per_turn: number;
  supply: number;
  morale: number;
  training: number;
  equipment: number;
  arrears: number;
  mobility: number;
  loyalty: number;
  status: string;
};

type Power = {
  id: string;
  name: string;
  kind: string;
  leader: string;
  stance: string;
  leverage: number;
  satisfaction: number;
  military_strength: number;
  cohesion: number;
  supply: number;
  agenda: string;
  status: string;
  last_action: string;
};

type Building = {
  id: string;
  region_id: string;
  name: string;
  category: string;
  level: number;
  condition: number;
  maintenance: number;
  risk: number;
  output_metric: string;
  output_amount: number;
  status: string;
  origin: string;
};

type MapNode = {
  id: string;
  kind: "region" | "theater" | "external";
  x: number;
  y: number;
  label?: string;
  risk: number;
  region?: Region;
  armies: Army[];
  buildings?: Building[];
};

type Minister = {
  id?: string;
  name: string;
  office: string;  // 去职者已清空，可能为空串
  office_type: string;
  faction: string;
  style: string;
  status: string;  // active/dismissed/imprisoned/exiled/retired/dead/offstage
  status_reason?: string;
  status_label: string;  // 中文：在朝/已罢黜/下狱/流放/致仕…
  summary: string;
  favorite: boolean;
  portrait_id?: string;  // 空/undefined=无专属，前端 fallback 到池
  power_id?: string;     // 大明=ming, 后金=houjin, 流寇=bandits 等
  skills: Array<{ id: string; name: string; sources: string[]; description: string }>;
};

type EventItem = {
  id: string;
  title: string;
  kind: string;
  summary: string;
  urgency: number;
  severity: number;
  credibility: number;
  interests: string[];
  audiences: string[];
};

type Directive = {
  id: number;
  event_id: string;
  event_title: string;
  actor: string;
  skill_id: string;
  skill_name: string;
  text: string;
  source: string;
  status: string; // pending（待核定大臣拟旨）| draft（颁诏候选）
  notes: string;
  authority: string;
};

type Issue = {
  id: number;
  kind: "situation" | "initiative";
  title: string;
  bar_value: number;
  bar_good_meaning: string;
  bar_bad_meaning: string;
  phase: string;
  stage_text: string;
  severity: number;
  tags: string[];
  inertia: number;
  resolve_condition: string;
  fail_condition: string;
  ongoing_text: string;
  effect_on_resolve: Record<string, number>;
  effect_on_fail: Record<string, number>;
};

type ClosedIssue = {
  id: number;
  kind: "situation" | "initiative";
  title: string;
  status: "resolved" | "failed" | "dropped";
  bar_value: number;
  bar_good_meaning: string;
  bar_bad_meaning: string;
  closed_turn: number;
  stage_text: string;
  effect: any;
};

type BudgetItem = {
  name: string;
  amount: number;
  note: string;
};

type BudgetMovement = {
  delta: number;
  balance_after: number;
  category: string;
  reason: string;
};

type BudgetAccount = {
  balance: number;
  income: BudgetItem[];
  expense: BudgetItem[];
  income_total: number;
  expense_total: number;
  net: number;
  movements: BudgetMovement[];
  movements_total: number;
};

type Budget = Record<"国库" | "内库", BudgetAccount>;

type GameState = {
  turn: { year: number; period: number; turn: number };
  metrics: Metrics;
  previous_summary: string;
  treasury: string;
  issues: Issue[];
  closed_this_turn: ClosedIssue[];
  budget: Budget;
  region_warning: string;
  army_warning: string;
  power_warning: string;
  powers: Power[];
  victory_status: { status: string; summary: string };
  events: EventItem[];
  regions: Region[];
  armies: Army[];
  map_nodes: MapNode[];
  ministers: Minister[];
  consorts: Minister[];
  directives: Directive[];
  pending_count: number;
  last_decree: string;
  last_report: string;
};

type ChatMessage = { role: "user" | "minister"; content: string };
type ChatDisplayMessage = ChatMessage & { pending?: boolean };
type Suggestion = { label: string; text: string; prefix?: boolean };
type ModalName = "none" | "state" | "chat" | "edict" | "report" | "extraction" | "history" | "menu" | "secret_orders" | "minister_detail" | "faction_network" | "general_skills" | "consort_profile";
type SaveEntry = { name: string; size: number; mtime: number };
type LLMConfigInfo = {
  base_url: string;
  model: string;
  max_tokens: number;
  timeout_seconds: number;
  advanced_model: string;
  advanced_base_url: string;
  has_advanced_api_key: boolean;
  has_api_key: boolean;
  persisted: {
    base_url: string;
    model: string;
    has_api_key: boolean;
    max_tokens: number;
    timeout_seconds: number;
    advanced_model: string;
    advanced_base_url: string;
    has_advanced_api_key: boolean;
  };
};
type SecretOrder = {
  id: number;
  turn_issued: number;
  due_turn: number;
  year_issued: number;
  period_issued: number;
  minister_name: string;
  title: string;
  content: string;
  tags: string[];
  importance: number;
  status: "active" | "pending_review" | "done" | "failed" | "cancelled";
  result: string;
  sim_note: string;
  turn_closed: number | null;
};

type ProposedDirective = { id: number; text: string; status: string; notes: string };
type ChatResponse = {
  answer: string;
  history: ChatMessage[];
  suggestions: Suggestion[];
  directives: Directive[];
  court_action?: string;
  next_minister?: string;
  registered_minister?: string;
  proposed_directive?: ProposedDirective | null;
  secret_order_id?: number;
};

type ApiErrorDetail = {
  code?: string;
  message?: string;
  provider_message?: string;
  status_code?: number | null;
};

class ApiRequestError extends Error {
  detail: ApiErrorDetail;

  constructor(detail: ApiErrorDetail, fallback: string) {
    const message = detail.message || fallback;
    super(detail.code ? `[${detail.code}] ${message}` : message);
    this.name = "ApiRequestError";
    this.detail = detail;
  }
}

const normalizeApiError = (error: any, fallback: string): ApiErrorDetail => {
  const detail = error?.detail ?? error;
  if (detail && typeof detail === "object") {
    return {
      code: detail.code,
      message: detail.message || detail.detail || fallback,
      provider_message: detail.provider_message,
      status_code: detail.status_code,
    };
  }
  return { message: String(detail || fallback) };
};

const formatApiError = (error: any, fallback: string) => {
  const detail = error instanceof ApiRequestError ? error.detail : normalizeApiError(error, fallback);
  return detail.code ? `[${detail.code}] ${detail.message || fallback}` : detail.message || fallback;
};

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiRequestError(normalizeApiError(error, response.statusText), response.statusText);
  }
  return response.json();
};

const parseSseMessage = (raw: string): { event: string; data: string } | null => {
  const lines = raw.split(/\r?\n/);
  let event = "message";
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
    } else if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) return null;
  return { event, data: dataLines.join("\n") };
};

const streamChat = async (
  ministerName: string,
  message: string,
  onDelta: (delta: string) => void,
): Promise<ChatResponse> => {
  const response = await fetch(`/api/ministers/${encodeURIComponent(ministerName)}/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ message }),
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new ApiRequestError(normalizeApiError(error, response.statusText), response.statusText);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式回复。");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { value, done } = await reader.read();
    buffer += decoder.decode(value || new Uint8Array(), { stream: !done });
    const messages = buffer.split("\n\n");
    buffer = messages.pop() || "";

    for (const messageBlock of messages) {
      const parsed = parseSseMessage(messageBlock);
      if (!parsed) continue;
      const payload = JSON.parse(parsed.data);
      if (parsed.event === "delta") {
        onDelta(String(payload.content || ""));
      } else if (parsed.event === "done") {
        return payload as ChatResponse;
      } else if (parsed.event === "error") {
        throw new ApiRequestError(normalizeApiError(payload, "流式回复失败。"), "流式回复失败。");
      }
    }

    if (done) break;
  }

  throw new Error("流式回复中断，未收到完成事件。");
};

const scoreTone = (value: number, inverse = false) => {
  const danger = inverse ? value >= 65 : value <= 38;
  const warn = inverse ? value >= 45 : value <= 52;
  if (danger) return "danger";
  if (warn) return "warn";
  return "good";
};

const formatMoney = (value: number) => `${value}万两`;

const formatSignedMoney = (value: number) => `${value > 0 ? "+" : ""}${formatMoney(value)}`;

const monthlyAmount = (value: number) => Math.max(0, Math.round(value / 3));

const issueTone = (value: number) => {
  if (value <= 28) return "danger";
  if (value <= 58) return "warn";
  return "good";
};

const formatIssueEffect = (effect: Record<string, number>) => {
  const parts = Object.entries(effect || {})
    .filter(([, value]) => typeof value === "number" && value !== 0)
    .map(([key, value]) => `${key} ${value > 0 ? "+" : ""}${value}`);
  return parts.length ? parts.join("、") : "无直接数值影响";
};

const formatClosedEffect = (effect: any) => {
  if (!effect || typeof effect !== "object") return "无直接数值影响";
  const parts: string[] = [];
  const metrics = effect.metrics || {};
  for (const [k, v] of Object.entries(metrics)) {
    const n = Number(v);
    if (!n) continue;
    parts.push(`${k}${n > 0 ? "+" : ""}${n}`);
  }
  const econ = Array.isArray(effect.economy) ? effect.economy : [];
  for (const e of econ) {
    const n = Number(e?.delta);
    if (!n) continue;
    parts.push(`${e.account || "钱粮"}${n > 0 ? "+" : ""}${n}万`);
  }
  const factions = effect.factions || {};
  for (const [k, v] of Object.entries(factions)) {
    if (v && typeof v === "object") {
      const sub: string[] = [];
      for (const [kk, vv] of Object.entries(v as any)) {
        const n = Number(vv);
        if (!n) continue;
        sub.push(`${kk}${n > 0 ? "+" : ""}${n}`);
      }
      if (sub.length) parts.push(`${k}（${sub.join("、")}）`);
    } else {
      const n = Number(v);
      if (n) parts.push(`${k}${n > 0 ? "+" : ""}${n}`);
    }
  }
  return parts.length ? parts.join("、") : "无直接数值影响";
};

const splitReportItems = (text: string, prefix: string) => {
  const cleaned = text.replace(prefix, "").trim();
  const totalMatch = cleaned.match(/(两京十三省账面[月]税合计[^。]+|建档兵力合计[^。]+)。?$/);
  const itemsPart = totalMatch ? cleaned.slice(0, totalMatch.index).replace(/。$/, "") : cleaned.replace(/。$/, "");
  return {
    items: itemsPart.split("；").map((item) => item.replace(/^。+|。+$/g, "").trim()).filter(Boolean),
    tail: totalMatch?.[1] || "",
  };
};

const briefTreasury = (state: GameState) => [
  `固定预算：国库月净${formatSignedMoney(state.budget["国库"].net)}，内库月净${formatSignedMoney(state.budget["内库"].net)}。`,
  `账面余银：国库${formatMoney(state.budget["国库"].balance)}，内库${formatMoney(state.budget["内库"].balance)}。`,
];

const briefRegionWarnings = (text: string) => {
  const { items, tail } = splitReportItems(text, "地区警讯：");
  return [...items.slice(0, 3), tail].filter(Boolean);
};

const briefArmyWarnings = (text: string) => {
  const { items, tail } = splitReportItems(text, "军队警讯：");
  return [...items.slice(0, 3), tail].filter(Boolean);
};


const getMapIntelStyle = (node: MapNode): React.CSSProperties => {
  const left = Math.min(82, Math.max(18, node.x));
  const horizontal = node.x > 66 ? "-100%" : node.x < 34 ? "0" : "-50%";
  const style: React.CSSProperties = {
    left: `${left}%`,
    transform: `translateX(${horizontal})`,
    maxHeight: "calc(100vh - 24px)",
  };
  if (node.y > 50) {
    style.bottom = "12px";
    style.top = "auto";
  } else {
    style.top = "12px";
    style.bottom = "auto";
  }
  return style;
};

type AppView = "menu" | "game";

type MenuStatus = {
  has_api_key: boolean;
  has_running_game: boolean;
  has_main_db: boolean;
  saves: Array<{ name: string; size: number; mtime: number }>;
  llm: {
    base_url: string;
    model: string;
    has_api_key: boolean;
    max_tokens: number;
    timeout_seconds: number;
    advanced_model: string;
    advanced_base_url: string;
    has_advanced_api_key: boolean;
  };
};

function App() {
  const [appView, setAppView] = React.useState<AppView>("menu");
  const [menuStatus, setMenuStatus] = React.useState<MenuStatus | null>(null);
  const [state, setState] = React.useState<GameState | null>(null);
  const [selectedNodeId, setSelectedNodeId] = React.useState<string>("");
  const [mapIntelOpen, setMapIntelOpen] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [haremDrawerOpen, setHaremDrawerOpen] = React.useState(false);
  const [ministerGroup, setMinisterGroup] = React.useState("内阁+六部");
  const [haremGroup, setHaremGroup] = React.useState("全部");
  const [selectedMinister, setSelectedMinister] = React.useState<string>("");
  const [detailMinister, setDetailMinister] = React.useState<Minister | null>(null);
  const [temporaryActiveMinister, setTemporaryActiveMinister] = React.useState<Minister | null>(null);
  const [activeModal, setActiveModal] = React.useState<ModalName>("none");
  const [chat, setChat] = React.useState<ChatMessage[]>([]);
  const [suggestions, setSuggestions] = React.useState<Suggestion[]>([]);
  const [pendingUserMessage, setPendingUserMessage] = React.useState("");
  const [streamingMinisterMessage, setStreamingMinisterMessage] = React.useState("");
  const [chatNotice, setChatNotice] = React.useState("");
  const [composerHint, setComposerHint] = React.useState("");
  const [input, setInput] = React.useState("");
  const [directiveText, setDirectiveText] = React.useState("");
  const [editingDirectiveId, setEditingDirectiveId] = React.useState<number | null>(null);
  const [editingDirectiveText, setEditingDirectiveText] = React.useState("");
  const [decree, setDecree] = React.useState("");
  const [report, setReport] = React.useState("");
  const [gazetteReport, setGazetteReport] = React.useState("");
  const [busy, setBusy] = React.useState("");
  const [error, setError] = React.useState("");
  const [settleStage, setSettleStage] = React.useState("");
  const [settleThinking, setSettleThinking] = React.useState("");
  const [settleNarrative, setSettleNarrative] = React.useState("");
  const [closedShown, setClosedShown] = React.useState<number>(() => {
    const raw = sessionStorage.getItem("closedShownTurn");
    return raw ? Number(raw) : -1;
  });
  const [closedModal, setClosedModal] = React.useState<ClosedIssue[]>([]);
  const [gazetteShown, setGazetteShown] = React.useState<number>(-1);
  const [secretOrders, setSecretOrders] = React.useState<SecretOrder[]>([]);
  const [secretOrderShown, setSecretOrderShown] = React.useState<number>(-1);

  const loadState = React.useCallback(async () => {
    const data = await api<GameState>("/api/game/state");
    setState(data);
    setSelectedNodeId((current) => current || data.map_nodes[0]?.id || "");
    setDecree(data.last_decree || "");
    setReport(data.last_report || "");
  }, [selectedMinister]);

  const loadMinisterChat = React.useCallback(async (ministerName: string) => {
    const data = await api<{ minister: Minister; history: ChatMessage[]; suggestions: Suggestion[] }>(`/api/ministers/${encodeURIComponent(ministerName)}/chat`);
    const allKnown = [
      ...(state?.ministers || []),
      ...(state?.consorts || []),
    ];
    setTemporaryActiveMinister(allKnown.some((m) => m.name === data.minister.name) ? null : data.minister);
    setChat(data.history);
    setSuggestions(data.suggestions);
  }, [state]);

  const uploadPortrait = React.useCallback(async (ministerName: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    const resp = await fetch(`/api/consorts/${encodeURIComponent(ministerName)}/portrait`, {
      method: "POST",
      body: form,
    });
    if (!resp.ok) {
      const err = await resp.json().catch(() => ({ detail: resp.statusText }));
      throw new Error(err.detail || resp.statusText);
    }
    await loadState();  // 重新拉 state，新 portrait_id 流回卡片
  }, [loadState]);

  const refreshMenuStatus = React.useCallback(async () => {
    const s = await api<MenuStatus>("/api/menu/status");
    setMenuStatus(s);
    return s;
  }, []);

  React.useEffect(() => {
    refreshMenuStatus()
      .then((s) => {
        if (s.has_running_game) {
          setAppView("game");
          loadState().catch((err) => setError(err.message));
        }
      })
      .catch((err) => setError(err.message));
  }, [refreshMenuStatus, loadState]);

  const enterGameAfterMenu = React.useCallback(async () => {
    setAppView("game");
    await loadState();
  }, [loadState]);

  const exitToMenu = React.useCallback(async () => {
    await fetch("/api/menu/exit_to_menu", { method: "POST" });
    setState(null);
    setAppView("menu");
    await refreshMenuStatus();
  }, [refreshMenuStatus]);

  React.useEffect(() => {
    if (!state) return;
    const closed = state.closed_this_turn || [];
    const currentTurn = state.turn.turn;
    if (closed.length && currentTurn !== closedShown) {
      setClosedModal(closed);
      setClosedShown(currentTurn);
      sessionStorage.setItem("closedShownTurn", String(currentTurn));
    }
  }, [state, closedShown]);

  // 新回合进入时拉取全部密令，有 active 密令则弹密令进度弹窗（邸报关闭后显示）
  React.useEffect(() => {
    if (!state) return;
    const currentTurn = state.turn.turn;
    if (currentTurn === secretOrderShown) return;
    api<{ orders: SecretOrder[] }>("/api/secret_orders")
      .then(({ orders }) => {
        setSecretOrders(orders);
        if (orders.some(o => o.status === "active" || o.status === "pending_review")) {
          // 延迟 400ms，避免与邸报弹窗争抢
          setTimeout(() => setActiveModal("secret_orders"), 400);
        }
        setSecretOrderShown(currentTurn);
      })
      .catch(() => {/* 失败静默 */});
  }, [state?.turn.turn]);

  // 每次进入页面/换回合都弹上回合邸报。不持久化记录——刷新即重新弹。
  // 同一加载周期内同一回合不重复弹（gazetteShown 用 React state，刷新后回到 -1）。
  React.useEffect(() => {
    if (!state) return;
    const currentTurn = state.turn.turn;
    const summary = (state.previous_summary || "").trim();
    if (!summary) return;
    if (summary.startsWith("登基伊始")) return;
    if (currentTurn === gazetteShown) return;
    setGazetteReport(summary);
    setActiveModal("report");
    setGazetteShown(currentTurn);
  }, [state, gazetteShown]);

  React.useEffect(() => {
    if (!selectedMinister) {
      setChat([]);
      setSuggestions([]);
      setPendingUserMessage("");
      setStreamingMinisterMessage("");
      setChatNotice("");
      setComposerHint("");
      return;
    }
    setChat([]);
    setSuggestions([]);
    setPendingUserMessage("");
    setStreamingMinisterMessage("");
    setComposerHint("");
    loadMinisterChat(selectedMinister).catch((err) => setError(err.message));
  }, [selectedMinister, loadMinisterChat]);

  // 全局 ESC：按 z-index 优先级，最前面的弹窗先关
  React.useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (activeModal === "chat" || activeModal === "edict" || activeModal === "state" || activeModal === "history" || activeModal === "report" || activeModal === "secret_orders") {
        // 召对/诏书等全屏弹窗最优先
        setActiveModal("none");
      } else if (drawerOpen) {
        setDrawerOpen(false);
      } else if (haremDrawerOpen) {
        setHaremDrawerOpen(false);
      } else if (mapIntelOpen) {
        setMapIntelOpen(false);
      }
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [activeModal, drawerOpen, haremDrawerOpen, mapIntelOpen]);

  if (appView === "menu") {
    return (
      <MenuPage
        status={menuStatus}
        onRefresh={refreshMenuStatus}
        onEnterGame={enterGameAfterMenu}
        error={error}
        setError={setError}
      />
    );
  }

  if (!state) {
    return (
      <div className="loading-screen">
        <div className="loading-panel">
          <Crown size={28} />
          <p>正在启封奏牍与山河舆图...</p>
        </div>
      </div>
    );
  }

  const selectedNode = state.map_nodes.find((node) => node.id === selectedNodeId) || state.map_nodes[0];
  const ministers = filterMinisters(state.ministers, ministerGroup);
  const consorts = filterConsorts(state.consorts || [], haremGroup);
  const allCharacters = [...state.ministers, ...(state.consorts || [])];
  const activeMinister = selectedMinister
    ? allCharacters.find((m) => m.name === selectedMinister) || temporaryActiveMinister
    : null;
  const mapIntelStyle = selectedNode ? getMapIntelStyle(selectedNode) : undefined;

  const openChat = (minister: Minister) => {
    if (minister.status && minister.status !== "active") {
      setError(`${minister.name}已${minister.status_label}${minister.status_reason ? "（" + minister.status_reason + "）" : ""}，无法召见。`);
      return;
    }
    const switchingMinister = selectedMinister !== minister.name;
    if (switchingMinister) {
      setChat([]);
      setSuggestions([]);
      setTemporaryActiveMinister(null);
    }
    setSelectedMinister(minister.name);
    setActiveModal("chat");
    setError("");
    setComposerHint("");
    setChatNotice("");
    setPendingUserMessage("");
    setStreamingMinisterMessage("");
    loadMinisterChat(minister.name).catch((err) => setError(err.message));
  };

  const openDetailMinister = (minister: Minister) => {
    setDetailMinister(minister);
    setActiveModal("minister_detail");
  };

  const openFactionNetwork = () => setActiveModal("faction_network");
  const openGeneralSkills = (general: Minister) => {
    setDetailMinister(general);
    setActiveModal("general_skills");
  };
  const openConsortProfile = (consort: Minister) => {
    setDetailMinister(consort);
    setActiveModal("consort_profile");
  };

  const selectMapNode = (nodeId: string) => {
    setSelectedNodeId(nodeId);
    setMapIntelOpen(true);
  };

  const sendChat = async (text = input) => {
    if (busy) return;
    if (!activeMinister) return;
    const message = text.trim();
    if (!message) {
      setComposerHint("请先问话或点一个奏对题目");
      return;
    }

    const fromComposer = text === input;
    setPendingUserMessage(message);
    setStreamingMinisterMessage("");
    setBusy("大臣思索中");
    setError("");
    setComposerHint("");
    setChatNotice("");
    if (fromComposer) {
      setInput("");
    }
    try {
      const data = await streamChat(activeMinister.name, message, (delta) => {
        setStreamingMinisterMessage((current) => current + delta);
      });
      setPendingUserMessage("");
      setStreamingMinisterMessage("");
      setChat(data.history);
      setSuggestions(data.suggestions);
      setState((current) => (current ? { ...current, directives: data.directives } : current));
      await loadState();
      // 刷新密令列表（含历史，大臣可能调了 issue_secret_order tool）
      api<{ orders: SecretOrder[] }>("/api/secret_orders")
        .then(({ orders }) => setSecretOrders(orders))
        .catch(() => {});
      if (data.secret_order_id) {
        setChatNotice(`密令已秘密交付${activeMinister.name}，编号 #${data.secret_order_id}。`);
      }
      if (data.proposed_directive) {
        setChatNotice(`${activeMinister.name}已拟旨一道，待陛下在「诏书草案」核定（准/驳）。`);
      }
      if (data.next_minister) {
        setChat([]);
        setSuggestions([]);
        setStreamingMinisterMessage("");
        setSelectedMinister(data.next_minister);
        setActiveModal("chat");
        setChatNotice(`已传${data.next_minister}入殿。`);
        loadMinisterChat(data.next_minister).catch((err) => setError(err.message));
      }
      if (data.court_action === "dismiss") {
        setPendingUserMessage("");
        setChatNotice(`${activeMinister.name}已退下。请从左侧召见下一位大臣。`);
      }
    } catch (err) {
      if (fromComposer) {
        setInput(message);
      }
      setPendingUserMessage("");
      setStreamingMinisterMessage("");
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const createDirective = async () => {
    if (!directiveText.trim()) return;
    setBusy("登记诏书草案");
    setError("");
    try {
      const data = await api<{ directives: Directive[] }>("/api/directives", {
        method: "POST",
        body: JSON.stringify({
          text: directiveText.trim(),
        }),
      });
      setDirectiveText("");
      setState((current) => (current ? { ...current, directives: data.directives } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const toggleFavorite = async (minister: Minister) => {
    setBusy(minister.favorite ? "移出收藏" : "加入收藏");
    setError("");
    try {
      await api<{ favorites: string[] }>(`/api/favorites/${encodeURIComponent(minister.name)}`, {
        method: minister.favorite ? "DELETE" : "POST",
      });
      await loadState();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const startEditDirective = (directive: Directive) => {
    setEditingDirectiveId(directive.id);
    setEditingDirectiveText(directive.text);
  };

  const cancelEditDirective = () => {
    setEditingDirectiveId(null);
    setEditingDirectiveText("");
  };

  const saveDirective = async (directive: Directive) => {
    if (!editingDirectiveText.trim()) return;
    setBusy("修改草案");
    setError("");
    try {
      const data = await api<{ directives: Directive[] }>(`/api/directives/${directive.id}`, {
        method: "PATCH",
        body: JSON.stringify({ text: editingDirectiveText.trim() }),
      });
      setState((current) => (current ? { ...current, directives: data.directives } : current));
      cancelEditDirective();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const deleteDirective = async (directiveId: number) => {
    setBusy("删除草案");
    setError("");
    try {
      const data = await api<{ directives: Directive[] }>(`/api/directives/${directiveId}`, { method: "DELETE" });
      setState((current) => (current ? { ...current, directives: data.directives } : current));
      if (editingDirectiveId === directiveId) {
        cancelEditDirective();
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const confirmDirective = async (directiveId: number) => {
    setBusy("核定大臣拟旨");
    setError("");
    try {
      const data = await api<{ directives: Directive[]; pending_count: number }>(`/api/directives/${directiveId}/confirm`, { method: "POST" });
      setState((current) => (current ? { ...current, directives: data.directives, pending_count: data.pending_count } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const rejectDirective = async (directiveId: number) => {
    setBusy("驳回大臣拟旨");
    setError("");
    try {
      const data = await api<{ directives: Directive[]; pending_count: number }>(`/api/directives/${directiveId}/reject`, { method: "POST" });
      setState((current) => (current ? { ...current, directives: data.directives, pending_count: data.pending_count } : current));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const writeDecree = async () => {
    setBusy("拟写正式诏书");
    setError("");
    try {
      const data = await api<{ decree: string }>("/api/decree/write", { method: "POST" });
      setDecree(data.decree);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy("");
    }
  };

  const issueDecree = async () => {
    setBusy("月末结算");
    setSettleStage("");
    setSettleThinking("");
    setSettleNarrative("");
    setError("");
    try {
      const response = await fetch("/api/decree/issue/stream", { method: "POST" });
      if (!response.ok || !response.body) {
        throw new Error(`颁诏失败：HTTP ${response.status}`);
      }
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let done = false;
      let failed = "";
      while (!done) {
        const { value, done: streamDone } = await reader.read();
        if (streamDone) break;
        buffer += decoder.decode(value, { stream: true });
        // SSE 事件以空行分隔
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          let evName = "";
          let dataRaw = "";
          for (const line of block.split("\n")) {
            if (line.startsWith("event: ")) evName = line.slice(7).trim();
            else if (line.startsWith("data: ")) dataRaw += line.slice(6);
          }
          if (!evName || !dataRaw) continue;
          let data: { content?: string; message?: string } = {};
          try { data = JSON.parse(dataRaw); } catch { continue; }
          if (evName === "stage") {
            setSettleStage(data.content || "");
          } else if (evName === "thinking") {
            setSettleThinking((prev) => prev + (data.content || ""));
          } else if (evName === "text") {
            setSettleNarrative((prev) => prev + (data.content || ""));
          } else if (evName === "error") {
            failed = data.message || "颁诏失败。";
            done = true;
          } else if (evName === "done") {
            done = true;
          }
        }
      }
      if (failed) {
        setError(failed);
        setBusy("");
        return;
      }
      // 结算完成：强制整页刷新，草案/对话/局势/closed 弹窗全部按新 state 重新初始化
      window.location.reload();
      return;
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setBusy("");
    }
  };

  const settling = busy === "月末结算";
  const guardClose = (fn: () => void) => () => {
    if (settling) return;
    fn();
  };

  return (
    <main className="game-shell">
      <GrandMap nodes={state.map_nodes} selectedId={mapIntelOpen ? selectedNode?.id || "" : ""} onSelect={selectMapNode} />
      {/* FEATURE: 热力图 + 势力图标 + 战场动画层 */}
      <MapOverlays state={state} selectedNodeId={selectedNodeId} mapIntelOpen={mapIntelOpen} />
      <TopStatusBar
        state={state}
        onOpenState={() => setActiveModal("state")}
        onOpenMenu={() => setActiveModal("menu")}
        onToggleCourt={() => { setDrawerOpen((current) => !current); }}
        onToggleHarem={() => { setHaremDrawerOpen((current) => !current); }}
      />
      <BottomCommandBar
        eventsCount={state.events.length}
        directivesCount={state.directives.length}
        secretOrdersCount={secretOrders.filter((o) => o.status === "active" || o.status === "pending_review").length}
        onOpenMemorials={() => setActiveModal("state")}
        onOpenEdict={() => setActiveModal("edict")}
        onOpenExtraction={() => setActiveModal("extraction")}
        onOpenHistory={() => setActiveModal("history")}
        onOpenSecretOrders={() => setActiveModal("secret_orders")}
      />

      <CourtDrawer
        state={state}
        ministers={ministers}
        ministerGroup={ministerGroup}
        selectedMinister={selectedMinister}
        open={drawerOpen}
        onGroupChange={setMinisterGroup}
        onClose={guardClose(() => setDrawerOpen(false))}
        onOpenChat={openChat}
      />

      <HaremDrawer
        consorts={consorts}
        haremGroup={haremGroup}
        selectedMinister={selectedMinister}
        open={haremDrawerOpen}
        onGroupChange={setHaremGroup}
        onClose={guardClose(() => setHaremDrawerOpen(false))}
        onOpenChat={openChat}
        onUploadPortrait={uploadPortrait}
      />

      <SituationPanel issues={state.issues} closedIssues={state.closed_this_turn || []} />

      {mapIntelOpen && selectedNode ? (
        <section className="map-intel-panel overlay-panel" style={mapIntelStyle}>
          <button className="icon-button panel-close" aria-label="关闭地区详情" onClick={() => setMapIntelOpen(false)}>
            <X size={16} />
          </button>
          <NodeIntel node={selectedNode} />
        </section>
      ) : null}

      {activeModal === "state" ? (
        <FullscreenModal title="国势与奏报" subtitle={`${state.turn.year} 年 ${state.turn.period} 月`} bgClass="modal-bg-state" onClose={guardClose(() => setActiveModal("none"))}>
          <StateModal state={state} />
        </FullscreenModal>
      ) : null}

      {activeModal === "chat" && activeMinister ? (
        <FullscreenModal title={`召对：${activeMinister.name}`} subtitle={activeMinister.office} bgClass="modal-bg-chat" onClose={guardClose(() => setActiveModal("none"))}>
          <ChatModal
            minister={activeMinister}
            portraitPrefix={(state.consorts || []).some((c) => c.name === activeMinister.name) ? "consort_" : "minister_"}
            chat={chat}
            suggestions={suggestions}
            pendingUserMessage={pendingUserMessage}
            streamingMinisterMessage={streamingMinisterMessage}
            chatNotice={chatNotice}
            composerHint={composerHint}
            input={input}
            busy={busy}
            error={error}
            secretOrders={secretOrders.filter((o) => o.minister_name === activeMinister.name && (o.status === "active" || o.status === "pending_review"))}
            onInput={setInput}
            onSend={sendChat}
            onHint={setComposerHint}
            onFavorite={() => toggleFavorite(activeMinister)}
            onOpenEdict={() => setActiveModal("edict")}
            onClose={guardClose(() => setActiveModal("none"))}
          />
        </FullscreenModal>
      ) : null}

      {activeModal === "minister_detail" && detailMinister ? (
        <FullscreenModal title={detailMinister.name} subtitle={detailMinister.office} bgClass="modal-bg-state" onClose={() => setActiveModal("none")}>
          <MinisterDetailModal minister={detailMinister} onClose={() => setActiveModal("none")} onOpenSkills={openGeneralSkills} />
        </FullscreenModal>
      ) : null}

      {activeModal === "faction_network" ? (
        <FullscreenModal title="派系关系图" subtitle="势力网络一览" bgClass="modal-bg-state" onClose={() => setActiveModal("none")}>
          <FactionNetworkModal ministers={state.ministers} onClose={() => setActiveModal("none")} onSelectMinister={openDetailMinister} />
        </FullscreenModal>
      ) : null}

      {activeModal === "general_skills" && detailMinister ? (
        <FullscreenModal title={`${detailMinister.name} — 技能详情`} subtitle="武将技能" bgClass="modal-bg-state" onClose={() => setActiveModal("none")}>
          <GeneralSkillPanel minister={detailMinister} onClose={() => setActiveModal("none")} />
        </FullscreenModal>
      ) : null}

      {activeModal === "consort_profile" && detailMinister ? (
        <FullscreenModal title="妃嫔档案" subtitle={detailMinister.name} bgClass="modal-bg-chat" onClose={() => setActiveModal("none")}>
          <ConsortProfileModal consort={detailMinister} onClose={() => setActiveModal("none")} />
        </FullscreenModal>
      ) : null}

      {activeModal === "edict" ? (
        <FullscreenModal title="诏书草案" subtitle="本月指令、拟诏与颁布" bgClass="modal-bg-edict" onClose={guardClose(() => setActiveModal("none"))}>
          <EdictModal
            state={state}
            directiveText={directiveText}
            editingDirectiveId={editingDirectiveId}
            editingDirectiveText={editingDirectiveText}
            decree={decree}
            report={report}
            busy={busy}
            error={error}
            onDirectiveTextChange={setDirectiveText}
            onEditingTextChange={setEditingDirectiveText}
            onCreateDirective={createDirective}
            onStartEdit={startEditDirective}
            onCancelEdit={cancelEditDirective}
            onSaveDirective={saveDirective}
            onDeleteDirective={deleteDirective}
            onWriteDecree={writeDecree}
            onIssueDecree={issueDecree}
            onConfirmDirective={confirmDirective}
            onRejectDirective={rejectDirective}
          />
        </FullscreenModal>
      ) : null}

      {activeModal === "report" && (gazetteReport || report) ? (
        <ReportModal report={gazetteReport || report} onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "extraction" ? (
        <ExtractionModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "history" ? (
        <HistoryModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "menu" ? (
        <GameMenuModal
          onClose={guardClose(() => setActiveModal("none"))}
          onAfterLoad={() => {
            setActiveModal("none");
            window.location.reload();
          }}
          onExitToMenu={async () => {
            await exitToMenu();
            setActiveModal("none");
          }}
        />
      ) : null}

      {closedModal.length ? (
        <ClosedIssuesModal items={closedModal} onClose={() => setClosedModal([])} />
      ) : null}

      {activeModal === "secret_orders" ? (
        <SecretOrdersModal
          orders={secretOrders}
          onClose={() => setActiveModal("none")}
          onOpenMinister={(name) => {
            setActiveModal("chat");
            setSelectedMinister(name);
          }}
        />
      ) : null}

      {settling ? (
        <SettlementLock
          stage={settleStage}
          thinking={settleThinking}
          narrative={settleNarrative}
        />
      ) : null}
    </main>
  );
}

function SettlementLock({
  stage,
  thinking,
  narrative,
}: {
  stage: string;
  thinking: string;
  narrative: string;
}) {
  const thinkRef = React.useRef<HTMLDivElement>(null);
  const narrRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    const block = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
    };
    window.addEventListener("keydown", block, true);
    return () => window.removeEventListener("keydown", block, true);
  }, []);
  // 流式内容到达时自动滚到底
  React.useEffect(() => {
    if (thinkRef.current) thinkRef.current.scrollTop = thinkRef.current.scrollHeight;
  }, [thinking]);
  React.useEffect(() => {
    if (narrRef.current) narrRef.current.scrollTop = narrRef.current.scrollHeight;
  }, [narrative]);
  return (
    <div className="settlement-lock" role="alertdialog" aria-modal="true" aria-label="月末结算">
      <div className="settlement-lock-card">
        <Loader2 className="settlement-spin" size={28} />
        <h2>月末结算中</h2>
        <p>{stage === "数值推演结算" ? "档房核账中，钱粮、地方、军务落账，请稍候。" : stage ? `当前：${stage}` : "朝廷推演钱粮、地方、军务，请勿操作。"}</p>
        {thinking && (
          <div className="settlement-stream-block">
            <div className="settlement-stream-label">邸报房推敲</div>
            <div className="settlement-stream-text settlement-thinking" ref={thinkRef}>
              {thinking}
            </div>
          </div>
        )}
        {narrative && (
          <div className="settlement-stream-block">
            <div className="settlement-stream-label">月末奏章</div>
            <div className="settlement-stream-text settlement-narrative" ref={narrRef}>
              {narrative}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function MinisterPortrait({ primary, fallback, name }: { primary: string; fallback?: string; name: string }) {
  // 两级 fallback：primary（专属）→ fallback（pool 预设）→ 占位符
  const [stage, setStage] = React.useState<"primary" | "fallback" | "placeholder">(
    fallback ? "primary" : (primary ? "primary" : "placeholder")
  );
  const src = stage === "primary" ? primary : stage === "fallback" ? (fallback ?? "") : "";
  if (stage === "placeholder") {
    return <div className="minister-card-portrait-placeholder">臣</div>;
  }
  return (
    <img
      className="minister-card-portrait"
      src={src}
      alt={name}
      onError={() => {
        if (stage === "primary" && fallback) setStage("fallback");
        else setStage("placeholder");
      }}
    />
  );
}

// 朝班两条透视线（百分比锚点，由用户拖定）
// 左列：韩爌(外) → 黄立极(内)；右列：张瑞图(外) → 施凤来(内)
const LEFT_ANCHOR  = { near: { px: 0.077, py: 0.532 }, far: { px: 0.377, py: 0.066 } };
const RIGHT_ANCHOR = { near: { px: 0.862, py: 0.532 }, far: { px: 0.558, py: 0.045 } };

// 每列槽位数
const COURT_SLOTS_PER_ROW = 10;

// 生成两列所有槽位坐标（百分比）
function courtSlots(): { px: number; py: number; side: "left" | "right"; slot: number }[] {
  const slots = [];
  for (let i = 0; i < COURT_SLOTS_PER_ROW; i++) {
    const t = i / (COURT_SLOTS_PER_ROW - 1);
    slots.push({
      px: LEFT_ANCHOR.near.px + t * (LEFT_ANCHOR.far.px - LEFT_ANCHOR.near.px),
      py: LEFT_ANCHOR.near.py + t * (LEFT_ANCHOR.far.py - LEFT_ANCHOR.near.py),
      side: "left" as const, slot: i,
    });
    slots.push({
      px: RIGHT_ANCHOR.near.px + t * (RIGHT_ANCHOR.far.px - RIGHT_ANCHOR.near.px),
      py: RIGHT_ANCHOR.near.py + t * (RIGHT_ANCHOR.far.py - RIGHT_ANCHOR.near.py),
      side: "right" as const, slot: i,
    });
  }
  return slots;
}

// 找最近槽位（已被占用的跳过，但允许同名覆盖）
function snapToSlot(px: number, py: number, occupied: Set<string>, selfKey: string): { px: number; py: number } {
  const slots = courtSlots();
  let best = null as { px: number; py: number } | null;
  let bestDist = Infinity;
  for (const s of slots) {
    const key = `${s.side}:${s.slot}`;
    if (occupied.has(key) && key !== selfKey) continue;
    const d = Math.hypot(s.px - px, s.py - py);
    if (d < bestDist) { bestDist = d; best = s; }
  }
  return best ?? { px, py };
}

// 默认坐标：从 near 开始，每人占一格，紧挨着排不留空
const COURT_SLOT_STEP = 1 / (COURT_SLOTS_PER_ROW - 1);  // 相邻槽间距（百分比t）

function defaultCourtPct(index: number, total: number): { px: number; py: number } {
  const leftCount = Math.ceil(total / 2);
  const isLeft = index < leftCount;
  const posInRow = isLeft ? index : index - leftCount;
  const anchor = isLeft ? LEFT_ANCHOR : RIGHT_ANCHOR;
  const t = posInRow * COURT_SLOT_STEP;  // 从槽0开始连续，不跳格
  return {
    px: anchor.near.px + t * (anchor.far.px - anchor.near.px),
    py: anchor.near.py + t * (anchor.far.py - anchor.near.py),
  };
}

// 坐标存百分比（0-1），持久化到服务端 db（按存档隔离）
async function loadCourtPos(): Promise<Record<string, { px: number; py: number }>> {
  try {
    const r = await fetch("/api/court_layout");
    if (!r.ok) return {};
    const d = await r.json();
    return JSON.parse(d.layout || "{}");
  } catch { return {}; }
}
function saveCourtPos(pos: Record<string, { px: number; py: number }>) {
  fetch("/api/court_layout", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ layout: JSON.stringify(pos) }),
  }).catch(() => {});
}

function MinisterCardList({
  list,
  portraitPrefix,
  selectedMinister,
  emptyNote,
  onOpenChat,
  onUploadPortrait,
  courtMode = false,
}: {
  list: Minister[];
  portraitPrefix: string;
  selectedMinister: string;
  emptyNote: string;
  onOpenChat: (minister: Minister) => void;
  onOpenDetail?: (minister: Minister) => void;
  onUploadPortrait?: (ministerName: string, file: File) => Promise<void>;
  courtMode?: boolean;
}) {
  const containerRef = React.useRef<HTMLDivElement>(null);
  const [positions, setPositions] = React.useState<Record<string, { px: number; py: number }>>({});
  const savedPosRef = React.useRef<Record<string, { px: number; py: number }> | null>(null);
  const dragging = React.useRef<{ name: string; startMX: number; startMY: number; startPX: number; startPY: number } | null>(null);
  const didDrag = React.useRef(false);

  // 固定职位 → 固定槽位（由 office 文字推导：office 逗号分项里命中即占该槽）
  const FIXED_SLOTS: { role: string; side: "left" | "right"; slot: number }[] = [
    { role: "首辅",    side: "left",  slot: 0 },
    { role: "次辅",    side: "right", slot: 0 },
    { role: "吏部尚书", side: "left",  slot: 1 },
    { role: "户部尚书", side: "right", slot: 1 },
    { role: "礼部尚书", side: "left",  slot: 2 },
    { role: "兵部尚书", side: "right", slot: 2 },
    { role: "刑部尚书", side: "left",  slot: 3 },
    { role: "工部尚书", side: "right", slot: 3 },
  ];

  // 从 office 字符串推导固定席位：逗号切分，任一分项精确等于某固定职名即占该槽。
  // 南京XX尚书是留都缺，不占京职槽——精确匹配自然排除（分项是「南京兵部尚书」≠「兵部尚书」）。
  function roleFromOffice(office: string): string {
    const parts = (office || "").split(",").map((s) => s.trim());
    const fs = FIXED_SLOTS.find((f) => parts.includes(f.role));
    return fs ? fs.role : "";
  }

  function fixedSlotFor(role: string): { px: number; py: number } | null {
    if (!role) return null;
    const allSlots = courtSlots();
    const fs = FIXED_SLOTS.find((f) => f.role === role);
    if (!fs) return null;
    const s = allSlots.find((sl) => sl.side === fs.side && sl.slot === fs.slot);
    return s ? { px: s.px, py: s.py } : null;
  }

  // 拖动覆盖坐标只加载一次。list 变化只重排，不重 fetch。
  const listKey = list.map((m) => m.name).join("|");
  React.useEffect(() => {
    let cancelled = false;
    const arrange = (saved: Record<string, { px: number; py: number }>) => {
      if (cancelled) return;
      const allSlots = courtSlots();
      const next: Record<string, { px: number; py: number }> = {};
      const usedSlots = new Set<string>();

      list.forEach((m) => {
        const role = roleFromOffice(m.office || "");
        const fixed = fixedSlotFor(role);
        if (fixed) {
          next[m.name] = fixed;
          const fs = FIXED_SLOTS.find((f) => f.role === role);
          if (fs) usedSlots.add(`${fs.side}:${fs.slot}`);
        }
      });

      list.forEach((m) => {
        if (next[m.name]) return;
        if (saved[m.name]) {
          const cur = saved[m.name];
          let best = allSlots.find((s) => !usedSlots.has(`${s.side}:${s.slot}`)) ?? allSlots[0];
          let bestD = Infinity;
          for (const s of allSlots) {
            if (usedSlots.has(`${s.side}:${s.slot}`)) continue;
            const d = Math.hypot(s.px - cur.px, s.py - cur.py);
            if (d < bestD) { bestD = d; best = s; }
          }
          usedSlots.add(`${best.side}:${best.slot}`);
          next[m.name] = { px: best.px, py: best.py };
        } else {
          const slot = allSlots.find((s) => !usedSlots.has(`${s.side}:${s.slot}`));
          if (slot) {
            usedSlots.add(`${slot.side}:${slot.slot}`);
            next[m.name] = { px: slot.px, py: slot.py };
          } else {
            next[m.name] = { px: 0.5, py: 0.532 };
          }
        }
      });
      setPositions(next);
    };

    if (savedPosRef.current !== null) {
      arrange(savedPosRef.current);
    } else {
      loadCourtPos().then((saved) => {
        savedPosRef.current = saved;
        arrange(saved);
      });
    }
    return () => { cancelled = true; };
  }, [listKey]);

  const onMouseDown = (e: React.MouseEvent, name: string) => {
    if ((e.target as HTMLElement).closest(".portrait-upload-btn")) return;
    e.preventDefault();
    const pos = positions[name] || { px: 0.5, py: 0.8 };
    dragging.current = { name, startMX: e.clientX, startMY: e.clientY, startPX: pos.px, startPY: pos.py };
    didDrag.current = false;

    const onMove = (ev: MouseEvent) => {
      if (!dragging.current) return;
      const dx = ev.clientX - dragging.current.startMX;
      const dy = ev.clientY - dragging.current.startMY;
      if (Math.abs(dx) > 3 || Math.abs(dy) > 3) didDrag.current = true;
      const el = containerRef.current;
      if (!el) return;
      const { width, height } = el.getBoundingClientRect();
      // 拖动增量转百分比
      const npx = Math.max(0, Math.min(1, dragging.current.startPX + dx / width));
      const npy = Math.max(0, Math.min(1, dragging.current.startPY + dy / height));
      setPositions((prev) => {
        const next = { ...prev, [dragging.current!.name]: { px: npx, py: npy } };
        savedPosRef.current = next;
        saveCourtPos(next);
        return next;
      });
    };
    const onUp = () => {
      if (dragging.current && didDrag.current) {
        // 松手时吸附到最近槽位
        const dragName = dragging.current.name;
        setPositions((prev) => {
          const cur = prev[dragName];
          if (!cur) return prev;
          // 已占槽位（其他大臣）
          const occupied = new Set<string>();
          // 找吸附目标
          const snapped = snapToSlot(cur.px, cur.py, occupied, "");
          const next = { ...prev, [dragName]: snapped };
          savedPosRef.current = next;
          saveCourtPos(next);
          return next;
        });
      }
      dragging.current = null;
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  if (!list.length) return <div className={courtMode ? "minister-list minister-list-court" : "minister-list"}><div className="empty-note">{emptyNote}</div></div>;

  // 非朝班模式（全部tab）：普通网格
  if (!courtMode) {
    return (
      <div className="minister-list">
        {list.map((minister) => {
          const isCustom = minister.portrait_id?.startsWith("custom:");
          const dedicated = isCustom
            ? `/portraits/custom/${encodeURIComponent(minister.name)}?t=${cacheBust(minister.portrait_id!)}`
            : `/portraits/${portraitPrefix}${minister.id ?? minister.name}.png`;
          const poolFallback = !isCustom && minister.portrait_id ? `/portraits/${minister.portrait_id}.png` : undefined;
          const ousted = minister.status !== "active";
          return (
            <button key={minister.name}
              className={`minister-card ${selectedMinister === minister.name ? "selected" : ""} ${ousted ? "ousted" : ""}`}
              onClick={() => onOpenChat(minister)}>
              <div className="minister-card-portrait-wrap">
                <MinisterPortrait primary={dedicated} fallback={poolFallback} name={minister.name} />
                {onUploadPortrait && <PortraitUploadButton ministerName={minister.name} onUpload={onUploadPortrait} />}
              </div>
              <div className="minister-card-info">
                <div className="minister-card-top">
                  <span className="minister-name">{minister.name}</span>
                  {ousted && <span className={`minister-status status-${minister.status}`}>{minister.status_label}</span>}
                  {minister.office && <span className="minister-office">{minister.office}</span>}
                </div>
                <span className="minister-bio">{minister.summary}</span>
              </div>
              {onOpenDetail && (
                <button className="minister-detail-btn" onClick={(e) => { e.stopPropagation(); onOpenDetail(minister); }} title="查看详情">
                  <Edit3 size={12} />
                </button>
              )}
              {minister.favorite && <Star className="favorite-mark" size={13} />}
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <div className="minister-list minister-list-court" ref={containerRef}>
      {list.map((minister) => {
        const isCustom = minister.portrait_id?.startsWith("custom:");
        const dedicated = isCustom
          ? `/portraits/custom/${encodeURIComponent(minister.name)}?t=${cacheBust(minister.portrait_id!)}`
          : `/portraits/${portraitPrefix}${minister.id ?? minister.name}.png`;
        const poolFallback = !isCustom && minister.portrait_id
          ? `/portraits/${minister.portrait_id}.png`
          : undefined;
        const ousted = minister.status !== "active";
        const pct = positions[minister.name];
        // 透视缩放：py=0最远最小，py=1最近最大
        const perspScale = pct ? 0.38 + 0.62 * pct.py : 1;
        // 卡片宽用 vh 单位（CSS），这里只控制 scale
        return (
          <button
            key={minister.name}
            className={`minister-card ${selectedMinister === minister.name ? "selected" : ""} ${ousted ? "ousted" : ""}`}
            style={pct ? {
              position: "absolute",
              left: `${pct.px * 100}%`,
              top: `${pct.py * 100}%`,
              cursor: "grab",
              transform: `scale(${perspScale.toFixed(3)})`,
              transformOrigin: "bottom center",
              zIndex: Math.round(pct.py * 1000),
            } : { visibility: "hidden" }}
            onMouseDown={(e) => onMouseDown(e, minister.name)}
            onClick={(e) => { if (didDrag.current) { e.preventDefault(); return; } onOpenChat(minister); }}
          >
            <div className="minister-card-portrait-wrap">
              <MinisterPortrait primary={dedicated} fallback={poolFallback} name={minister.name} />
              {onUploadPortrait && (
                <PortraitUploadButton ministerName={minister.name} onUpload={onUploadPortrait} />
              )}
            </div>
            <div className="minister-card-info">
              <div className="minister-card-top">
                <span className="minister-name">{minister.name}</span>
                {ousted && <span className={`minister-status status-${minister.status}`}>{minister.status_label}</span>}
                {minister.office && <span className="minister-office">{minister.office}</span>}
              </div>
              <span className="minister-bio">{minister.summary}</span>
            </div>
            {minister.favorite && <Star className="favorite-mark" size={13} />}
          </button>
        );
      })}
    </div>
  );
}

// 自定义立绘文件名固定（一人一图），故按 portrait_id 之外另用上传时间戳刷缓存。
const _portraitBust: Record<string, number> = {};
function cacheBust(key: string): number {
  if (!_portraitBust[key]) _portraitBust[key] = Date.now();
  return _portraitBust[key];
}

function PortraitUploadButton({
  ministerName,
  onUpload,
}: {
  ministerName: string;
  onUpload: (ministerName: string, file: File) => Promise<void>;
}) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const [busy, setBusy] = React.useState(false);
  return (
    <>
      <button
        type="button"
        className="portrait-upload-btn"
        title="上传立绘"
        disabled={busy}
        onClick={(e) => {
          e.stopPropagation();  // 别触发卡片的召见
          inputRef.current?.click();
        }}
      >
        <Upload size={13} />
      </button>
      <input
        ref={inputRef}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        style={{ display: "none" }}
        onClick={(e) => e.stopPropagation()}
        onChange={async (e) => {
          const file = e.target.files?.[0];
          e.target.value = "";  // 允许重选同一文件
          if (!file) return;
          setBusy(true);
          try {
            // 立即刷该人物缓存键，loadState 回来后新图不被旧缓存挡住。
            _portraitBust[`custom:${ministerName}`] = Date.now();
            await onUpload(ministerName, file);
          } catch (err) {
            window.alert(`上传失败：${(err as Error).message}`);
          } finally {
            setBusy(false);
          }
        }}
      />
    </>
  );
}

function CourtDrawer({
  state: _state,
  ministers,
  ministerGroup,
  selectedMinister,
  open,
  onGroupChange,
  onClose,
  onOpenChat,
}: {
  state: GameState;
  ministers: Minister[];
  ministerGroup: string;
  selectedMinister: string;
  open: boolean;
  onGroupChange: (group: string) => void;
  onClose: () => void;
  onOpenChat: (minister: Minister) => void;
}) {
  return (
    <>
      {open && <button className="drawer-scrim" aria-label="收起" onClick={onClose} />}
      <aside className={`court-drawer ${open ? "open" : ""}`}>
        <div className="drawer-brand">
          <div className="panel-title">
            <Landmark size={17} />
            <span>朝堂</span>
          </div>
          <button className="icon-button" aria-label="收起" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="segmented">
          {["内阁+六部", "收藏", "全部"].map((group) => (
            <button
              className={ministerGroup === group ? "active" : ""}
              key={group}
              onClick={() => onGroupChange(group)}
            >
              {group}
            </button>
          ))}
        </div>
        <MinisterCardList
          list={ministers}
          portraitPrefix="minister_"
          selectedMinister={selectedMinister}
          emptyNote="此栏暂无可召见大臣。"
          onOpenChat={onOpenChat}
          onOpenDetail={openDetailMinister}
          courtMode={ministerGroup !== "全部"}
        />
      </aside>
    </>
  );
}

function HaremDrawer({
  consorts,
  haremGroup,
  selectedMinister,
  open,
  onGroupChange,
  onClose,
  onOpenChat,
  onUploadPortrait,
}: {
  consorts: Minister[];
  haremGroup: string;
  selectedMinister: string;
  open: boolean;
  onGroupChange: (group: string) => void;
  onClose: () => void;
  onOpenChat: (minister: Minister) => void;
  onUploadPortrait: (ministerName: string, file: File) => Promise<void>;
}) {
  return (
    <>
      {open && <button className="drawer-scrim" aria-label="收起" onClick={onClose} />}
      <aside className={`court-drawer harem-drawer overlay-panel ${open ? "open" : ""}`}>
        <div className="drawer-brand">
          <div className="panel-title">
            <Crown size={17} />
            <span>后宫</span>
          </div>
          <button className="icon-button" aria-label="收起" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="segmented">
          {["全部", "收藏"].map((group) => (
            <button
              className={haremGroup === group ? "active" : ""}
              key={group}
              onClick={() => onGroupChange(group)}
            >
              {group}
            </button>
          ))}
        </div>
        <MinisterCardList
          list={consorts}
          portraitPrefix="consort_"
          selectedMinister={selectedMinister}
          emptyNote="后宫暂无可召见之人。"
          onOpenChat={onOpenChat}
          onOpenDetail={openConsortProfile}
          onUploadPortrait={onUploadPortrait}
        />
      </aside>
    </>
  );
}

function TopStatusBar({
  state,
  onOpenState,
  onOpenMenu,
  onToggleCourt,
  onToggleHarem,
}: {
  state: GameState;
  onOpenState: () => void;
  onOpenMenu: () => void;
  onToggleCourt: () => void;
  onToggleHarem: () => void;
}) {
  const scoreKeys = ["民心", "皇威"];
  return (
    <header className="status-bar" aria-label="国势状态栏">
      <button className="status-emblem" onClick={onOpenState}>
        <img src="/icon_ming_emblem.png" alt="大明" className="emblem-art" />
        <span>{state.turn.year} 年 {state.turn.period} 月</span>
      </button>
      <div className="status-metrics">
        <BudgetHover accountName="国库" budget={state.budget["国库"]} />
        <BudgetHover accountName="内库" budget={state.budget["内库"]} />
        {scoreKeys.map((key) => (
          <span className={`status-pill ${scoreTone(state.metrics[key], false)}`} key={key}>
            {key} <b>{state.metrics[key]}</b>
          </span>
        ))}
        <button className="status-menu court-toggle-btn" onClick={onToggleCourt} aria-label="打开朝堂">
          <Landmark size={16} />
          <span>朝堂</span>
        </button>
        <button className="status-menu court-toggle-btn harem-btn" onClick={onToggleHarem} aria-label="打开后宫">
          <Crown size={16} />
          <span>后宫</span>
        </button>
        <button className="status-menu" onClick={onOpenMenu} aria-label="游戏菜单">
          <Menu size={16} />
          <span>菜单</span>
        </button>
      </div>
    </header>
  );
}

function BudgetHover({ accountName, budget }: { accountName: "国库" | "内库"; budget: BudgetAccount }) {
  const [open, setOpen] = React.useState(false);
  return (
    <span
      className={`budget-hover ${open ? "open" : ""}`}
      onMouseEnter={() => setOpen(true)}
      onMouseLeave={() => setOpen(false)}
      onFocus={() => setOpen(true)}
      onBlur={() => setOpen(false)}
    >
      <button
        className="status-money budget-trigger"
        type="button"
        aria-label={`查看${accountName}固定收支`}
        onClick={() => setOpen((current) => !current)}
      >
        <span>{accountName} <b>{formatMoney(budget.balance)}</b></span>
        <small className={budget.net >= 0 ? "income" : "expense"}>月 {formatSignedMoney(budget.net)}</small>
      </button>
      <span className="budget-popover" role="tooltip">
        <span className="budget-popover-head">
          <b>{accountName}月度定额</b>
          <span className="budget-summary">
            <span><small>入</small><strong className="income">{formatMoney(budget.income_total)}</strong></span>
            <span><small>出</small><strong className="expense">{formatMoney(budget.expense_total)}</strong></span>
            <span><small>净</small><strong className={budget.net >= 0 ? "income" : "expense"}>{formatSignedMoney(budget.net)}</strong></span>
          </span>
        </span>
        <BudgetList title="固定收入" items={budget.income} />
        <BudgetList title="固定支出" items={budget.expense} expense />
        <BudgetMovementsList movements={budget.movements} total={budget.movements_total} />
      </span>
    </span>
  );
}

function BudgetMovementsList({ movements, total }: { movements: BudgetMovement[]; total: number }) {
  if (!movements.length) {
    return (
      <span className="budget-list">
        <span className="budget-list-title">本月一次性入账（上月末结算）</span>
        <span className="budget-row"><span><b>暂无</b><small>上月末未结算入出</small></span></span>
      </span>
    );
  }
  return (
    <span className="budget-list">
      <span className="budget-list-title">
        本月一次性入账（上月末结算）
        <small className={total >= 0 ? "income" : "expense"}>　合计 {formatSignedMoney(total)}</small>
      </span>
      {movements.map((m, idx) => {
        const sign = m.delta >= 0 ? "+" : "-";
        const cls = m.delta >= 0 ? "income" : "expense";
        return (
          <span className="budget-row" key={`mv-${idx}`}>
            <span>
              <b>{m.category || "—"}</b>
              <small>{m.reason}</small>
            </span>
            <strong className={cls}>{sign}{formatMoney(Math.abs(m.delta))}</strong>
          </span>
        );
      })}
    </span>
  );
}

function BudgetList({ title, items, expense = false }: { title: string; items: BudgetItem[]; expense?: boolean }) {
  return (
    <span className="budget-list">
      <span className="budget-list-title">{title}</span>
      {items.map((item) => (
        <span className="budget-row" key={`${title}-${item.name}`}>
          <span>
            <b>{item.name}</b>
            <small>{item.note}</small>
          </span>
          <strong className={expense ? "expense" : "income"}>{expense ? "-" : "+"}{formatMoney(item.amount)}</strong>
        </span>
      ))}
    </span>
  );
}

function BottomCommandBar({
  eventsCount,
  directivesCount,
  secretOrdersCount,
  onOpenMemorials,
  onOpenEdict,
  onOpenExtraction,
  onOpenHistory,
  onOpenSecretOrders,
}: {
  eventsCount: number;
  directivesCount: number;
  secretOrdersCount: number;
  onOpenMemorials: () => void;
  onOpenEdict: () => void;
  onOpenExtraction: () => void;
  onOpenHistory: () => void;
  onOpenSecretOrders: () => void;
}) {
  return (
    <nav className="bottom-command-bar" aria-label="朝政主操作">
      <button className="command-icon" onClick={onOpenMemorials} aria-label={`奏疏 ${eventsCount} 件待览`}>
        <img src="/icon_seal.png" alt="" className="command-art" />
        {eventsCount ? <span className="command-badge">{eventsCount}</span> : null}
        <span className="command-caption"><b>奏疏</b><small>{eventsCount} 件待览</small></span>
      </button>
      <button className="command-icon" onClick={onOpenExtraction} aria-label="邸报详明">
        <img src="/icon_scroll.png" alt="" className="command-art" />
        <span className="command-caption"><b>邸报详明</b><small>数项加减/账目明细</small></span>
      </button>
      <button className="command-icon" onClick={onOpenEdict} aria-label={`诏书草案 ${directivesCount} 道待发`}>
        <img src="/icon_scroll.png" alt="" className="command-art" />
        {directivesCount ? <span className="command-badge">{directivesCount}</span> : null}
        <span className="command-caption"><b>诏书草案</b><small>{directivesCount ? `${directivesCount} 道待发` : "本月未下旨"}</small></span>
      </button>
      <button className="command-icon" onClick={onOpenSecretOrders} aria-label={`密令 ${secretOrdersCount} 条进行中`}>
        <img src="/bg_edict.png" alt="" className="command-art command-art-secret" />
        {secretOrdersCount ? <span className="command-badge command-badge-secret">{secretOrdersCount}</span> : null}
        <span className="command-caption"><b>密令</b><small>{secretOrdersCount ? `${secretOrdersCount} 条进行中` : "暂无密令"}</small></span>
      </button>
      <button className="command-icon" onClick={onOpenHistory} aria-label="历代奏报">
        <img src="/icon_scroll.png" alt="" className="command-art" />
        <span className="command-caption"><b>史册</b><small>历代奏报/诏书</small></span>
      </button>
    </nav>
  );
}

function FullscreenModal({
  title,
  subtitle,
  bgClass,
  onClose,
  children,
  headerExtra,
}: {
  title: string;
  subtitle: string;
  bgClass?: string;
  onClose: () => void;
  children: React.ReactNode;
  headerExtra?: React.ReactNode;
}) {
  return (
    <section className="fullscreen-layer" role="dialog" aria-modal="true" aria-label={title}>
      <div className="fullscreen-scrim" onClick={onClose} />
      <div className={`fullscreen-modal ${bgClass || ""}`}>
        <header className="modal-header">
          <div className="modal-title">
            <div>
              <h1>{title}</h1>
              <span>{subtitle}</span>
            </div>
          </div>
          <div className="modal-header-actions">
            {headerExtra}
            <button className="icon-button" aria-label="关闭弹窗" onClick={onClose}>
              <X size={18} />
            </button>
          </div>
        </header>
        {children}
      </div>
    </section>
  );
}

type ExtractionData = {
  turn: number;
  year: number;
  period: number;
  exists: boolean;
  extractor_output?: any;
};

function ReportModal({ report, onClose }: { report: string; onClose: () => void }) {
  return (
    <FullscreenModal title="月末奏疏" subtitle="推演结果" bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document modal-scroll">
        <div className="document-section">
          <pre className="memorial-text">{report}</pre>
        </div>
      </article>
    </FullscreenModal>
  );
}

function SecretOrdersModal({
  orders,
  onClose,
  onOpenMinister,
}: {
  orders: SecretOrder[];
  onClose: () => void;
  onOpenMinister: (name: string) => void;
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
        />
      ) : null}
    </FullscreenModal>
  );
}

function SecretOrderDetailDialog({
  order,
  statusLabel,
  statusCls,
  onClose,
  onOpenMinister,
}: {
  order: SecretOrder;
  statusLabel: Record<string, string>;
  statusCls: Record<string, string>;
  onClose: () => void;
  onOpenMinister: (name: string) => void;
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
          <button className="secondary-action" onClick={onClose}>返回列表</button>
        </footer>
      </section>
    </div>
  );
}

function SecretOrderDetailBlock({ title, text, tone = "default" }: { title: string; text: string; tone?: "default" | "green" }) {
  return (
    <section className={`so-detail-block so-detail-block-${tone}`}>
      <h3>{title}</h3>
      <p>{text}</p>
    </section>
  );
}

function ClosedIssuesModal({ items, onClose }: { items: ClosedIssue[]; onClose: () => void }) {
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

function ClosedGroup({ title, items, cls }: { title: string; items: ClosedIssue[]; cls: string }) {
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

function ExtractionModal({ onClose }: { onClose: () => void }) {
  const [extraction, setExtraction] = React.useState<ExtractionData | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState("");

  React.useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const resp = await fetch("/api/turn_extraction");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (alive) setExtraction(data);
      } catch (e: any) {
        if (alive) setError(e?.message || "加载失败");
      } finally {
        if (alive) setLoading(false);
      }
    })();
    return () => { alive = false; };
  }, []);

  return (
    <FullscreenModal title="邸报详明" subtitle="数项加减/账目明细" bgClass="modal-bg-state" onClose={onClose}>
      <article className="state-document modal-scroll">
        <ExtractionView data={extraction} loading={loading} error={error} />
      </article>
    </FullscreenModal>
  );
}

type HistoryTurnItem = {
  turn: number;
  year: number;
  period: number;
  has_report: boolean;
  has_extraction: boolean;
  has_directive: boolean;
};

type HistoryDirective = {
  id: number;
  turn: number;
  year: number;
  period: number;
  event_id: string;
  event_title: string;
  actor: string;
  skill_id: string;
  text: string;
  source: string;
  status: string;
  notes: string;
  created_at: string;
  updated_at: string;
};

type HistoryDetail = {
  turn: number;
  exists: boolean;
  year: number;
  period: number;
  report: string;
  decree_text: string;
  directives: HistoryDirective[];
  extraction: ExtractionData | null;
};

function HistoryModal({ onClose }: { onClose: () => void }) {
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
        const resp = await fetch("/api/history/turns");
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
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
        const resp = await fetch(`/api/history/turn/${selectedTurn}`);
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
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

function GameMenuModal({
  onClose,
  onAfterLoad,
  onExitToMenu,
}: {
  onClose: () => void;
  onAfterLoad: () => void;
  onExitToMenu: () => void;
}) {
  const [tab, setTab] = React.useState<"save" | "load" | "llm" | "reset" | "exit_menu" | "shutdown">("save");
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <section className="center-layer" role="dialog" aria-modal="true" aria-label="游戏菜单">
      <div className="center-scrim" onClick={onClose} />
      <div className="center-modal">
        <header className="center-modal-header">
          <h1>游戏菜单</h1>
          <button className="icon-button" aria-label="关闭弹窗" onClick={onClose}>
            <X size={18} />
          </button>
        </header>
        <div className="game-menu">
          <nav className="game-menu-tabs">
            <button className={tab === "save" ? "active" : ""} onClick={() => setTab("save")}>
              <Save size={14} /> 保存存档
            </button>
            <button className={tab === "load" ? "active" : ""} onClick={() => setTab("load")}>
              <Upload size={14} /> 加载存档
            </button>
            <button className={tab === "llm" ? "active" : ""} onClick={() => setTab("llm")}>
              <Settings size={14} /> LLM 配置
            </button>
            <button className={tab === "reset" ? "active" : ""} onClick={() => setTab("reset")}>
              <RotateCcw size={14} /> 重开新局
            </button>
            <button className={tab === "exit_menu" ? "active" : ""} onClick={() => setTab("exit_menu")}>
              <LogOut size={14} /> 回到主菜单
            </button>
            <button className={tab === "shutdown" ? "active" : ""} onClick={() => setTab("shutdown")}>
              <Power size={14} /> 退出游戏
            </button>
          </nav>
          <div className="game-menu-body">
            {tab === "save" ? <SaveTab /> : null}
            {tab === "load" ? <LoadTab onAfterLoad={onAfterLoad} /> : null}
            {tab === "llm" ? <LLMConfigTab /> : null}
            {tab === "reset" ? <ResetTab onAfterReset={onAfterLoad} /> : null}
            {tab === "exit_menu" ? <ExitToMenuTab onExit={onExitToMenu} /> : null}
            {tab === "shutdown" ? <ShutdownTab /> : null}
          </div>
        </div>
      </div>
    </section>
  );
}

function SaveTab() {
  const [name, setName] = React.useState("");
  const [saves, setSaves] = React.useState<SaveEntry[]>([]);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");

  const refresh = React.useCallback(async () => {
    try {
      const data = await api<{ saves: SaveEntry[] }>("/api/saves");
      setSaves(data.saves);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);

  React.useEffect(() => {
    refresh();
  }, [refresh]);

  const onSave = async () => {
    if (!name.trim()) {
      setErr("请填存档名。");
      return;
    }
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      await api<{ save: { name: string }; saves: SaveEntry[] }>("/api/saves", {
        method: "POST",
        body: JSON.stringify({ name: name.trim() }),
      });
      setMsg(`已保存为 ${name.trim()}.db`);
      setName("");
      await refresh();
    } catch (e) {
      const detail = e instanceof ApiRequestError ? e.detail : null;
      setErr(detail ? `code: ${detail.code || "unknown"}\nmessage: ${detail.message || (e instanceof Error ? e.message : String(e))}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="menu-section">
      <h3>保存当前局</h3>
      <p className="menu-hint">将当前 DB 热备到 data/saves/&lt;名字&gt;.db。同名直接覆盖。</p>
      <div className="menu-row">
        <input
          className="menu-input"
          placeholder="存档名（字母/数字/._-）"
          value={name}
          onChange={(e) => setName(e.target.value)}
          disabled={busy}
        />
        <button className="menu-btn primary" onClick={onSave} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Save size={14} />} 保存
        </button>
      </div>
      {msg ? <div className="menu-success">{msg}</div> : null}
      {err ? <div className="menu-error">{err}</div> : null}
      <h4>现有存档</h4>
      <SavesList saves={saves} onRefresh={refresh} />
    </section>
  );
}

function LoadTab({ onAfterLoad }: { onAfterLoad: () => void }) {
  const [saves, setSaves] = React.useState<SaveEntry[]>([]);
  const [busy, setBusy] = React.useState("");
  const [err, setErr] = React.useState("");
  const refresh = React.useCallback(async () => {
    try {
      const data = await api<{ saves: SaveEntry[] }>("/api/saves");
      setSaves(data.saves);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }, []);
  React.useEffect(() => {
    refresh();
  }, [refresh]);

  const onLoad = async (n: string) => {
    if (!window.confirm(`确定加载 ${n}.db？当前未保存进度会丢失。`)) return;
    setBusy(n);
    setErr("");
    try {
      await api(`/api/saves/${encodeURIComponent(n)}/load`, { method: "POST" });
      onAfterLoad();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy("");
    }
  };

  return (
    <section className="menu-section">
      <h3>加载存档</h3>
      <p className="menu-hint">选一份覆盖回主 DB。加载后页面会自动重新载入。</p>
      {err ? <div className="menu-error">{err}</div> : null}
      <SavesList saves={saves} onRefresh={refresh} action={onLoad} busy={busy} />
    </section>
  );
}

function ResetTab({ onAfterReset }: { onAfterReset: () => void }) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const [confirmText, setConfirmText] = React.useState("");

  const canReset = confirmText.trim() === "重开";

  const onReset = async () => {
    if (!canReset) return;
    if (!window.confirm("确定重开新局？当前局所有数据将被永久清空（存档目录不动）。")) return;
    setBusy(true);
    setErr("");
    try {
      await api("/api/game/reset", { method: "POST" });
      onAfterReset();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };

  return (
    <section className="menu-section">
      <h3>重开新局</h3>
      <p className="menu-hint">
        清空主 DB（聊天记录、回合奏报、局势、ledger 全清），重置到天启七年十二月开局。
        <b>不可撤销</b>。要保留当前局，先到「保存存档」存一份。
      </p>
      <p className="menu-hint">输入「重开」二字以解锁按钮：</p>
      <div className="menu-row">
        <input
          className="menu-input"
          placeholder="输入：重开"
          value={confirmText}
          onChange={(e) => setConfirmText(e.target.value)}
          disabled={busy}
        />
        <button className="menu-btn danger" onClick={onReset} disabled={!canReset || busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <RotateCcw size={14} />} 重开新局
        </button>
      </div>
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function ExitToMenuTab({ onExit }: { onExit: () => void | Promise<void> }) {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const onClick = async () => {
    if (!window.confirm("回到主菜单？当前对局会关闭（DB 仍保留，可从「继续上局」回到此处）。")) return;
    setBusy(true);
    setErr("");
    try {
      await onExit();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <section className="menu-section">
      <h3>回到主菜单</h3>
      <p className="menu-hint">
        关闭当前游戏会话，回到主菜单。数据库与存档不变；可从主菜单「继续上局」或「加载存档」回到游戏。
      </p>
      <div className="menu-row">
        <button className="menu-btn primary" onClick={onClick} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <LogOut size={14} />} 回到主菜单
        </button>
      </div>
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function ShutdownTab() {
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");
  const onClick = async () => {
    if (!window.confirm("退出整个游戏？前后端进程都会关闭，未保存的进度会丢失。")) return;
    setBusy(true);
    setErr("");
    try {
      await fetch("/api/menu/shutdown", { method: "POST" });
      // server 已发 SIGTERM 给自己；前端尝试关页面（浏览器可能拦截），否则提示用户。
      setTimeout(() => {
        try { window.close(); } catch { /* noop */ }
      }, 400);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setBusy(false);
    }
  };
  return (
    <section className="menu-section">
      <h3>退出游戏</h3>
      <p className="menu-hint">
        终止服务进程并尝试关闭浏览器页面。<b>未保存的进度会丢失</b>。要保留当前局，先到「保存存档」。
      </p>
      <div className="menu-row">
        <button className="menu-btn danger" onClick={onClick} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Power size={14} />} 退出游戏
        </button>
      </div>
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function SavesList({
  saves,
  onRefresh,
  action,
  busy,
}: {
  saves: SaveEntry[];
  onRefresh: () => void;
  action?: (name: string) => void;
  busy?: string;
}) {
  const [delErr, setDelErr] = React.useState("");
  const onDelete = async (n: string) => {
    if (!window.confirm(`删除 ${n}.db？`)) return;
    try {
      await api(`/api/saves/${encodeURIComponent(n)}`, { method: "DELETE" });
      onRefresh();
    } catch (e) {
      setDelErr(e instanceof Error ? e.message : String(e));
    }
  };
  if (!saves.length) return <div className="menu-empty">尚无存档。</div>;
  return (
    <ul className="saves-list">
      {delErr ? <div className="menu-error">{delErr}</div> : null}
      {saves.map((s) => (
        <li key={s.name} className="saves-row">
          <div className="saves-name">
            <b>{s.name}</b>
            <small>
              {new Date(s.mtime * 1000).toLocaleString()} · {(s.size / 1024).toFixed(1)} KB
            </small>
          </div>
          <div className="saves-actions">
            {action ? (
              <button className="menu-btn primary" disabled={busy === s.name} onClick={() => action(s.name)}>
                {busy === s.name ? <Loader2 size={14} className="spin" /> : <Upload size={14} />} 加载
              </button>
            ) : null}
            <button className="menu-btn danger" onClick={() => onDelete(s.name)}>
              <Trash2 size={14} /> 删
            </button>
          </div>
        </li>
      ))}
    </ul>
  );
}

function LLMConfigTab() {
  const [info, setInfo] = React.useState<LLMConfigInfo | null>(null);
  const [baseUrl, setBaseUrl] = React.useState("");
  const [model, setModel] = React.useState("");
  const [advancedModel, setAdvancedModel] = React.useState("");
  const [advancedBaseUrl, setAdvancedBaseUrl] = React.useState("");
  const [advancedApiKey, setAdvancedApiKey] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [maxTokens, setMaxTokens] = React.useState("8000");
  const [timeoutSeconds, setTimeoutSeconds] = React.useState("180");
  const [show, setShow] = React.useState(false);
  const [busy, setBusy] = React.useState(false);
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");

  React.useEffect(() => {
    api<LLMConfigInfo>("/api/llm/config")
      .then((data) => {
        setInfo(data);
        setBaseUrl(data.base_url);
        setModel(data.model);
        setAdvancedModel(data.advanced_model || "");
        setAdvancedBaseUrl(data.advanced_base_url || "");
        setMaxTokens(String(data.max_tokens || 8000));
        setTimeoutSeconds(String(data.timeout_seconds || 180));
      })
      .catch((e) => setErr(e instanceof Error ? e.message : String(e)));
  }, []);

  const onSave = async () => {
    setBusy(true);
    setErr("");
    setMsg("");
    try {
      const data = await api<LLMConfigInfo>("/api/llm/config", {
        method: "POST",
        body: JSON.stringify({
          base_url: baseUrl,
          model,
          api_key: apiKey,
          max_tokens: parseInt(maxTokens) || 8000,
          timeout_seconds: parseFloat(timeoutSeconds) || 180,
          advanced_model: advancedModel,
          advanced_base_url: advancedBaseUrl,
          advanced_api_key: advancedApiKey.trim() ? advancedApiKey : "__keep__",
        }),
      });
      setInfo((cur) => (cur ? { ...cur, ...data } : null));
      setApiKey("");
      setAdvancedApiKey("");
      setMsg("已生效并写入 data/runtime_llm.json。");
    } catch (e) {
      const detail = e instanceof ApiRequestError ? e.detail : null;
      setErr(detail ? `code: ${detail.code || "unknown"}\nmessage: ${detail.message || (e instanceof Error ? e.message : String(e))}` : e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className="menu-section">
      <h3>LLM 配置</h3>
      <p className="menu-hint">
        立即生效并写入 <code>data/runtime_llm.json</code>，重启进程后自动加载。api_key 留空保留当前。
      </p>
      <label className="menu-field">
        <span>Base URL</span>
        <input
          className="menu-input"
          value={baseUrl}
          onChange={(e) => setBaseUrl(e.target.value)}
          placeholder="https://api.openai.com/v1"
        />
      </label>
      <label className="menu-field">
        <span>Model</span>
        <input
          className="menu-input"
          value={model}
          onChange={(e) => setModel(e.target.value)}
          placeholder="gpt-4o-mini"
        />
      </label>
      <label className="menu-field">
        <span>Advanced Model <small className="menu-hint">（推演 + 打分专用，空=与 Model 一致）</small></span>
        <input
          className="menu-input"
          value={advancedModel}
          onChange={(e) => setAdvancedModel(e.target.value)}
          placeholder="deepseek-reasoner / gpt-5（留空 fallback）"
        />
      </label>
      <label className="menu-field">
        <span>Advanced Base URL <small className="menu-hint">（advanced 专用网关，空=与 Base URL 一致）</small></span>
        <input
          className="menu-input"
          value={advancedBaseUrl}
          onChange={(e) => setAdvancedBaseUrl(e.target.value)}
          placeholder="https://other-gateway/v1（留空复用主 Base URL）"
        />
      </label>
      <label className="menu-field">
        <span>
          Advanced API Key{" "}
          {info?.has_advanced_api_key ? (
            <small className="ok">（当前已设置）</small>
          ) : (
            <small className="menu-hint">（空=复用主 API Key）</small>
          )}
        </span>
        <input
          className="menu-input"
          type={show ? "text" : "password"}
          value={advancedApiKey}
          onChange={(e) => setAdvancedApiKey(e.target.value)}
          placeholder="留空=复用主 API Key / 保留当前"
        />
      </label>
      <label className="menu-field">
        <span>Max Tokens</span>
        <input
          className="menu-input"
          type="number"
          min={256}
          max={65536}
          value={maxTokens}
          onChange={(e) => setMaxTokens(e.target.value)}
          placeholder="8000"
        />
      </label>
      <label className="menu-field">
        <span>Timeout Seconds</span>
        <input
          className="menu-input"
          type="number"
          min={10}
          max={900}
          value={timeoutSeconds}
          onChange={(e) => setTimeoutSeconds(e.target.value)}
          placeholder="180"
        />
      </label>
      <label className="menu-field">
        <span>
          API Key{" "}
          {info?.has_api_key ? <small className="ok">（当前已设置）</small> : <small className="warn">（未设置）</small>}
        </span>
        <div className="menu-row">
          <input
            className="menu-input"
            type={show ? "text" : "password"}
            value={apiKey}
            onChange={(e) => setApiKey(e.target.value)}
            placeholder={info?.has_api_key ? "留空保留当前" : "请输入"}
            autoComplete="off"
          />
          <button className="menu-btn" type="button" onClick={() => setShow((v) => !v)}>
            {show ? "隐" : "显"}
          </button>
        </div>
      </label>
      <div className="menu-row">
        <button className="menu-btn primary" onClick={onSave} disabled={busy}>
          {busy ? <Loader2 size={14} className="spin" /> : <Check size={14} />} 保存并应用
        </button>
      </div>
      {msg ? <div className="menu-success">{msg}</div> : null}
      {err ? <div className="menu-error">{err}</div> : null}
    </section>
  );
}

function HistoryDetailView({
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
          <pre className="memorial-text">{detail.report}</pre>
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

function ExtractionView({ data, loading, error }: { data: ExtractionData | null; loading: boolean; error: string }) {
  if (loading) return <div className="document-section"><p className="long-copy">加载中…</p></div>;
  if (error) return <div className="document-section"><p className="long-copy">加载失败：{error}</p></div>;
  if (!data || !data.exists) return <div className="document-section"><p className="long-copy">该回合无 extractor 数据。</p></div>;
  const rawOut = data.extractor_output;
  if (!rawOut || typeof rawOut !== "object") {
    return <div className="document-section"><pre className="memorial-text">{String(rawOut ?? "")}</pre></div>;
  }
  // modular 结构：数据在 merged（已合并扁平），顶层只有 mode/modules/merged/raw。老存档是扁平对象，直接用。
  const out = (rawOut as any).mode === "modular" && (rawOut as any).merged && typeof (rawOut as any).merged === "object"
    ? (rawOut as any).merged
    : rawOut;
  return (
    <div className="document-section extraction-view">
      <ExtractionSection title="国势变化">
        <MetricDeltaBlock data={pickField(out, "国势变化", "metric_delta")} />
      </ExtractionSection>
      <ExtractionSection title="钱粮收支">
        <EconomyBlock data={pickField(out, "钱粮收支", "economy_moves")} />
      </ExtractionSection>
      <ExtractionSection title="派系变化">
        <FactionBlock data={pickField(out, "派系变化", "faction_delta")} />
      </ExtractionSection>
      <ExtractionSection title="官职任免">
        <OfficeChangesBlock data={pickField(out, "人事变更", "office_changes")} />
      </ExtractionSection>
      <ExtractionSection title="去职变更">
        <StatusChangesBlock data={pickField(out, "人物状态变化", "character_status_changes")} />
      </ExtractionSection>
      <ExtractionSection title="后宫纳妃">
        <AppointmentsBlock data={pickField(out, "后宫册封", "appointments")} />
      </ExtractionSection>
      <ExtractionSection title="局势推进">
        <IssueAdvancesBlock data={pickField(out, "局势推进", "issue_advances")} />
      </ExtractionSection>
      <ExtractionSection title="新立局势">
        <NewIssuesBlock data={pickField(out, "新立局势", "new_issues")} />
      </ExtractionSection>
      <ExtractionSection title="结案 / 失败">
        <CloseIssuesBlock data={pickField(out, "结案局势", "close_issues")} />
      </ExtractionSection>
      <ExtractionSection title="撤旨">
        <CancelsBlock data={pickField(out, "撤销局势", "cancels")} />
      </ExtractionSection>
      <ExtractionSection title="地区变化">
        <GenericKVBlock data={pickField(out, "地区变化", "region_delta")} />
      </ExtractionSection>
      <ExtractionSection title="军队变化">
        <GenericKVBlock data={pickField(out, "军队变化", "army_delta")} />
      </ExtractionSection>
      <ExtractionSection title="新建军队">
        <NewArmiesBlock data={pickField(out, "新建军队", "new_armies")} />
      </ExtractionSection>
      <ExtractionSection title="势力变化">
        <GenericKVBlock data={pickField(out, "势力变化", "power_updates")} />
      </ExtractionSection>
      <ExtractionSection title="财政系数">
        <FiscalBlock data={pickField(out, "财政制度变化", "fiscal_changes")} />
      </ExtractionSection>
      <ExtractionSection title="外交关系">
        <GenericKVBlock data={pickField(out, "外交关系", "world_advance") ?? pickField(out, "外交", "world_advance") ?? pickField(out, "外交态度", "world_advance") ?? pickField(out, "四方动向", "world_advance")} />
      </ExtractionSection>
    </div>
  );
}

function pickField(obj: any, cn: string, en: string): any {
  if (!obj || typeof obj !== "object") return undefined;
  return obj[cn] ?? obj[en];
}

function pickItem(obj: any, cn: string, en: string): any {
  if (!obj || typeof obj !== "object") return undefined;
  return obj[cn] ?? obj[en];
}

function ExtractionSection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="extraction-section">
      <h3 className="extraction-section-title">{title}</h3>
      <div className="extraction-section-body">{children}</div>
    </section>
  );
}

function fmtDelta(n: any): string {
  const num = Number(n);
  if (!Number.isFinite(num)) return String(n);
  if (num > 0) return `+${num}`;
  return String(num);
}

function isEmptyData(d: any): boolean {
  if (d == null) return true;
  if (Array.isArray(d)) return d.length === 0;
  if (typeof d === "object") return Object.keys(d).length === 0;
  return false;
}

function MetricDeltaBlock({ data }: { data: any }) {
  if (isEmptyData(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-kv">
      {Object.entries(data).map(([k, v]) => (
        <li key={k}><span>{k}</span><b className={Number(v) >= 0 ? "good" : "bad"}>{fmtDelta(v)}</b></li>
      ))}
    </ul>
  );
}

function EconomyBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((item: any, i: number) => (
        <li key={i}>
          <b className={Number(pickItem(item, "增量", "delta")) >= 0 ? "good" : "bad"}>
            {pickItem(item, "账户", "account") || "?"} {fmtDelta(pickItem(item, "增量", "delta"))} 万
          </b>
          <span>{pickItem(item, "分类", "category") || ""}{pickItem(item, "原因", "reason") ? ` — ${pickItem(item, "原因", "reason")}` : ""}</span>
        </li>
      ))}
    </ul>
  );
}

function FactionBlock({ data }: { data: any }) {
  if (isEmptyData(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-kv">
      {Object.entries(data).map(([k, v]: [string, any]) => {
        if (v && typeof v === "object") {
          return (
            <li key={k}>
              <span>{k}</span>
              <b>{Object.entries(v).map(([kk, vv]) => `${kk}${fmtDelta(vv)}`).join("  ")}</b>
            </li>
          );
        }
        return <li key={k}><span>{k}</span><b className={Number(v) >= 0 ? "good" : "bad"}>{fmtDelta(v)}</b></li>;
      })}
    </ul>
  );
}

function IssueAdvancesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={Number(pickItem(it, "进度增量", "delta_bar")) >= 0 ? "good" : "bad"}>
            #{pickItem(it, "局势编号", "issue_id")} 进度 {fmtDelta(pickItem(it, "进度增量", "delta_bar"))}
            {pickItem(it, "惯性增量", "inertia_delta") ? `，惯性 ${fmtDelta(pickItem(it, "惯性增量", "inertia_delta"))}` : ""}
          </b>
          {pickItem(it, "阶段", "stage_text") ? <span>{pickItem(it, "阶段", "stage_text")}</span> : null}
          {pickItem(it, "叙述", "narrative") ? <span className="extraction-narr">{pickItem(it, "叙述", "narrative")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function NewIssuesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b>{pickItem(it, "标题", "title") || pickItem(it, "编号", "id") || "新事项"}（{pickItem(it, "类型", "kind") || pickItem(it, "来源类型", "origin_kind") || ""}）</b>
          {pickItem(it, "阶段", "stage_text") ? <span>{pickItem(it, "阶段", "stage_text")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function CloseIssuesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "原因", "reason") === "resolved" ? "good" : "bad"}>
            #{pickItem(it, "局势编号", "issue_id")} {pickItem(it, "原因", "reason") === "resolved" ? "结案" : "失败"}
          </b>
          {pickItem(it, "叙述", "narrative") ? <span>{pickItem(it, "叙述", "narrative")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function CancelsBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b>#{pickItem(it, "局势编号", "issue_id")} 撤旨</b>
          {pickItem(it, "叙述", "narrative") ? <span>{pickItem(it, "叙述", "narrative")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function OfficeChangesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "rejected", "rejected") ? "bad" : "good"}>
            {pickItem(it, "姓名", "name")} → {pickItem(it, "新官职", "new_office")}
            {pickItem(it, "新官署类别", "new_office_type") ? `（${pickItem(it, "新官署类别", "new_office_type")}）` : ""}
            {pickItem(it, "rejected", "rejected") ? "（未落地）" : pickItem(it, "kind", "kind") === "appoint" ? "（新进朝堂）" : ""}
          </b>
          {pickItem(it, "displaced", "displaced") ? <span>顶替 {pickItem(it, "displaced", "displaced")} 去职</span> : null}
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function StatusChangesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  const label: Record<string, string> = {
    dismissed: "罢黜", imprisoned: "下狱", exiled: "流放",
    retired: "致仕", dead: "身故", offstage: "去位",
  };
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "rejected", "rejected") ? "bad" : ""}>
            {pickItem(it, "姓名", "name")} {label[pickItem(it, "状态", "status")] || pickItem(it, "状态", "status")}
            {pickItem(it, "rejected", "rejected") ? "（未落地）" : ""}
          </b>
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function AppointmentsBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={pickItem(it, "rejected", "rejected") ? "bad" : "good"}>
            {pickItem(it, "姓名", "name")} 册封 {pickItem(it, "位号", "office")}
            {pickItem(it, "rejected", "rejected") ? "（未落地）" : ""}
          </b>
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function FiscalBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((it: any, i: number) => (
        <li key={i}>
          <b className={Number(pickItem(it, "增量", "delta")) >= 0 ? "good" : "bad"}>
            {pickItem(it, "键", "key")} {fmtDelta(pickItem(it, "增量", "delta"))}
          </b>
          {pickItem(it, "原因", "reason") ? <span>{pickItem(it, "原因", "reason")}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function GenericKVBlock({ data }: { data: any }) {
  if (isEmptyData(data)) return <p className="extraction-empty">无</p>;
  return <pre className="extraction-json">{JSON.stringify(data, null, 2)}</pre>;
}

function NewArmiesBlock({ data }: { data: any }) {
  if (isEmptyData(data) || !Array.isArray(data)) return <p className="extraction-empty">无</p>;
  return (
    <ul className="extraction-list">
      {data.map((item: any, i: number) => {
        const name = pickItem(item, "名称", "name") || pickItem(item, "编号", "id") || "?";
        const owner = pickItem(item, "归属", "owner_power") || "?";
        const manpower = pickItem(item, "人数", "manpower");
        const station = pickItem(item, "驻扎地", "station") || "";
        const commander = pickItem(item, "统将", "commander") || "";
        return (
          <li key={i}>
            <b>{name}</b>（归属：{owner}）{manpower ? ` · ${manpower}人` : ""}
            <span>{station}{commander ? ` · ${commander}` : ""}</span>
          </li>
        );
      })}
    </ul>
  );
}

function PreviousSummary({ summary }: { summary: string }) {
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

function StateModal({ state }: { state: GameState }) {
  const report = state.last_report || state.previous_summary;
  return (
    <article className="state-document modal-scroll">
      <section className="document-section">
        {report
          ? <pre className="memorial-text">{report}</pre>
          : <div className="empty-note">尚无上月奏报。</div>}
      </section>
    </article>
  );
}

function BriefReport({ title, items }: { title: string; items: string[] }) {
  return (
    <article>
      <h2>{title}</h2>
      <ul className="brief-list">
        {items.map((item) => <li key={`${title}-${item}`}>{item}</li>)}
      </ul>
    </article>
  );
}

function SituationPanel({ issues, closedIssues }: { issues: Issue[]; closedIssues: ClosedIssue[] }) {
  const active = issues.filter((issue) => issue.kind === "situation" || issue.kind === "initiative");
  const [collapsed, setCollapsed] = React.useState(false);
  if (!active.length && !closedIssues.length) return null;
  active.sort((a, b) => {
    if (a.kind !== b.kind) return a.kind === "initiative" ? -1 : 1;
    return a.id - b.id;
  });
  return (
    <aside className={`situation-panel ${collapsed ? "collapsed" : ""}`} aria-label="局势进度">
      <div className="situation-panel-title">
        <span>局势进度</span>
        <button
          type="button"
          className="situation-toggle"
          aria-label={collapsed ? "展开局势" : "收起局势"}
          onClick={() => setCollapsed((c) => !c)}
        >{collapsed ? "+" : "−"}</button>
      </div>
      {!collapsed && closedIssues.length ? (
        <div className="situation-closed-list">
          {closedIssues.map((ci) => (
            <div className={`situation-closed-row ${ci.status}`} key={`closed-${ci.id}`} tabIndex={0}>
              <div className="situation-closed-head">
                <span className="situation-closed-badge">{ci.status === "resolved" ? "已结案" : ci.status === "failed" ? "已崩坏" : "已撤"}</span>
                <span className="situation-closed-name">{ci.title}</span>
              </div>
              <div className="situation-closed-effect">{formatClosedEffect(ci.effect)}</div>
            </div>
          ))}
        </div>
      ) : null}
      {!collapsed && <div className="situation-list">
        {active.map((issue) => (
          <div className={`situation-row ${issueTone(issue.bar_value)}`} key={issue.id} tabIndex={0}>
            <div className="situation-row-head">
              <span className="situation-name">{issue.title}</span>
              <b>{issue.bar_value}</b>
            </div>
            <div className="situation-bar">
              <i style={{ width: `${Math.max(0, Math.min(100, issue.bar_value))}%` }} />
            </div>
            <div className="situation-tip" role="tooltip">
              <div className="situation-tip-head">#{issue.id} {issue.title}</div>
              <div className="situation-tip-row"><span>阶段</span><b>{issue.phase}</b></div>
              <div className="situation-tip-row"><span>进度</span><b>{issue.bar_value} / 100</b></div>
              <div className="situation-tip-row">
                <span>月度推进</span>
                <b>{issue.inertia > 0 ? `+${issue.inertia}` : issue.inertia}/月</b>
              </div>
              <div className="situation-tip-row">
                <span>当前影响</span>
                <b>{issue.ongoing_text || "无"}</b>
              </div>
              <p className="situation-tip-stage">{issue.stage_text}</p>
              <div className="situation-tip-outcome good">
                <div className="situation-tip-outcome-head">达成（{issue.bar_good_meaning}）</div>
                {issue.resolve_condition && <p>{issue.resolve_condition}</p>}
                <div className="situation-tip-effect">{formatIssueEffect(issue.effect_on_resolve)}</div>
              </div>
              <div className="situation-tip-outcome bad">
                <div className="situation-tip-outcome-head">失败（{issue.bar_bad_meaning}）</div>
                {issue.fail_condition && <p>{issue.fail_condition}</p>}
                <div className="situation-tip-effect">{formatIssueEffect(issue.effect_on_fail)}</div>
              </div>
              {issue.tags.length ? (
                <div className="situation-tip-tags">
                  {issue.tags.map((tag) => <small key={tag}>{tag}</small>)}
                </div>
              ) : null}
            </div>
          </div>
        ))}
      </div>}
    </aside>
  );
}

function IssueGroup({ title, issues }: { title: string; issues: Issue[] }) {
  if (!issues.length) return null;
  return (
    <div className="issue-group">
      <h3>{title}</h3>
      <div className="issue-list">
        {issues.map((issue) => (
          <article className={`issue-line ${issueTone(issue.bar_value)}`} key={issue.id}>
            <div className="issue-head">
              <b>#{issue.id} {issue.title}</b>
              <span>{issue.phase} · {issue.bar_value}</span>
            </div>
            <div className="issue-progress" aria-label={`${issue.title}进度 ${issue.bar_value}`}>
              <span>{issue.bar_bad_meaning}</span>
              <div>
                <i style={{ width: `${Math.max(0, Math.min(100, issue.bar_value))}%` }} />
              </div>
              <span>{issue.bar_good_meaning}</span>
            </div>
            <p>{issue.stage_text}</p>
            {issue.tags.length ? (
              <div className="issue-tags">
                {issue.tags.map((tag) => <small key={tag}>{tag}</small>)}
              </div>
            ) : null}
          </article>
        ))}
      </div>
    </div>
  );
}

function ChatModal({
  minister,
  portraitPrefix,
  chat,
  suggestions,
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
  onOpenEdict,
  onClose,
}: {
  minister: Minister;
  portraitPrefix: string;
  chat: ChatMessage[];
  suggestions: Suggestion[];
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
  onOpenEdict: () => void;
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
    onSend(input);
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
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
                {o.content && <div className="secret-order-content">{o.content}</div>}
                {o.sim_note && <div className="secret-order-content"><b>月度动向：</b>{o.sim_note}</div>}
                {o.result && <div className="secret-order-content"><b>承办回报：</b>{o.result}</div>}
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
              <p>{message.content}</p>
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
              placeholder="问大臣军情、钱粮、地方，或要求他拟旨... Enter 发送，Shift+Enter 换行"
            />
          </label>
          <div className="composer-actions">
            <button className={`primary-action ${!input.trim() ? "is-empty" : ""}`} onClick={handleSend} disabled={!!busy}>
              <Send size={15} />
              发送
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

function EdictModal({
  state,
  directiveText,
  editingDirectiveId,
  editingDirectiveText,
  decree,
  report,
  busy,
  error,
  onDirectiveTextChange,
  onEditingTextChange,
  onCreateDirective,
  onStartEdit,
  onCancelEdit,
  onSaveDirective,
  onDeleteDirective,
  onWriteDecree,
  onIssueDecree,
  onConfirmDirective,
  onRejectDirective,
}: {
  state: GameState;
  directiveText: string;
  editingDirectiveId: number | null;
  editingDirectiveText: string;
  decree: string;
  report: string;
  busy: string;
  error: string;
  onDirectiveTextChange: (value: string) => void;
  onEditingTextChange: (value: string) => void;
  onCreateDirective: () => void;
  onStartEdit: (directive: Directive) => void;
  onCancelEdit: () => void;
  onSaveDirective: (directive: Directive) => void;
  onDeleteDirective: (directiveId: number) => void;
  onWriteDecree: () => void;
  onIssueDecree: () => void;
  onConfirmDirective: (directiveId: number) => void;
  onRejectDirective: (directiveId: number) => void;
}) {
  const pendingDirectives = state.directives.filter((d) => d.status === "pending");
  const draftDirectives = state.directives.filter((d) => d.status !== "pending");
  const hasPending = pendingDirectives.length > 0;
  return (
    <div className="edict-full-grid">
      <section className="modal-pane directive-pane">
        <h2>本月指令</h2>
        {hasPending && (
          <div className="pending-directives" role="region" aria-label="待核定大臣拟旨">
            <h3>⚠ 大臣拟旨待核定（{pendingDirectives.length}）</h3>
            {pendingDirectives.map((directive) => (
              <div className="directive-item pending" key={directive.id}>
                <div className="directive-head">
                  <b>#{directive.id}</b>
                  <span>{directive.source}</span>
                </div>
                <p>{directive.text}</p>
                {directive.notes ? <small>{directive.notes}</small> : null}
                <div className="directive-tools">
                  <button onClick={() => onConfirmDirective(directive.id)} disabled={!!busy}><Check size={14} />准</button>
                  <button onClick={() => onRejectDirective(directive.id)} disabled={!!busy}><X size={14} />驳</button>
                </div>
              </div>
            ))}
          </div>
        )}
        <div className="directive-list">
          {draftDirectives.map((directive) => (
            <div className="directive-item" key={directive.id}>
              <div className="directive-head">
                <b>#{directive.id}</b>
                <span>{directive.source}</span>
              </div>
              {editingDirectiveId === directive.id ? (
                <div className="directive-edit">
                  <textarea value={editingDirectiveText} onChange={(event) => onEditingTextChange(event.target.value)} />
                  <div>
                    <button className="icon-button" onClick={() => onSaveDirective(directive)} aria-label="保存草案"><Check size={15} /></button>
                    <button className="icon-button" onClick={onCancelEdit} aria-label="取消修改"><X size={15} /></button>
                  </div>
                </div>
              ) : (
                <>
                  <p>{directive.text}</p>
                  {directive.notes ? <small>{directive.notes}</small> : null}
                  <div className="directive-tools">
                    <button onClick={() => onStartEdit(directive)}><Edit3 size={14} />改</button>
                    <button onClick={() => onDeleteDirective(directive.id)}><Trash2 size={14} />删</button>
                  </div>
                </>
              )}
            </div>
          ))}
          {!draftDirectives.length && !hasPending && <div className="empty-note">本月不可空过。请先召见大臣，或在右侧新增一道指令。</div>}
        </div>
      </section>

      <section className="modal-pane edict-compose">
        <h2>新增指令</h2>
        <textarea
          value={directiveText}
          onChange={(event) => onDirectiveTextChange(event.target.value)}
          placeholder="例如：命毕自严核拨关宁、山海关、蓟镇辽饷一百五十二万两..."
        />
        <div className="edict-actions">
          <button onClick={onCreateDirective} disabled={!!busy || !directiveText.trim()}>新增草案</button>
          <button onClick={onWriteDecree} disabled={!!busy || !draftDirectives.length || hasPending}>生成诏书</button>
          <button className="primary-action" onClick={onIssueDecree} disabled={!!busy || !draftDirectives.length || hasPending}>颁布诏书</button>
        </div>
        {hasPending && <small className="pending-hint">尚有 {pendingDirectives.length} 道大臣拟旨待核定（准/驳），核定后方可颁诏。</small>}
      </section>

      <section className="modal-pane settlement-box">
        <h2>诏书与奏章</h2>
        {busy && <div className="busy-line"><Loader2 size={15} />{busy}...</div>}
        {error && <div className="error-line" role="alert">{error}</div>}
        {decree || report ? (
          <pre>{`${decree || ""}${report ? `\n\n${report}` : ""}`}</pre>
        ) : (
          <div className="empty-note">生成诏书后，正式诏文会在此显示；颁布后会显示月末总结奏章。</div>
        )}
      </section>
    </div>
  );
}

// 官职品级权重，数字越小品级越高（排越前）
function officeRank(office: string): number {
  if (/首辅/.test(office)) return 1;
  if (/次辅/.test(office)) return 2;
  if (/大学士/.test(office)) return 3;
  if (/尚书/.test(office)) return 4;
  if (/侍郎/.test(office)) return 5;
  if (/都御史|巡抚|总督/.test(office)) return 6;
  if (/郎中/.test(office)) return 8;
  return 9;
}

function filterMinisters(ministers: Minister[], group: string) {
  const courtMinisters = ministers.filter((m) => (m.power_id || "ming") === "ming");
  if (group === "内阁+六部" || group === "内阁" || group === "六部") {
    return courtMinisters
      .filter((m) =>
        (m.office_type === "内阁" || ["吏部", "户部", "礼部", "兵部", "刑部", "工部"].includes(m.office_type))
        && m.status === "active"
        && !!(m.office || "").trim()
        && !/前|罢|致仕/.test(m.office || "")  // 无实职不排朝班
      )
      .sort((a, b) => officeRank(a.office || "") - officeRank(b.office || ""));
  }
  if (group === "收藏") return courtMinisters.filter((minister) => minister.favorite);
  return courtMinisters;
}

function filterConsorts(consorts: Minister[], group: string) {
  const mingConsorts = consorts.filter((c) => (c.power_id || "ming") === "ming");
  if (group === "收藏") return mingConsorts.filter((c) => c.favorite);
  return mingConsorts;
}

function GrandMap({ nodes, selectedId, onSelect }: { nodes: MapNode[]; selectedId: string; onSelect: (id: string) => void }) {
  const viewportRef = React.useRef<HTMLDivElement | null>(null);
  const [tileW, setTileW] = React.useState<number>(() => (typeof window !== "undefined" ? window.innerWidth : 1280));
  const [offsetX, setOffsetX] = React.useState(0);
  const dragState = React.useRef<{ pointerId: number; startX: number; originX: number; moved: boolean } | null>(null);
  const [dragging, setDragging] = React.useState(false);

  // 坐标取点工具：URL 加 ?coords=1 开启。点地图打印 x/y%（对照 web_app.py map_nodes）。
  const coordPick = typeof window !== "undefined" && new URLSearchParams(window.location.search).has("coords");
  const [pick, setPick] = React.useState<{ x: number; y: number } | null>(null);
  const onPickClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!coordPick || dragState.current?.moved) return;
    const rect = viewportRef.current?.getBoundingClientRect();
    if (!rect || tileW <= 0) return;
    let lx = (e.clientX - rect.left - offsetX) % tileW;
    if (lx < 0) lx += tileW;
    const x = +(lx / tileW * 100).toFixed(1);
    const y = +((e.clientY - rect.top) / rect.height * 100).toFixed(1);
    setPick({ x, y });
    console.log(`map coord: (${x}, ${y})`);
  };

  const wrap = React.useCallback((x: number, w: number) => {
    if (w <= 0) return 0;
    let r = x % w;
    if (r > 0) r -= w;
    return r;
  }, []);

  React.useEffect(() => {
    const measure = () => {
      const w = viewportRef.current?.clientWidth ?? window.innerWidth;
      setTileW(w);
      setOffsetX((cur) => wrap(cur, w));
    };
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [wrap]);

  React.useEffect(() => {
    const onWheel = (e: WheelEvent) => {
      if (e.ctrlKey) return;
      const target = e.target as HTMLElement | null;
      if (target && target.closest('button, a, input, textarea, select, [role="dialog"], .court-drawer, .map-intel-panel, .modal-scroll, .fullscreen-modal, .situation-panel, .chat-main')) return;
      const dx = Math.abs(e.deltaX) > Math.abs(e.deltaY) ? e.deltaX : e.deltaY;
      if (dx === 0) return;
      e.preventDefault();
      setOffsetX((cur) => wrap(cur - dx, viewportRef.current?.clientWidth ?? tileW));
    };
    window.addEventListener("wheel", onWheel, { passive: false });
    return () => window.removeEventListener("wheel", onWheel);
  }, [wrap, tileW]);

  const onPointerDown = (e: React.PointerEvent<HTMLDivElement>) => {
    if (e.button !== 0 && e.pointerType === "mouse") return;
    // 点在节点按钮上：不抢 pointer capture，否则 click 被劫持到 section，按钮 onClick 不触发。
    if ((e.target as HTMLElement).closest(".map-node")) return;
    dragState.current = {
      pointerId: e.pointerId,
      startX: e.clientX,
      originX: offsetX,
      moved: false,
    };
    (e.currentTarget as HTMLDivElement).setPointerCapture(e.pointerId);
    setDragging(true);
  };

  const onPointerMove = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = dragState.current;
    if (!st || st.pointerId !== e.pointerId) return;
    const dx = e.clientX - st.startX;
    if (!st.moved && Math.abs(dx) > 4) st.moved = true;
    setOffsetX(wrap(st.originX + dx, tileW));
  };

  const endDrag = (e: React.PointerEvent<HTMLDivElement>) => {
    const st = dragState.current;
    if (!st || st.pointerId !== e.pointerId) return;
    try { (e.currentTarget as HTMLDivElement).releasePointerCapture(e.pointerId); } catch {}
    dragState.current = null;
    setDragging(false);
  };

  const wasDragged = () => dragState.current?.moved === true;
  const tiles = [-1, 0, 1];

  return (
    <section
      ref={viewportRef}
      className={`grand-map ${dragging ? "dragging" : ""}`}
      aria-label="大明地图"
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={endDrag}
      onPointerCancel={endDrag}
      onClick={onPickClick}
    >
      <div
        className="map-strip"
        style={{ transform: `translate3d(${offsetX}px, 0, 0)` }}
      >
        {tiles.map((idx) => (
          <div
            key={idx}
            className="map-tile"
            style={{ width: tileW }}
            aria-hidden={idx !== 0}
          >
            {nodes.map((node) => {
              const selected = idx === 0 && selectedId === node.id;
              const danger = node.risk > 175;
              if (node.kind === "external") {
                return (
                  <div
                    key={`${idx}:${node.id}`}
                    className="map-node external"
                    style={{ left: `${node.x}%`, top: `${node.y}%` }}
                    aria-hidden="true"
                  >
                    <span>{node.region?.name.split(" / ")[0] || node.label}</span>
                  </div>
                );
              }
              return (
                <button
                  key={`${idx}:${node.id}`}
                  className={`map-node ${node.kind} ${selected ? "selected" : ""} ${danger ? "danger" : ""}`}
                  style={{ left: `${node.x}%`, top: `${node.y}%` }}
                  onClick={(ev) => {
                    if (wasDragged()) { ev.preventDefault(); ev.stopPropagation(); return; }
                    onSelect(node.id);
                  }}
                  aria-label={`查看${node.region?.name || node.label}`}
                  tabIndex={idx === 0 ? 0 : -1}
                >
                  {node.kind === "theater" ? <Shield size={16} /> : <MapPinned size={15} />}
                  <span>{node.region?.name.split(" / ")[0] || node.label}</span>
                </button>
              );
            })}
          </div>
        ))}
      </div>
      {coordPick && pick ? (
        <div className="coord-pick-readout">
          x: {pick.x} &nbsp; y: {pick.y}
        </div>
      ) : null}
    </section>
  );
}

function NodeIntel({ node }: { node: MapNode }) {
  const region = node.region;
  return (
    <>
      <div className="panel-title">
        {node.kind === "theater" ? <Shield size={14} /> : <MapPinned size={14} />}
        <span>{region?.name || node.label}</span>
      </div>
      {region ? (
        <table className="intel-table">
          <tbody>
            <tr><th>人口</th><td>{region.population}万</td><th>田亩</th><td>{region.registered_land}万亩</td></tr>
            <tr><th>民心</th><td>{region.public_support}</td><th>动乱</th><td>{region.unrest}</td></tr>
            <tr><th>粮食</th><td>{region.grain_security}</td><th>月税</th><td>{monthlyAmount(region.tax_per_turn)}万/月</td></tr>
            <tr><th>归属</th><td>{region.controlled_by || "ming"}</td><th>类型</th><td>{region.kind}</td></tr>
            <tr><th>天灾</th><td colSpan={3}>{region.natural_disaster}</td></tr>
            <tr><th>人祸</th><td colSpan={3}>{region.human_disaster}</td></tr>
            <tr><th>状况</th><td colSpan={3}>{region.status}</td></tr>
          </tbody>
        </table>
      ) : null}
      <div className="garrison-title">驻军</div>
      {node.armies.length ? (
        <table className="intel-table">
          <thead>
            <tr><th>番号</th><th>兵种</th><th>兵</th><th>饷</th><th>士气</th><th>欠饷</th></tr>
          </thead>
          <tbody>
            {node.armies.map((army) => {
              const maint = army.maintenance_per_turn || 0;
              const arr = army.arrears || 0;
              const months = maint > 0 && arr > 0 ? (arr / maint) : 0;
              const arrText = arr > 0
                ? (months > 0 ? `${arr}万两（≈${months.toFixed(1)}月）` : `${arr}万两`)
                : '—';
              return (
                <tr key={army.id}>
                  <td>{army.name}</td>
                  <td>{army.troop_type}</td>
                  <td>{army.manpower}</td>
                  <td>{monthlyAmount(maint)}</td>
                  <td>{army.morale}</td>
                  <td>{arrText}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      ) : <div className="empty-note">本地未记录常驻军。</div>}
      {region ? (
        <>
          <div className="garrison-title">建筑</div>
          {node.buildings && node.buildings.length ? (
            <table className="intel-table">
              <thead>
                <tr><th>名称</th><th>类别</th><th>等级</th><th>完好</th><th>维护</th><th>产出</th></tr>
              </thead>
              <tbody>
                {node.buildings.map((b) => (
                  <tr key={b.id}>
                    <td>{b.name}</td>
                    <td>{b.category}</td>
                    <td>{b.level}</td>
                    <td>{b.condition}</td>
                    <td>{b.maintenance}万/月</td>
                    <td>{b.output_metric ? `${b.output_metric}+${b.output_amount}` : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : <div className="empty-note">本地未记录建筑。</div>}
        </>
      ) : null}
    </>
  );
}

function Info({ label, value, tone }: { label: string; value: React.ReactNode; tone?: string }) {
  return (
    <div className={`info-cell ${tone || ""}`}>
      <span>{label}</span>
      <b>{value}</b>
    </div>
  );
}

function MenuPage({
  status,
  onRefresh,
  onEnterGame,
  error,
  setError,
}: {
  status: MenuStatus | null;
  onRefresh: () => Promise<MenuStatus>;
  onEnterGame: () => Promise<void>;
  error: string;
  setError: (msg: string) => void;
}) {
  const [busy, setBusy] = React.useState<string>("");
  const [showApiForm, setShowApiForm] = React.useState(false);
  const [showSaveList, setShowSaveList] = React.useState(false);

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError("");
    try {
      await fn();
    } catch (err: any) {
      setError(err?.message || String(err));
    } finally {
      setBusy("");
    }
  };

  const onNewGame = () =>
    guard("新游戏中...", async () => {
      if (status?.has_main_db && !window.confirm("将覆盖当前主进度，是否继续？建议先在游戏中保存为存档。")) return;
      await api("/api/menu/new_game", { method: "POST" });
      await onEnterGame();
    });

  const onContinue = () =>
    guard("载入上次进度...", async () => {
      await api("/api/menu/continue", { method: "POST" });
      await onEnterGame();
    });

  const onLoadSave = (name: string) =>
    guard(`载入「${name}」...`, async () => {
      await api(`/api/menu/load_save/${encodeURIComponent(name)}`, { method: "POST" });
      await onEnterGame();
    });

  const hasKey = !!status?.has_api_key;
  const hasMainDb = !!status?.has_main_db;
  const saves = status?.saves || [];

  return (
    <div className="menu-screen">
      <div className="menu-panel">
        <h1 className="menu-title">明末力挽狂澜</h1>
        <p className="menu-subtitle">崇祯元年正月 · 召大臣议天下事</p>

        {!hasKey && (
          <div className="menu-notice">尚未配置 API 接口。请先「设置 API」。</div>
        )}
        {error && <div className="menu-error">{error}</div>}

        <div className="menu-buttons">
          <button className="menu-btn primary" disabled={!hasKey || !!busy} onClick={onNewGame}>
            开始新游戏
          </button>
          <button className="menu-btn" disabled={!hasKey || !hasMainDb || !!busy} onClick={onContinue} title={hasMainDb ? "" : "无上次进度"}>
            继续
          </button>
          <button className="menu-btn" disabled={!hasKey || !!busy || !saves.length} onClick={() => setShowSaveList(true)} title={saves.length ? "" : "暂无存档"}>
            加载存档 {saves.length ? `(${saves.length})` : ""}
          </button>
          <button className="menu-btn" disabled={!!busy} onClick={() => setShowApiForm(true)}>
            设置 API {hasKey ? "" : "（必需）"}
          </button>
        </div>

        {busy && <div className="menu-busy">{busy}</div>}
        {hasKey && status?.llm && (
          <div className="menu-llm-info">
            当前接口：{status.llm.base_url} · {status.llm.model}
          </div>
        )}
      </div>

      {showApiForm && (
        <ApiSettingsModal
          initial={status?.llm}
          onClose={() => setShowApiForm(false)}
          onSaved={async () => {
            setShowApiForm(false);
            await onRefresh();
          }}
        />
      )}

      {showSaveList && (
        <SaveListModal
          saves={saves}
          onClose={() => setShowSaveList(false)}
          onLoad={async (name) => {
            setShowSaveList(false);
            await onLoadSave(name);
          }}
        />
      )}
    </div>
  );
}

function ApiSettingsModal({
  initial,
  onClose,
  onSaved,
}: {
  initial?: {
    base_url: string;
    model: string;
    has_api_key: boolean;
    max_tokens?: number;
    timeout_seconds?: number;
    advanced_model?: string;
    advanced_base_url?: string;
    has_advanced_api_key?: boolean;
  };
  onClose: () => void;
  onSaved: () => Promise<void>;
}) {
  const [baseUrl, setBaseUrl] = React.useState(initial?.base_url || "https://api.deepseek.com");
  const [model, setModel] = React.useState(initial?.model || "deepseek-chat");
  const [advancedModel, setAdvancedModel] = React.useState(initial?.advanced_model || "");
  const [advancedBaseUrl, setAdvancedBaseUrl] = React.useState(initial?.advanced_base_url || "");
  const [advancedApiKey, setAdvancedApiKey] = React.useState("");
  const [apiKey, setApiKey] = React.useState("");
  const [maxTokens, setMaxTokens] = React.useState(String(initial?.max_tokens || 8000));
  const [timeoutSeconds, setTimeoutSeconds] = React.useState(String(initial?.timeout_seconds || 180));
  const [busy, setBusy] = React.useState(false);
  const [err, setErr] = React.useState("");

  const onSave = async () => {
    setBusy(true);
    setErr("");
    try {
      const response = await fetch("/api/menu/llm", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          base_url: baseUrl.trim(),
          model: model.trim(),
          api_key: apiKey.trim(),
          max_tokens: parseInt(maxTokens) || 8000,
          timeout_seconds: parseFloat(timeoutSeconds) || 180,
          advanced_model: advancedModel.trim(),
          advanced_base_url: advancedBaseUrl.trim(),
          advanced_api_key: advancedApiKey.trim(),
        }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({ detail: response.statusText }));
        const detail = normalizeApiError(payload, response.statusText);
        setErr(`code: ${detail.code || "unknown"}\nmessage: ${detail.message || response.statusText}`);
        return;
      }
      await onSaved();
    } catch (e: any) {
      setErr(`code: request_failed\nmessage: ${e?.message || String(e)}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="menu-modal-bg" onClick={onClose}>
      <div className="menu-modal" onClick={(e) => e.stopPropagation()}>
        <h2>设置 API</h2>
        <p className="menu-hint">推荐 DeepSeek（中文好、价格便宜）。配置写入本地，不上传。</p>
        <label>
          Base URL
          <input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="https://api.deepseek.com" />
        </label>
        <label>
          Model
          <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="deepseek-chat" />
        </label>
        <label>
          Advanced Model <small className="menu-hint">（推演 + 打分专用；留空 fallback）</small>
          <input value={advancedModel} onChange={(e) => setAdvancedModel(e.target.value)} placeholder="deepseek-reasoner / gpt-5" />
        </label>
        <label>
          Advanced Base URL <small className="menu-hint">（advanced 专用网关；留空复用主 Base URL）</small>
          <input value={advancedBaseUrl} onChange={(e) => setAdvancedBaseUrl(e.target.value)} placeholder="https://other-gateway/v1" />
        </label>
        <label>
          Advanced API Key{" "}
          <small className="menu-hint">{initial?.has_advanced_api_key ? "(已配置；留空保留)" : "(留空=复用主 API Key)"}</small>
          <input type="password" value={advancedApiKey} onChange={(e) => setAdvancedApiKey(e.target.value)} placeholder={initial?.has_advanced_api_key ? "(已配置；如需更换请重新填写)" : "留空=复用主 Key"} />
        </label>
        <label>
          Max Tokens
          <input type="number" min={256} max={65536} value={maxTokens} onChange={(e) => setMaxTokens(e.target.value)} placeholder="8000" />
        </label>
        <label>
          Timeout Seconds
          <input type="number" min={10} max={900} value={timeoutSeconds} onChange={(e) => setTimeoutSeconds(e.target.value)} placeholder="180" />
        </label>
        <label>
          API Key
          <input type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder={initial?.has_api_key ? "(已配置；如需更换请重新填写)" : "sk-..."} />
        </label>
        {err && <div className="menu-error">{err}</div>}
        <div className="menu-modal-actions">
          <button onClick={onClose} disabled={busy}>取消</button>
          <button className="primary" onClick={onSave} disabled={busy || !baseUrl.trim() || !model.trim() || (!apiKey.trim() && !initial?.has_api_key)}>
            {busy ? "保存中..." : "保存"}
          </button>
        </div>
      </div>
    </div>
  );
}

function SaveListModal({
  saves,
  onClose,
  onLoad,
}: {
  saves: Array<{ name: string; size: number; mtime: number }>;
  onClose: () => void;
  onLoad: (name: string) => Promise<void>;
}) {
  return (
    <div className="menu-modal-bg" onClick={onClose}>
      <div className="menu-modal" onClick={(e) => e.stopPropagation()}>
        <h2>加载存档</h2>
        <ul className="menu-save-list">
          {saves.map((s) => (
            <li key={s.name}>
              <button onClick={() => onLoad(s.name)}>
                <span className="save-name">{s.name}</span>
                <span className="save-meta">{new Date(s.mtime * 1000).toLocaleString("zh-CN")}</span>
              </button>
            </li>
          ))}
        </ul>
        <div className="menu-modal-actions">
          <button onClick={onClose}>关闭</button>
        </div>
      </div>
    </div>
  );
}

// ============================================
// FEATURE COMPONENTS
// ============================================

// --- 热力图 + 势力图标 + 战场动画层 ---
function MapOverlays({ state, selectedNodeId, mapIntelOpen }: { state: GameState; selectedNodeId: string; mapIntelOpen: boolean }) {
  // 热力图：根据各省 unrest 值生成叠加层
  const heatNodes = state.regions.map((r) => {
    const node = state.map_nodes.find((n) => n.region?.id === r.id);
    if (!node) return null;
    const intensity = Math.min(1, (r.unrest || 0) / 100);
    if (intensity < 0.05) return null;
    const red = Math.round(intensity * 220);
    const green = Math.round((1 - intensity) * 80);
    return { x: node.x, y: node.y, color: `rgba(${red},${green},0,${intensity * 0.7})`, name: r.name };
  }).filter(Boolean);

  // 势力图标：外部势力在地图上的位置
  const powerColors: Record<string, string> = {
    houjin: "#8a2f25",
    bandits: "#8a6424",
    mengen: "#3a5a3a",
  };
  const powerEmblems: Record<string, string> = {
    houjin: "金",
    bandits: "寇",
    mengen: "蒙",
  };
  const externalPowers = state.powers.filter((p) => p.kind === "external" || p.id !== "ming");

  return (
    <>
      {/* 热力图 */}
      <div className="map-heat-layer" aria-hidden="true">
        <svg width="100%" height="100%" style={{ position: "absolute", inset: 0 }}>
          {heatNodes.map((n, i) => n && (
            <ellipse key={i} cx={`${n.x}%`} cy={`${n.y}%`} rx="8%" ry="5%"
              fill={n.color} style={{ mixBlendMode: "multiply" }} />
          ))}
        </svg>
      </div>

      {/* 势力图标 */}
      {externalPowers.map((p) => {
        const node = state.map_nodes.find((n) => n.kind === "external" && n.label?.includes(p.name));
        if (!node) return null;
        return (
          <div
            key={p.id}
            className="power-map-icon"
            style={{ left: `${node.x}%`, top: `${node.y}%` }}
            aria-hidden="true"
          >
            <div
              className="power-icon-emblem"
              style={{ background: powerColors[p.id] || "#5a4a30" }}
            >
              {powerEmblems[p.id] || p.name[0]}
            </div>
            <span className="power-icon-label">{p.name}</span>
          </div>
        );
      })}

      {/* 战场动画 */}
      {state.powers.map((p) => {
        if (!p.last_action || p.status === "defeated") return null;
        // 简单动画：如果势力有军事行动，显示行军箭头
        const fromNode = state.map_nodes.find((n) => n.kind === "theater" && n.armies.some((a) => a.controller === p.id));
        const toNode = state.map_nodes[0]; // 默认目标
        if (!fromNode) return null;
        return (
          <svg key={p.id} className="battle-march-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <defs>
              <marker id={`arrowhead-${p.id}`} markerWidth="4" markerHeight="4" refX="2" refY="2" orient="auto">
                <polygon points="0 0, 4 2, 0 4" className="battle-arrow-head" />
              </marker>
            </defs>
            <line
              className="battle-arrow"
              x1={`${fromNode.x}%`} y1={`${fromNode.y}%`}
              x2={`${(fromNode.x + 50) % 100}%`} y2={`${(fromNode.y + 30) % 100}%`}
              markerEnd={`url(#arrowhead-${p.id})`}
            />
          </svg>
        );
      })}
    </>
  );
}

// --- 大臣详情弹窗 ---
function MinisterDetailModal({ minister, onClose, onOpenSkills }: {
  minister: Minister;
  onClose: () => void;
  onOpenSkills?: (m: Minister) => void;
}) {
  const isOusted = minister.status !== "active";
  const isGeneral = minister.office.includes("总兵") || minister.office.includes("将军") || minister.office.includes("镇守");
  const poolFallback = minister.portrait_id ? `/portraits/${minister.portrait_id}.png` : undefined;
  const dedicated = minister.portrait_id?.startsWith("custom:")
    ? `/portraits/custom/${encodeURIComponent(minister.name)}?t=${cacheBust(minister.portrait_id!)}`
    : `/portraits/minister_${minister.id ?? minister.name}.png`;

  return (
    <div className="fullscreen-modal minister-detail-modal" role="dialog" aria-modal="true">
      <div className="modal-bg" onClick={onClose} />
      <div className="modal-panel">
        <div className="modal-header">
          <h2 className="modal-title"><Crown size={18} /> {minister.name}</h2>
          <button className="icon-button" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-scroll">
          <div className="minister-detail-header">
            <img
              src={dedicated}
              alt={minister.name}
              className="minister-detail-portrait"
              onError={(e) => { if (poolFallback) (e.target as HTMLImageElement).src = poolFallback; }}
            />
            <div className="minister-detail-name-block">
              <h3 className="minister-detail-name">{minister.name}</h3>
              <div className="minister-detail-meta">
                {minister.office && <span className="minister-detail-tag">{minister.office}</span>}
                {minister.faction && <span className="minister-detail-tag" style={{ background: "var(--blue)" }}>{minister.faction}</span>}
                <span className={`minister-detail-tag ${isOusted ? "tag-ousted" : ""}`}>{minister.status_label}</span>
                {minister.style && <span className="minister-detail-tag" style={{ background: "var(--jade)" }}>{minister.style}</span>}
              </div>
              <p className="minister-detail-bio">{minister.summary}</p>
            </div>
          </div>

          {minister.skills && minister.skills.length > 0 && (
            <div className="minister-detail-section">
              <h4>特殊技能</h4>
              <div className="skill-badge-list">
                {minister.skills.map((s) => (
                  <div key={s.id} style={{ marginBottom: 6 }}>
                    <span className="skill-badge">{s.name}</span>
                    <p className="skill-badge-desc">{s.description}</p>
                  </div>
                ))}
              </div>
              {isGeneral && onOpenSkills && (
                <button className="primary" style={{ marginTop: 10 }} onClick={() => onOpenSkills(minister)}>
                  查看技能详情
                </button>
              )}
            </div>
          )}

          <div className="minister-detail-section">
            <h4>派系</h4>
            <p>{minister.faction || "无派系"}</p>
          </div>

          <div className="minister-detail-section">
            <h4>备注</h4>
            <p>{minister.status_reason || "无"}</p>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- 人物关系网络图 ---
function FactionNetworkModal({ ministers, onClose, onSelectMinister }: {
  ministers: Minister[];
  onClose: () => void;
  onSelectMinister: (m: Minister) => void;
}) {
  // 派系颜色映射
  const factionColors: Record<string, string> = {
    "东林": "#3a6b8a",
    "阉党": "#8a2f25",
    "浙党": "#5a7a3a",
    "楚党": "#7a5a3a",
    "东宫": "#6a3a7a",
    "后金": "#8a2f25",
    "流寇": "#8a6424",
  };
  const factionColor = (f: string) => factionColors[f] || "#725f42";

  // 简化布局：按派系分组放射状
  const factions = [...new Set(ministers.map((m) => m.faction).filter(Boolean))] as string[];
  const centerX = 280, centerY = 190, radius = 140;
  const nodeMap: Record<string, { x: number; y: number; members: Minister[] }> = {};
  factions.forEach((f, fi) => {
    const angle = (fi / factions.length) * 2 * Math.PI - Math.PI / 2;
    nodeMap[f] = {
      x: centerX + radius * Math.cos(angle),
      y: centerY + radius * Math.sin(angle),
      members: ministers.filter((m) => m.faction === f),
    };
  });

  const edges: Array<{ x1: number; y1: number; x2: number; y2: number; hostile?: boolean }> = [];
  for (let i = 0; i < factions.length; i++) {
    for (let j = i + 1; j < factions.length; j++) {
      const fi = factions[i], fj = factions[j];
      const hostile = (fi === "东林" && fj === "阉党") || (fi === "阉党" && fj === "东林");
      edges.push({
        x1: nodeMap[fi].x, y1: nodeMap[fi].y,
        x2: nodeMap[fj].x, y2: nodeMap[fj].y,
        hostile: !!hostile,
      });
    }
  }

  return (
    <div className="fullscreen-modal" role="dialog" aria-modal="true">
      <div className="modal-bg" onClick={onClose} />
      <div className="modal-panel" style={{ maxWidth: 640 }}>
        <div className="modal-header">
          <h2 className="modal-title"><Landmark size={18} /> 派系关系图</h2>
          <button className="icon-button" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-scroll">
          <div className="faction-network-wrap">
            <svg width="560" height="380" viewBox="0 0 560 380">
              {/* 连线 */}
              {edges.map((e, i) => (
                <line key={i} className={`faction-edge ${e.hostile ? "edge-hostile" : ""}`}
                  x1={e.x1} y1={e.y1} x2={e.x2} y2={e.y2} />
              ))}
              {/* 派系节点 */}
              {Object.entries(nodeMap).map(([f, data]) => (
                <g key={f} className="faction-node" onClick={() => onSelectMinister(data.members[0])}>
                  <circle cx={data.x} cy={data.y} r={22} fill={factionColor(f)} opacity={0.85}
                    stroke="rgba(255,239,185,0.6)" strokeWidth={1.5} />
                  <text x={data.x} y={data.y + 4} className="faction-node-label" fill="#fff8e8">{f}</text>
                  <text x={data.x} y={data.y + 38} textAnchor="middle" fontSize={10} fill="var(--muted)">
                    {data.members.length}人
                  </text>
                </g>
              ))}
            </svg>
          </div>
          <div className="faction-legend">
            {factions.map((f) => (
              <div key={f} className="faction-legend-item">
                <span className="faction-legend-dot" style={{ background: factionColor(f) }} />
                <span>{f}</span>
              </div>
            ))}
            <div className="faction-legend-item">
              <span style={{ width: 20, height: 2, background: "var(--cinnabar)", display: "inline-block" }} />
              <span>敌对</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- 武将技能树面板 ---
function GeneralSkillPanel({ minister, onClose }: { minister: Minister; onClose: () => void }) {
  const skills = minister.skills || [];
  const skillIcons = ["⚔️", "🏹", "🛡️", "🎯", "🔥", "💨"];
  return (
    <div className="fullscreen-modal" role="dialog" aria-modal="true">
      <div className="modal-bg" onClick={onClose} />
      <div className="modal-panel" style={{ maxWidth: 560 }}>
        <div className="modal-header">
          <h2 className="modal-title"><Swords size={18} /> {minister.name} — 技能</h2>
          <button className="icon-button" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-scroll">
          <div className="general-skill-panel">
            {skills.length === 0 && <p className="empty-note">此将暂无特殊技能记录。</p>}
            {skills.map((s, i) => (
              <div key={s.id} className="skill-tree-row">
                <div className="skill-icon-box">{skillIcons[i % skillIcons.length]}</div>
                <div className="skill-tree-info">
                  <div className="skill-tree-name">{s.name}</div>
                  <div className="skill-tree-sources">{s.sources.join(" / ")}</div>
                  <div className="skill-tree-desc">{s.description}</div>
                </div>
              </div>
            ))}
          </div>
          <div style={{ padding: "12px 0", borderTop: "1px solid var(--border)", marginTop: 12 }}>
            <h4 style={{ fontFamily: "var(--font-brush)", marginBottom: 8 }}>兵种信息</h4>
            <p style={{ fontSize: "0.88rem", color: "var(--muted)" }}>
              现任职务：{minister.office || "无"} &nbsp;|&nbsp; 派系：{minister.faction || "无"}
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

// --- 后宫妃嫔档案卡 ---
function ConsortProfileModal({ consort, onClose }: { consort: Minister; onClose: () => void }) {
  const poolFallback = consort.portrait_id ? `/portraits/${consort.portrait_id}.png` : undefined;
  const dedicated = consort.portrait_id?.startsWith("custom:")
    ? `/portraits/custom/${encodeURIComponent(consort.name)}?t=${cacheBust(consort.portrait_id!)}`
    : `/portraits/consort_${consort.id ?? consort.name}.png`;

  return (
    <div className="fullscreen-modal consort-profile-modal" role="dialog" aria-modal="true">
      <div className="modal-bg" onClick={onClose} />
      <div className="modal-panel" style={{ maxWidth: 520 }}>
        <div className="modal-header">
          <h2 className="modal-title"><Crown size={18} /> 妃嫔档案</h2>
          <button className="icon-button" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="modal-scroll">
          <div className="consort-profile-card">
            <img
              src={dedicated}
              alt={consort.name}
              className="consort-profile-portrait"
              onError={(e) => { if (poolFallback) (e.target as HTMLImageElement).src = poolFallback; }}
            />
            <h3 className="consort-profile-name">{consort.name}</h3>
            <div className="consort-profile-tags">
              {consort.office && <span className="minister-detail-tag">{consort.office}</span>}
              {consort.style && <span className="minister-detail-tag" style={{ background: "var(--jade)" }}>{consort.style}</span>}
              <span className={`minister-detail-tag ${consort.status !== "active" ? "tag-ousted" : ""}`}>{consort.status_label}</span>
            </div>

            <div className="consort-profile-section">
              <h4>简介</h4>
              <p style={{ fontSize: "0.9rem", lineHeight: 1.6 }}>{consort.summary}</p>
            </div>

            {consort.skills && consort.skills.length > 0 && (
              <div className="consort-profile-section">
                <h4>才艺</h4>
                <div className="skill-badge-list">
                  {consort.skills.map((s) => (
                    <span key={s.id} className="skill-badge">{s.name}</span>
                  ))}
                </div>
              </div>
            )}

            <div className="consort-profile-section">
              <h4>备注</h4>
              <p style={{ fontSize: "0.85rem", color: "var(--muted)" }}>{consort.status_reason || "无"}</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
