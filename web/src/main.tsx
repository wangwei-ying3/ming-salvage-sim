import React from "react";
import { createRoot } from "react-dom/client";
import {
  Check,
  Crown,
  Edit3,
  Landmark,
  Loader2,
  MapPinned,
  Menu,
  Save,
  Send,
  Settings,
  ScrollText,
  Shield,
  Star,
  Coins,
  Trash2,
  Swords,
  RotateCcw,
  Upload,
  Users,
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

type ExternalPower = {
  id: string;
  name: string;
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
  name: string;
  office: string;
  office_type: string;
  faction: string;
  style: string;
  summary: string;
  favorite: boolean;
  loyalty: number;
  ability: number;
  integrity: number;
  courage: number;
  status: string;
  status_label: string;
  birth_year: number;
  death_year: number;
  death_info: string;
  death_cause: string;
  imprison_info: string;
  imprisoned_cause: string;
  current_office: string;
  skills: Array<{ id: string; name: string; sources: string[]; description: string }>;
  east_bureau_bound: boolean;
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

type BudgetAccount = {
  balance: number;
  income: BudgetItem[];
  expense: BudgetItem[];
  income_total: number;
  expense_total: number;
  net: number;
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
  external_power_warning: string;
  external_powers: ExternalPower[];
  victory_status: { status: string; summary: string };
  events: EventItem[];
  regions: Region[];
  armies: Army[];
  map_nodes: MapNode[];
  ministers: Minister[];
  directives: Directive[];
  pending_count: number;
  last_decree: string;
  last_report: string;
};

type ChatMessage = { role: "user" | "minister"; content: string };
type ChatDisplayMessage = ChatMessage & { pending?: boolean };
type Suggestion = { label: string; text: string };
type ModalName = "none" | "state" | "chat" | "edict" | "report" | "extraction" | "history" | "menu" | "officials" | "court" | "end";
type EndGameSummary = { score: number; title: string; summary: string } | null;
type SaveEntry = { name: string; size: number; mtime: number };
type LLMConfigInfo = {
  base_url: string;
  model: string;
  has_api_key: boolean;
  persisted: { base_url: string; model: string; has_api_key: boolean };
};
type ProposedDirective = { id: number; text: string; status: string; notes: string };
type ChatResponse = {
  answer: string;
  history: ChatMessage[];
  suggestions: Suggestion[];
  directives: Directive[];
  court_action?: string;
  next_minister?: string;
  proposed_directive?: ProposedDirective | null;
};

const safeStr = (v: unknown): string => {
  if (typeof v === "string") return v;
  if (v === null || v === undefined) return "";
  try { return JSON.stringify(v); } catch { return String(v); }
};

const api = async <T,>(path: string, options?: RequestInit): Promise<T> => {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options?.headers || {}) },
    ...options,
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: response.statusText }));
    throw new Error(error.detail || response.statusText);
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
    throw new Error(error.detail || response.statusText);
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
        onDelta(typeof payload.content === "string" ? payload.content : JSON.stringify(payload.content ?? ""));
      } else if (parsed.event === "done") {
        return payload as ChatResponse;
      } else if (parsed.event === "error") {
        throw new Error(payload.message || "流式回复失败。");
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

const shortSuggestionLabel = (suggestion: Suggestion) => {
  const label = safeStr(suggestion.label);
  const stext = safeStr(suggestion.text);
  const combined = `${label} ${stext}`;
  if (combined.includes("辽") || combined.includes("关宁") || combined.includes("边") || combined.includes("驻军")) return "问军务";
  if (combined.includes("钱") || combined.includes("库") || combined.includes("太仓") || combined.includes("饷")) return "问钱粮";
  if (combined.includes("拟旨") || combined.includes("旨")) return "命其拟旨";
  if (combined.includes("阻力")) return "问阻力";
  if (combined.includes("密查") || combined.includes("账册")) return "命其密查";
  if (combined.includes("举荐") || combined.includes("人物")) return "让其举荐";
  if (combined.includes("奏报")) return "问奏报";
  if (combined.includes("登记") || combined.includes("承办")) return "命其承办";
  return label.replace(/^请/, "").slice(0, 6);
};

// ── 官员面板小部件 ─────────────────────────────────────────────────
const hiddenFactions = new Set(["东林", "皇党", "阉党"]);
const factionColor = (faction: string): string => {
  const colors: Record<string, string> = {
    "皇党": "#c4a45a", "阉党": "#a03737", "东林": "#3d7a5a",
    "军队": "#7a5a3d", "宗室": "#8a5a8a", "中立": "#6a7a8a",
    "西学": "#3a7a9a", "技术官僚": "#3a7a9a",
  };
  return colors[faction] || "#6a7a8a";
};

function StatBar({ label, value }: { label: string; value: number }) {
  const tone = value >= 70 ? "high" : value >= 40 ? "mid" : "low";
  return (
    <span className="stat-bar" title={`${label} ${value}`}>
      <small>{label}</small>
      <span className={`stat-track stat-${tone}`}>
        <span className="stat-fill" style={{ width: `${value}%` }} />
      </span>
    </span>
  );
}

function MiniStat({ label, value }: { label: string; value: number }) {
  const tone = value >= 70 ? "high" : value >= 40 ? "mid" : "low";
  return (
    <small className={`mini-stat mini-${tone}`} title={`${label} ${value}`}>
      {label}{value}
    </small>
  );
}

const statusBadge = (status: string, _label: string): string => {
  const map: Record<string, string> = {
    dismissed: "badge-danger",
    exiled: "badge-danger",
    imprisoned: "badge-warn",
    retired: "badge-muted",
    dead: "badge-dead",
  };
  return map[status] || "badge-muted";
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

function App() {
  const [state, setState] = React.useState<GameState | null>(null);
  const [selectedNodeId, setSelectedNodeId] = React.useState<string>("");
  const [mapIntelOpen, setMapIntelOpen] = React.useState(false);
  const [drawerOpen, setDrawerOpen] = React.useState(false);
  const [ministerGroup, setMinisterGroup] = React.useState("内阁");
  const [selectedMinister, setSelectedMinister] = React.useState<string>("");
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
  const [endGameSummary, setEndGameSummary] = React.useState<EndGameSummary>(null);
  const [endGameBusy, setEndGameBusy] = React.useState(false);

  // Prevent background scrolling when drawer or modal is open
  React.useEffect(() => {
    const isOpen = drawerOpen || activeModal !== "none";
    if (isOpen) {
      document.documentElement.style.overflow = "hidden";
      document.body.style.overflow = "hidden";
    } else {
      document.documentElement.style.overflow = "";
      document.body.style.overflow = "";
    }
    return () => {
      document.documentElement.style.overflow = "";
      document.body.style.overflow = "";
    };
  }, [drawerOpen, activeModal]);

  const loadState = React.useCallback(async () => {
    const data = await api<GameState>("/api/game/state");
    setState(data);
    setSelectedNodeId((current) => current || data.map_nodes[0]?.id || "");
    setDecree(data.last_decree || "");
    setReport(data.last_report || "");
  }, [selectedMinister]);

  const loadMinisterChat = React.useCallback(async (ministerName: string) => {
    const data = await api<{ history: ChatMessage[]; suggestions: Suggestion[] }>(`/api/ministers/${encodeURIComponent(ministerName)}/chat`);
    setChat(data.history);
    setSuggestions(data.suggestions);
  }, []);

  React.useEffect(() => {
    loadState().catch((err) => setError(err.message));
  }, [loadState]);

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

  React.useEffect(() => {
    if (!mapIntelOpen) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMapIntelOpen(false);
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [mapIntelOpen]);

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
  const activeMinister = selectedMinister ? state.ministers.find((minister) => minister.name === selectedMinister) || null : null;
  const mapIntelStyle = selectedNode ? getMapIntelStyle(selectedNode) : undefined;

  const openChat = (minister: Minister, pleadMessage?: string) => {
    const switchingMinister = selectedMinister !== minister.name;
    if (switchingMinister) {
      setChat([]);
      setSuggestions([]);
    }
    setSelectedMinister(minister.name);
    setActiveModal("chat");
    setDrawerOpen(false);
    setError("");
    setComposerHint("");
    setChatNotice(pleadMessage ? `${minister.name}跪地求情。` : "");
    setPendingUserMessage("");
    setStreamingMinisterMessage("");
    loadMinisterChat(minister.name).then(() => {
      if (pleadMessage) {
        setChat(prev => {
          const msg: ChatMessage = { role: "minister", content: pleadMessage };
          if (prev.some(m => m.role === "minister" && m.content === pleadMessage)) return prev;
          return [msg, ...prev];
        });
        setSuggestions([
          { label: "准", text: "准你所请，留在原职。" },
          { label: "驳", text: "朕意已决，不必多言。" },
        ]);
      }
    }).catch((err) => setError(err.message));
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

  const endGameClick = async (choice: string) => {
    if (endGameBusy) return;
    setEndGameBusy(true);
    setActiveModal("none");
    try {
      const data = await api<EndGameSummary>("/api/game/end", {
        method: "POST",
        body: JSON.stringify({ choice }),
      });
      setEndGameSummary(data);
      setActiveModal("none");
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setEndGameBusy(false);
    }
  };

  const closeEndGameSummary = async () => {
    setEndGameSummary(null);
    setActiveModal("none");
    try {
      await api("/api/game/restart", { method: "POST" });
    } catch { /* ignore */ }
    window.location.reload();
  };

  return (
    <main className="game-shell">
      <GrandMap nodes={state.map_nodes} selectedId={mapIntelOpen ? selectedNode?.id || "" : ""} onSelect={selectMapNode} />
      <TopStatusBar
        state={state}
        onOpenState={() => setActiveModal("state")}
        onOpenMenu={() => setActiveModal("menu")}
      />
      <BottomCommandBar
        eventsCount={state.events.length}
        directivesCount={state.directives.length}
        onOpenMemorials={() => setActiveModal("state")}
        onOpenEdict={() => setActiveModal("edict")}
        onOpenExtraction={() => setActiveModal("extraction")}
        onOpenHistory={() => setActiveModal("history")}
        onEndGame={() => setActiveModal("end")}
      />

      <CourtDrawer
        state={state}
        ministers={ministers}
        armies={state.armies}
        ministerGroup={ministerGroup}
        selectedMinister={selectedMinister}
        open={drawerOpen}
        onGroupChange={setMinisterGroup}
        onToggle={() => setDrawerOpen((current) => !current)}
        onClose={guardClose(() => setDrawerOpen(false))}
        onOpenChat={openChat}
        onRefresh={loadState}
      />

      <button className="court-toggle officials-toggle" onClick={() => setActiveModal("officials")} aria-label="官员表">
        <Users size={16} />
        <span>官员表</span>
      </button>
      <button className="court-toggle court-assembly-toggle" onClick={() => setActiveModal("court")} aria-label="朝会">
        <Landmark size={16} />
        <span>朝会</span>
      </button>

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
            chat={chat}
            suggestions={suggestions}
            pendingUserMessage={pendingUserMessage}
            streamingMinisterMessage={streamingMinisterMessage}
            chatNotice={chatNotice}
            composerHint={composerHint}
            input={input}
            busy={busy}
            error={error}
            onInput={setInput}
            onSend={sendChat}
            onHint={setComposerHint}
            onFavorite={() => toggleFavorite(activeMinister)}
            onOpenEdict={() => setActiveModal("edict")}
            onClose={guardClose(() => setActiveModal("none"))}
          />
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

      {activeModal === "report" && report ? (
        <ReportModal report={report} onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "extraction" ? (
        <ExtractionModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "officials" ? (
        <OfficialsPanel
          state={state}
          onClose={guardClose(() => setActiveModal("none"))}
          onRefresh={loadState}
          onOpenChat={openChat}
          ministers={state.ministers}
        />
      ) : null}

      {activeModal === "court" ? (
        <CourtAssembly
          state={state}
          onClose={guardClose(() => setActiveModal("none"))}
          onRefresh={loadState}
        />
      ) : null}

      {activeModal === "history" ? (
        <HistoryModal onClose={guardClose(() => setActiveModal("none"))} />
      ) : null}

      {activeModal === "end" ? (
        <div className="center-layer" role="dialog" aria-modal="true">
          <div className="center-modal end-game-modal">
            <div className="center-modal-header">
              <h1><Landmark size={18} /> 结束游戏</h1>
              <button className="icon-button" aria-label="关闭" onClick={() => setActiveModal("none")}><X size={16} /></button>
            </div>
            <div className="end-game-body">
              <p className="end-game-prompt">你打算如何结束这一生？</p>
              <div className="end-game-choices">
                <button className="end-game-choice" onClick={() => endGameClick("自吊东南枝")} disabled={endGameBusy}>
                  <span className="end-game-choice-icon">🌳</span>
                  <span className="end-game-choice-label">自吊东南枝</span>
                  <span className="end-game-choice-desc">煤山老槐树下，一尺白绫了却残生</span>
                </button>
                <button className="end-game-choice" onClick={() => endGameClick("退位让贤")} disabled={endGameBusy}>
                  <span className="end-game-choice-icon">👑</span>
                  <span className="end-game-choice-label">退位让贤</span>
                  <span className="end-game-choice-desc">逊位禅让，将江山托付有德之人</span>
                </button>
              </div>
              {endGameBusy ? <p className="end-game-busy">史官秉笔，正在撰写青史……</p> : null}
            </div>
          </div>
        </div>
      ) : null}

      {endGameSummary ? (
        <FullscreenModal title={endGameSummary.title || "史鉴"} subtitle={`评分：${endGameSummary.score}/100`} bgClass="modal-bg-state" onClose={closeEndGameSummary}>
          <article className="state-document modal-scroll">
            <div className="document-section">
              <div className="end-game-score">
                <span className={`end-game-score-value ${endGameSummary.score >= 80 ? "score-high" : endGameSummary.score >= 60 ? "score-mid" : "score-low"}`}>
                  {endGameSummary.score}
                </span>
                <span className="end-game-score-label">/ 100</span>
              </div>
              <pre className="memorial-text end-game-summary-text">{endGameSummary.summary}</pre>
            </div>
          </article>
        </FullscreenModal>
      ) : null}

      {activeModal === "menu" ? (
        <GameMenuModal
          onClose={guardClose(() => setActiveModal("none"))}
          onAfterLoad={() => {
            setActiveModal("none");
            window.location.reload();
          }}
        />
      ) : null}

      {closedModal.length ? (
        <ClosedIssuesModal items={closedModal} onClose={() => setClosedModal([])} />
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
        <p>{stage ? `当前：${stage}` : "朝廷推演钱粮、地方、军务，请勿操作。"}</p>
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

function CourtDrawer({
  state,
  ministers,
  armies,
  ministerGroup,
  selectedMinister,
  open,
  onGroupChange,
  onToggle,
  onClose,
  onOpenChat,
  onRefresh,
}: {
  state: GameState;
  ministers: Minister[];
  armies: Army[];
  ministerGroup: string;
  selectedMinister: string;
  open: boolean;
  onGroupChange: (group: string) => void;
  onToggle: () => void;
  onClose: () => void;
  onOpenChat: (minister: Minister, pleadMessage?: string) => void;
  onRefresh: () => void;
}) {
  const prevOpen = React.useRef(false);
  const [actionBusy, setActionBusy] = React.useState("");

  // 每次打开朝堂时刷新数据
  React.useEffect(() => {
    if (open && !prevOpen.current) onRefresh();
    prevOpen.current = open;
  }, [open, onRefresh]);

  const doDismiss = async (name: string) => {
    setActionBusy(name);
    try {
      const r = await fetch(`/api/game/dismiss_officer?name=${encodeURIComponent(name)}`, { method: "POST" });
      if (r.status === 400) {
        const data = await r.json();
        if (data.detail?.includes("皇威不足") && confirm(`${data.detail}`)) {
          await fetch(`/api/game/dismiss_officer?name=${encodeURIComponent(name)}&force=true`, { method: "POST" });
        }
      }
      onRefresh();
    } catch { /* ignore */ }
    setActionBusy("");
  };

  const doRelease = async (name: string) => {
    setActionBusy(name);
    try {
      await fetch(`/api/game/release?name=${encodeURIComponent(name)}`, { method: "POST" });
      onRefresh();
    } catch { /* ignore */ }
    setActionBusy("");
  };

  const doImprison = async (name: string) => {
    setActionBusy(name);
    try {
      const r = await fetch(`/api/game/dismiss?name=${encodeURIComponent(name)}`, { method: "POST" });
      if (r.status === 400) {
        const data = await r.json();
        if (data.detail?.includes("皇威不足") && confirm(`${data.detail}`)) {
          await fetch(`/api/game/dismiss?name=${encodeURIComponent(name)}&force=true`, { method: "POST" });
        }
      }
      onRefresh();
    } catch { /* ignore */ }
    setActionBusy("");
  };

  const statusCounts = React.useMemo(() => {
    const counts: Record<string, number> = {};
    for (const m of state.ministers) {
      const key = m.status === "active" ? "在任" : m.status_label;
      counts[key] = (counts[key] || 0) + 1;
    }
    return counts;
  }, [state.ministers]);

  const allCount = state.ministers.length;

  // For "全部" view, group ministers by status
  const grouped = ministerGroup === "全部"
    ? statusGroups.map((sg) => ({
        ...sg,
        list: ministers.filter((m) => m.status === sg.key),
      })).filter((g) => g.list.length > 0)
    : null;

  return (
    <>
      <button className={`court-toggle ${open ? "open" : ""}`} onClick={onToggle} aria-expanded={open} aria-label="打开朝堂">
        <Landmark size={18} />
        <span>朝堂</span>
      </button>
      {open && <button className="drawer-scrim" aria-label="收起朝堂" onClick={onClose} />}
      <aside className={`court-drawer overlay-panel ${open ? "open" : ""}`}>
        <div className="drawer-brand" style={{ marginBottom: 8 }}>
          <div className="panel-title">
            <Landmark size={17} />
            <span>朝堂快捷</span>
          </div>
          <div className="drawer-header-right">
            <span className={`drawer-wei ${(state.metrics["皇威"] || 0) < 30 ? "wei-low" : ""}`}>
              皇威{state.metrics["皇威"] || 0}
            </span>
            <button className="icon-button" aria-label="收起朝堂" onClick={onClose}>
              <X size={16} />
            </button>
          </div>
        </div>
        <div className="minister-list">
          <div className="drawer-legend" style={{ marginBottom: 8, fontSize: "0.72rem" }}>
            <span>共 {allCount} 名官员</span>
            {Object.entries(statusCounts).map(([label, count]) => (
              <span key={label} className="status-summary-item">{label}{count}</span>
            ))}
          </div>
          <div className="segmented" style={{ marginBottom: 8 }}>
            {["内阁", "六部", "军队", "收藏", "全部"].map((group) => (
              <button className={ministerGroup === group ? "active" : ""} key={group} onClick={() => onGroupChange(group)}>
                {group}
              </button>
            ))}
          </div>
          {ministerGroup === "军队" ? (
            armies.map((army) => (
              <ArmyCardItem key={army.id} army={army} />
            ))
          ) : grouped ? (
            statusGroups.map((g) => {
              const list = ministers.filter((m) => m.status === g.key);
              if (!list.length) return null;
              return (
                <div key={g.key} className="status-group">
                  <div className="status-group-head">{g.label}（{list.length}）</div>
                  {list.map((minister) => (
                    <MinisterCardItem
                      key={minister.name}
                      minister={minister}
                      selected={selectedMinister === minister.name}
                      actionBusy={actionBusy}
                      onOpenChat={onOpenChat}
                      onDismiss={doDismiss}
                      onRelease={doRelease}
                      onImprison={doImprison}
                    />
                  ))}
                </div>
              );
            })
          ) : (
            ministers.map((minister) => (
              <MinisterCardItem
                key={minister.name}
                minister={minister}
                selected={selectedMinister === minister.name}
                actionBusy={actionBusy}
                onOpenChat={onOpenChat}
                onDismiss={doDismiss}
                onRelease={doRelease}
                onImprison={doImprison}
              />
            ))
          )}
          {!ministers.length && <div className="empty-note" style={{ marginTop: 8 }}>此栏暂无可召见大臣。</div>}
        </div>
      </aside>
    </>
  );
}

function MinisterCardItem({
  minister,
  selected,
  actionBusy,
  onOpenChat,
  onDismiss,
  onRelease,
  onImprison,
}: {
  minister: Minister;
  selected: boolean;
  actionBusy: string;
  onOpenChat: (m: Minister, pleadMessage?: string) => void;
  onDismiss: (name: string) => void;
  onRelease: (name: string) => void;
  onImprison: (name: string) => void;
}) {
  return (
    <button
      className={`minister-card ${selected ? "selected" : ""}`}
      onClick={() => onOpenChat(minister)}
    >
      <div className="minister-card-top">
      <span className="minister-name">{minister.name}</span>
        <span className="minister-office">{minister.current_office || minister.office}</span>
      </div>
      <span className="minister-bio">{minister.summary}</span>
      <span className="minister-tags">
        {!hiddenFactions.has(minister.faction) && (
          <span className="faction-tag" style={{ backgroundColor: factionColor(minister.faction) }}>
            {minister.faction}
          </span>
        )}
        {minister.status !== "active" && (
          <span className={`status-tag ${statusBadge(minister.status, minister.status_label)}`}>
            {minister.status_label}
          </span>
        )}
      </span>
      <span className="minister-stats">
        <StatBar label="忠" value={minister.loyalty} />
        <StatBar label="能" value={minister.ability} />
        <StatBar label="廉" value={minister.integrity} />
        <StatBar label="勇" value={minister.courage} />
      </span>
      {minister.status !== "active" && (minister.death_info || minister.imprison_info) && !minister.death_cause?.startsWith("处决") && (
        <span className="minister-status-note">
          {minister.death_info ? <span className="death-note">{minister.death_info}</span> : null}
          {minister.imprison_info && !minister.death_info ? <span className="imprison-note">{minister.imprison_info}</span> : null}
        </span>
      )}
      <div className="minister-actions" onClick={(e) => e.stopPropagation()}>
        {minister.status === "active" && (
          <button
            className="card-action danger"
            disabled={actionBusy === minister.name}
            onClick={() => { if (confirm(`确定罢黜 ${minister.name}？`)) onDismiss(minister.name); }}
          >
            罢免
          </button>
        )}
        {minister.status === "imprisoned" && (
          <button
            className="card-action ok"
            disabled={actionBusy === minister.name}
            onClick={() => { if (confirm(`确定释放 ${minister.name}？`)) onRelease(minister.name); }}
          >
            释放
          </button>
        )}
        {minister.status === "active" && (
          <button
            className="card-action warn"
            disabled={actionBusy === minister.name}
            onClick={() => { if (confirm(`确定将 ${minister.name} 下狱？`)) onImprison(minister.name); }}
          >
            下狱
          </button>
        )}
      </div>
      {minister.favorite && <Star className="favorite-mark" size={13} />}
    </button>
  );
}

function ArmyCardItem({ army }: { army: Army }) {
  const statColor = (v: number) => v >= 60 ? "#2ecc71" : v >= 40 ? "#f39c12" : "#e74c3c";
  return (
    <div className="army-card">
      <div className="army-card-header">
        <span className="army-name">{army.name}</span>
        <span className="army-station">{army.station}</span>
      </div>
      <div className="army-commander">
        <span className="army-commander-label">将:</span>
        <span>{army.commander}</span>
      </div>
      <div className="army-stats">
        <span className="army-stat" style={{ color: statColor(army.manpower) }}>兵:{Math.round(army.manpower / 10000)}万</span>
        <span className="army-stat" style={{ color: statColor(army.supply) }}>粮:{army.supply}</span>
        <span className="army-stat" style={{ color: statColor(army.morale) }}>气:{army.morale}</span>
        <span className="army-stat" style={{ color: statColor(army.training) }}>训:{army.training}</span>
        <span className="army-stat" style={{ color: statColor(army.equipment) }}>装:{army.equipment}</span>
        <span className="army-stat" style={{ color: statColor(100 - army.arrears) }}>饷:{100 - army.arrears}</span>
        <span className="army-stat" style={{ color: statColor(army.loyalty) }}>忠:{army.loyalty}</span>
      </div>
      <div className="army-status">{army.status}</div>
    </div>
  );
}

function OfficialsPanel({
  state,
  onClose,
  onRefresh,
  onOpenChat,
  ministers,
}: {
  state: GameState;
  onClose: () => void;
  onRefresh: () => void;
  onOpenChat: (m: Minister, pleadMessage?: string) => void;
  ministers: Minister[];
}) {
  const [name, setName] = React.useState("");
  const [office, setOffice] = React.useState("");
  const [officeTypes] = React.useState(["内阁", "吏部", "户部", "礼部", "兵部", "刑部", "工部", "锦衣卫", "东厂", "司礼监", "边镇"]);
  const [result, setResult] = React.useState("");
  const [error, setError] = React.useState("");

  const createOfficial = async () => {
    if (!name.trim() || !office.trim()) { setError("姓名和官职不能为空"); return; }
    setError(""); setResult("");
    try {
      const res = await fetch("/api/game/create_official", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name: name.trim(), office: office.trim() }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || "创建失败"); return; }
      setResult(`已创建：${data.name}，任 ${data.office}（${data.office_type}）`);
      setName(""); setOffice("");
      onRefresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "创建失败");
    }
  };

  const doTransfer = async (ministerName: string, newDept: string) => {
    if (!newDept) return;

    // 东厂固任者不可再调
    const curMin = ministers.find((m) => m.name === ministerName);
    if (curMin?.east_bureau_bound) {
      setError(`${ministerName}已入东厂，不可调任他处`);
      return;
    }

    // 调往东厂前警告：不可再调出
    if (newDept === "东厂") {
      const ok = confirm(`⚠️ 将 ${ministerName} 调往东厂后：\n\n1. 不可再调回六部/内阁\n2. 忠诚度降为零\n3. 每次月过后有1%概率发动政变\n\n确定要调任东厂吗？`);
      if (!ok) return;
    }

    const deptMap: Record<string, string> = {
      "内阁": "内阁大学士", "吏部": "吏部侍郎", "户部": "户部侍郎",
      "礼部": "礼部侍郎", "兵部": "兵部侍郎", "刑部": "刑部侍郎",
      "工部": "工部侍郎", "锦衣卫": "锦衣卫指挥使", "东厂": "东厂提督",
      "司礼监": "司礼监太监", "边镇": "蓟辽督师",
    };
    try {
      const res = await fetch("/api/game/transfer_official", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: ministerName,
          new_office: deptMap[newDept] || `${newDept}主事`,
          new_office_type: newDept,
        }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || "调动失败"); return; }
      setResult(data.message);
      onRefresh();
      // 调任东厂触发求情 → 打开聊天窗口，自动显示求情语
      if (data.plead) {
        const minister = ministers.find((m) => m.name === ministerName);
        if (minister) {
          onOpenChat(minister, data.plead_message);
        }
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "调动失败");
    }
  };

  const statusCounts: Record<string, number> = {};
  for (const m of state.ministers) {
    const key = m.status === "active" ? "在任" : m.status_label;
    statusCounts[key] = (statusCounts[key] || 0) + 1;
  }

  return (
    <div className="center-layer">
      <div className="center-scrim" onClick={onClose} />
      <div className="center-modal officials-modal">
        <div className="center-modal-header">
          <h1><Users size={18} /> 官员表</h1>
          <button className="icon-button" aria-label="关闭" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="officials-body">
          <div className="officials-summary">
            <span>共 {state.ministers.length} 名</span>
            {Object.entries(statusCounts).map(([label, count]) => (
              <span key={label} className="status-summary-item">{label}{count}</span>
            ))}
          </div>

          <div className="officials-create">
            <h3>生成新官员</h3>
            <div className="officials-form">
              <label>
                <span>姓名</span>
                <input className="menu-input" value={name} onChange={(e) => setName(e.target.value)} placeholder="输入官员姓名" />
              </label>
              <label>
                <span>官职</span>
                <input className="menu-input" value={office} onChange={(e) => setOffice(e.target.value)} placeholder="如：兵部员外郎" />
              </label>
              <div className="officials-department-hint">
                <span>快捷选部：</span>
                {officeTypes.map((ot) => (
                  <button
                    key={ot}
                    className={`office-type-tag ${office.includes(ot) ? "active" : ""}`}
                    onClick={() => {
                      if (ot === "内阁") setOffice("内阁大学士");
                      else if (ot === "边镇") setOffice("蓟辽督师");
                      else setOffice(`${ot}侍郎`);
                    }}
                  >
                    {ot}
                  </button>
                ))}
              </div>
              <button className="primary-action" onClick={createOfficial} style={{ marginTop: 8 }}>
                生成
              </button>
            </div>
            {error && <div className="menu-error">{error}</div>}
            {result && <div className="menu-success">{result}</div>}
          </div>

          <div className="officials-list">
            <h3>全部官员</h3>
            <div className="officials-table">
              <div className="officials-row officials-head">
                <span>姓名</span>
                <span>官职</span>
                <span>部门</span>
                <span>状态</span>
                <span>操作</span>
              </div>
              {state.ministers.map((m) => (
                <div key={m.name} className="officials-row">
                  <span className="officials-name">{m.name}</span>
                  <span>{m.current_office || m.office}</span>
                  <span>{m.office_type}</span>
                  <span>
                    {m.status !== "active" ? (
                      <span className={`status-tag ${statusBadge(m.status, m.status_label)}`}>{m.status_label}</span>
                    ) : (
                      <span style={{ color: "#2f7651", fontWeight: 700 }}>在任</span>
                    )}
                  </span>
                  <span className="officials-actions">
                    {m.status === "active" && (m.office_type === "东厂" || m.office_type === "司礼监") ? (
                      <span className="east-bound-label">
                        {m.office_type === "东厂" ? "东厂" : "司礼监"}·固任
                        {m.office_type === "东厂" ? (
                          <select className="transfer-select-mini" value="" onChange={(e) => { const v = e.target.value; if (v) doTransfer(m.name, v); }}>
                            <option value="">↻</option>
                            <option value="司礼监">→司礼监</option>
                          </select>
                        ) : (
                          <select className="transfer-select-mini" value="" onChange={(e) => { const v = e.target.value; if (v) doTransfer(m.name, v); }}>
                            <option value="">↻</option>
                            <option value="东厂">→东厂</option>
                          </select>
                        )}
                      </span>
                    ) : m.status === "active" ? (
                      <>
                        <select
                          className="transfer-select-inline"
                          value=""
                          onChange={(e) => { const v = e.target.value; if (v) doTransfer(m.name, v); }}
                        >
                          <option value="">调往</option>
                          {officeTypes.filter(ot => ot !== "司礼监").map((ot) => (
                            <option key={ot} value={ot}>{ot}</option>
                          ))}
                        </select>
                        <button className="card-action danger officials-action-btn" onClick={() => { if (confirm(`确定免职 ${m.name}？`)) { fetch(`/api/game/dismiss_officer?name=${encodeURIComponent(m.name)}`, { method: "POST" }).then(() => onRefresh()); } }}>免职</button>
                        <button className="card-action warn officials-action-btn" onClick={() => { if (confirm(`确定将 ${m.name} 下狱？`)) { fetch(`/api/game/dismiss?name=${encodeURIComponent(m.name)}`, { method: "POST" }).then(() => onRefresh()); } }}>下狱</button>
                      </>
                    ) : null}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function CourtAssembly({
  state,
  onClose,
  onRefresh,
}: {
  state: GameState;
  onClose: () => void;
  onRefresh: () => void;
}) {
  const [policy, setPolicy] = React.useState("");
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [busy, setBusy] = React.useState(false);
  const [result, setResult] = React.useState<null | {
    policy: string;
    imperial_prestige: number;
    responses: Array<{ name: string; stance: string; reason: string; speech: string; debate_with: string[] }>;
    summary: { total: number; support: number; oppose: number; neutral: number; can_enforce: boolean };
  }>(null);
  const [error, setError] = React.useState("");

  // Eligible: not imprisoned, not dead
  const eligible = state.ministers.filter((m) => m.status !== "imprisoned" && m.status !== "dead");

  const toggleMinister = (name: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  };

  const selectAll = () => {
    setSelected(new Set(eligible.map((m) => m.name)));
  };

  const holdAssembly = async () => {
    if (!policy.trim()) { setError("请输入政策内容"); return; }
    if (selected.size === 0) { setError("请选择参加朝会的大臣"); return; }
    setError(""); setBusy(true); setResult(null);
    try {
      const res = await fetch("/api/game/court_assembly", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          policy_text: policy.trim(),
          selected_ministers: Array.from(selected),
        }),
      });
      const data = await res.json();
      if (!res.ok) { setError(data.detail || "朝会失败"); setBusy(false); return; }
      setResult(data);
      onRefresh();
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : "朝会失败");
    }
    setBusy(false);
  };

  const stanceColor = (s: string) => {
    switch (s) {
      case "支持": return "#2ecc71";
      case "反对": return "#e74c3c";
      case "犹豫": return "#f39c12";
      default: return "#95a5a6";
    }
  };

  if (result) {
    return (
      <div className="center-layer">
        <div className="center-scrim" onClick={onClose} />
        <div className="center-modal court-modal">
          <div className="center-modal-header">
            <h1><Landmark size={18} /> 朝会结果</h1>
            <button className="icon-button" aria-label="关闭" onClick={onClose}><X size={16} /></button>
          </div>
          <div className="court-result-body">
            <div className="court-policy-display">
              <span className="court-label">圣上提出：</span>
              <p>{result.policy}</p>
            </div>
            <div className="court-summary-bar">
              <span className="court-summary-item">皇威：<b>{result.imperial_prestige}</b></span>
              <span className="court-summary-item support">支持：<b>{result.summary.support}</b></span>
              <span className="court-summary-item oppose">反对：<b>{result.summary.oppose}</b></span>
              <span className="court-summary-item neutral">中立：<b>{result.summary.neutral}</b></span>
              <span className={`court-summary-item ${result.summary.can_enforce ? "can-enforce" : "cannot-enforce"}`}>
                {result.summary.can_enforce ? "✅ 可推行" : "❌ 阻力较大"}
              </span>
            </div>
            <div className="court-responses">
              {result.responses.map((r) => (
                <div key={r.name} className={`court-response-card ${r.stance}`}>
                  <div className="court-response-header">
                    <span className="court-minister-name">{r.name}</span>
                    <span className="court-stance-badge" style={{ backgroundColor: stanceColor(r.stance) }}>{r.stance}</span>
                  </div>
                  <div className="court-response-reason">{r.reason}</div>
                  <div className="court-response-speech">"{r.speech}"</div>
                  {r.debate_with && r.debate_with.length > 0 && (
                    <div className="court-debate-info">争论对象：{r.debate_with.join("、")}</div>
                  )}
                </div>
              ))}
            </div>
            <div className="court-actions">
              <button className="primary-action" onClick={() => setResult(null)}>再开一次</button>
              <button className="primary-action" onClick={onClose}>关闭</button>
            </div>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="center-layer">
      <div className="center-scrim" onClick={onClose} />
      <div className="center-modal court-modal">
        <div className="center-modal-header">
          <h1><Landmark size={18} /> 朝会 <span className="unfinished-badge">未完善</span></h1>
          <button className="icon-button" aria-label="关闭" onClick={onClose}><X size={16} /></button>
        </div>
        <div className="court-body">
          <div className="court-policy-input">
            <label className="court-label">你的政策：</label>
            <textarea
              className="court-textarea"
              rows={3}
              value={policy}
              onChange={(e) => setPolicy(e.target.value)}
              placeholder="输入你想在朝会上提出的政策，如：朕欲加征辽饷以充军需..."
            />
          </div>
          <div className="court-eligible">
            <div className="court-eligible-header">
              <span>可选大臣（{eligible.length}）</span>
              <button className="court-select-all" onClick={selectAll}>全选</button>
            </div>
            <div className="court-eligible-list">
              {eligible.map((m) => (
                <label key={m.name} className={`court-eligible-item ${selected.has(m.name) ? "selected" : ""}`}>
                  <input
                    type="checkbox"
                    checked={selected.has(m.name)}
                    onChange={() => toggleMinister(m.name)}
                  />
                  <span className="court-eligible-name">{m.name}</span>
                  <span className="court-eligible-office">{m.current_office || m.office}</span>
                  {!hiddenFactions.has(m.faction) && (
                    <span className="court-eligible-faction" style={{ backgroundColor: factionColor(m.faction) }}>
                      {m.faction}
                    </span>
                  )}
                </label>
              ))}
            </div>
          </div>
          {error && <div className="menu-error">{error}</div>}
          <button className="primary-action" onClick={holdAssembly} disabled={busy} style={{ marginTop: 8, width: "100%" }}>
            {busy ? "朝会进行中..." : "🏛 开启朝会"}
          </button>
        </div>
      </div>
    </div>
  );
}

function TopStatusBar({
  state,
  onOpenState,
  onOpenMenu,
}: {
  state: GameState;
  onOpenState: () => void;
  onOpenMenu: () => void;
}) {
  const scoreKeys = ["民心", "皇威"];
  return (
    <header className="status-bar" aria-label="国势状态栏">
      <button className="status-emblem" onClick={onOpenState}>
        <img src="/icon_ming_emblem.png" alt="大明" className="emblem-art" />
        <span className="emblem-date">
          <span className="era-label">{state.turn.year >= 1628 ? "崇祯" : "天启"}{state.turn.year >= 1628 ? state.turn.year - 1627 : state.turn.year - 1620}年</span>
          <span className="date-text">{state.turn.year} 年 {state.turn.period} 月</span>
        </span>
      </button>
      <div className="status-metrics">
        <BudgetHover accountName="国库" budget={state.budget["国库"]} />
        <BudgetHover accountName="内库" budget={state.budget["内库"]} />
        {scoreKeys.map((key) => (
          <span className={`status-pill ${scoreTone(state.metrics[key], false)}`} key={key}>
            {key} <b>{state.metrics[key]}</b>
          </span>
        ))}
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
      </span>
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
  onOpenMemorials,
  onOpenEdict,
  onOpenExtraction,
  onOpenHistory,
  onEndGame,
}: {
  eventsCount: number;
  directivesCount: number;
  onOpenMemorials: () => void;
  onOpenEdict: () => void;
  onOpenExtraction: () => void;
  onOpenHistory: () => void;
  onEndGame: () => void;
}) {
  return (
    <nav className="bottom-command-bar" aria-label="朝政主操作">
      <button className="command-icon" onClick={onEndGame} aria-label="结束游戏">
        <img src="/icon_seal.png" alt="" className="command-art" />
        <span className="command-caption"><b>结束游戏</b><small>自评史鉴</small></span>
      </button>
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
  React.useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

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

function GameMenuModal({ onClose, onAfterLoad }: { onClose: () => void; onAfterLoad: () => void }) {
  const [tab, setTab] = React.useState<"save" | "load" | "llm" | "cheat">("save");
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
            <button className="game-menu-restart" onClick={async () => {
              if (!confirm("重新开始将重置当前游戏到开局状态，不影响已保存的存档。确定？")) return;
              try {
                await api("/api/game/restart", { method: "POST" });
                onAfterLoad();
              } catch (e) {
                alert("重新开始失败：" + (e instanceof Error ? e.message : String(e)));
              }
            }}>
              <RotateCcw size={14} /> 重新开始
            </button>
            <button className={tab === "cheat" ? "active" : ""} onClick={() => setTab("cheat")}>
              <Coins size={14} /> 作弊
            </button>
          </nav>
          <div className="game-menu-body">
            {tab === "save" ? <SaveTab /> : null}
            {tab === "load" ? <LoadTab onAfterLoad={onAfterLoad} /> : null}
            {tab === "llm" ? <LLMConfigTab /> : null}
            {tab === "cheat" ? <CheatTab onAfterLoad={onAfterLoad} /> : null}
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
      setErr(e instanceof Error ? e.message : String(e));
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
  const [apiKey, setApiKey] = React.useState("");
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
        body: JSON.stringify({ base_url: baseUrl, model, api_key: apiKey }),
      });
      setInfo((cur) => (cur ? { ...cur, ...data } : null));
      setApiKey("");
      setMsg("已生效并写入 data/runtime_llm.json。");
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
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

function CheatTab({ onAfterLoad }: { onAfterLoad: () => void }) {
  const [msg, setMsg] = React.useState("");
  const [err, setErr] = React.useState("");
  const addMoney = async (amount: number) => {
    setMsg("");
    setErr("");
    try {
      await api("/api/game/cheat/add_treasury", {
        method: "POST",
        body: JSON.stringify({ amount }),
      });
      setMsg(`已增加国库 ${amount} 万两`);
      setTimeout(() => onAfterLoad(), 1000);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  };
  return (
    <section className="menu-section">
      <h3>作弊指令</h3>
      <p className="menu-hint">增加国库银两，用于测试或挽救危局。</p>
      <div className="menu-row" style={{ gap: 10, marginTop: 10 }}>
        <button className="menu-btn primary" onClick={() => addMoney(100)}>
          <Coins size={14} /> 增加国库一百万两
        </button>
        <button className="menu-btn primary" onClick={() => addMoney(1000)}>
          <Coins size={14} /> 增加国库一千万两
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
  const out = data.extractor_output;
  if (!out || typeof out !== "object") {
    return <div className="document-section"><pre className="memorial-text">{String(out ?? "")}</pre></div>;
  }
  return (
    <div className="document-section extraction-view">
      <ExtractionSection title="国势变化（metric_delta）">
        <MetricDeltaBlock data={out.metric_delta} />
      </ExtractionSection>
      <ExtractionSection title="钱粮收支（economy_moves）">
        <EconomyBlock data={out.economy_moves} />
      </ExtractionSection>
      <ExtractionSection title="派系变化（faction_delta）">
        <FactionBlock data={out.faction_delta} />
      </ExtractionSection>
      <ExtractionSection title="局势推进（issue_advances）">
        <IssueAdvancesBlock data={out.issue_advances} />
      </ExtractionSection>
      <ExtractionSection title="新立局势（new_issues）">
        <NewIssuesBlock data={out.new_issues} />
      </ExtractionSection>
      <ExtractionSection title="结案 / 失败（close_issues）">
        <CloseIssuesBlock data={out.close_issues} />
      </ExtractionSection>
      <ExtractionSection title="撤旨（cancels）">
        <CancelsBlock data={out.cancels} />
      </ExtractionSection>
      <ExtractionSection title="地区变化（region_delta）">
        <GenericKVBlock data={out.region_delta} />
      </ExtractionSection>
      <ExtractionSection title="军队变化（army_delta）">
        <GenericKVBlock data={out.army_delta} />
      </ExtractionSection>
      <ExtractionSection title="外部势力（external_power_updates）">
        <GenericKVBlock data={out.external_power_updates} />
      </ExtractionSection>
      <ExtractionSection title="财政系数（fiscal_changes）">
        <FiscalBlock data={out.fiscal_changes} />
      </ExtractionSection>
      <ExtractionSection title="世界推进（world_advance）">
        <GenericKVBlock data={out.world_advance} />
      </ExtractionSection>
    </div>
  );
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
          <b className={Number(item?.delta) >= 0 ? "good" : "bad"}>{item?.account || "?"} {fmtDelta(item?.delta)} 万</b>
          <span>{item?.category || ""}{item?.reason ? ` — ${item.reason}` : ""}</span>
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
          <b className={Number(it?.delta_bar) >= 0 ? "good" : "bad"}>#{it?.issue_id} bar {fmtDelta(it?.delta_bar)}{it?.inertia_delta ? `，惯性 ${fmtDelta(it.inertia_delta)}` : ""}</b>
          {it?.stage_text ? <span>{it.stage_text}</span> : null}
          {it?.narrative ? <span className="extraction-narr">{it.narrative}</span> : null}
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
          <b>{it?.title || it?.id || "新事项"}（{it?.kind || it?.origin_kind || ""}）</b>
          {it?.stage_text ? <span>{it.stage_text}</span> : null}
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
          <b className={it?.reason === "resolved" ? "good" : "bad"}>#{it?.issue_id} {it?.reason === "resolved" ? "结案" : "失败"}</b>
          {it?.narrative ? <span>{it.narrative}</span> : null}
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
          <b>#{it?.issue_id} 撤旨</b>
          {it?.narrative ? <span>{it.narrative}</span> : null}
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
          <b className={Number(it?.delta) >= 0 ? "good" : "bad"}>{it?.key} {fmtDelta(it?.delta)}</b>
          {it?.reason ? <span>{it.reason}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function GenericKVBlock({ data }: { data: any }) {
  if (isEmptyData(data)) return <p className="extraction-empty">无</p>;
  return <pre className="extraction-json">{JSON.stringify(data, null, 2)}</pre>;
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
  chat,
  suggestions,
  pendingUserMessage,
  streamingMinisterMessage,
  chatNotice,
  composerHint,
  input,
  busy,
  error,
  onInput,
  onSend,
  onHint,
  onFavorite,
  onOpenEdict,
  onClose,
}: {
  minister: Minister;
  chat: ChatMessage[];
  suggestions: Suggestion[];
  pendingUserMessage: string;
  streamingMinisterMessage: string;
  chatNotice: string;
  composerHint: string;
  input: string;
  busy: string;
  error: string;
  onInput: (value: string) => void;
  onSend: (text?: string) => void;
  onHint: (value: string) => void;
  onFavorite: () => void;
  onOpenEdict: () => void;
  onClose: () => void;
}) {
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
    onSend(suggestion.text);
  };

  return (
    <div className="chat-full-grid">
      <aside className="modal-pane minister-side">
        <div className="minister-profile">
          <div>
            <h2>{minister.name}</h2>
            <p>{minister.current_office || minister.office}</p>
          </div>
          <button className="icon-button" aria-label="收藏大臣" onClick={onFavorite}>
            <Star size={16} fill={minister.favorite ? "currentColor" : "none"} />
          </button>
        </div>
        <span className="minister-tags">
          {!hiddenFactions.has(minister.faction) && (
            <span className="faction-tag" style={{ backgroundColor: factionColor(minister.faction) }}>
              {minister.faction}
            </span>
          )}
          {minister.status !== "active" && (
            <span className={`status-tag ${statusBadge(minister.status, minister.status_label)}`}>
              {minister.status_label}
            </span>
          )}
        </span>
        <p className="profile-copy">{minister.summary}</p>
        <div className="minister-stat-grid">
          <StatBar label="忠诚" value={minister.loyalty} />
          <StatBar label="能力" value={minister.ability} />
          <StatBar label="清廉" value={minister.integrity} />
          <StatBar label="勇气" value={minister.courage} />
        </div>
        <div className="minister-life">
          {minister.birth_year > 0 && (
            <small>生于{minister.birth_year}年</small>
          )}
          {minister.death_info && (
            <small className="death-note">{minister.death_info}</small>
          )}
          {minister.imprison_info && (
            <small className="imprison-note">下狱：{minister.imprison_info}</small>
          )}
        </div>
        <div className="skill-strip" aria-label="可用技能">
          {minister.skills.slice(0, 8).map((skill) => (
            <span key={skill.id} title={`${skill.description} 来源：${skill.sources.join("/")}`}>
              {skill.name}
            </span>
          ))}
        </div>
        <button className="secondary-action" onClick={onOpenEdict}>
          <ScrollText size={15} />
          转入诏书草案
        </button>
      </aside>

      <section className="modal-pane chat-main">
        <div className="chat-log" ref={chatLogRef}>
          {displayMessages.map((message, index) => (
            <div className={`chat-message ${message.role} ${message.pending ? "pending" : ""}`} key={`${message.role}-${index}-${safeStr(message.content)}`}>
              <span>{message.role === "user" ? "朕" : minister.name}</span>
              <p>{safeStr(message.content)}</p>
            </div>
          ))}
          {busy && !streamingMinisterMessage && (
            <div className="chat-message minister thinking">
              <span>{minister.name}</span>
              <p><Loader2 size={14} />大臣思索中...</p>
            </div>
          )}
          {chatNotice && <div className="chat-system-note">{safeStr(chatNotice)}</div>}
          {error && <div className="chat-system-note danger" role="alert">{safeStr(error)}</div>}
        </div>
        <div className="chat-composer">
          <div className="hitl-bar">
            {suggestions.map((suggestion) => (
              <button key={`${safeStr(suggestion.label)}-${safeStr(suggestion.text)}`} onClick={() => sendSuggestion(suggestion)} disabled={!!busy} title={safeStr(suggestion.text)}>
                {shortSuggestionLabel(suggestion)}
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

function filterMinisters(ministers: Minister[], group: string) {
  if (group === "内阁") return ministers.filter((m) => m.office_type === "内阁" && m.status === "active");
  if (group === "六部") return ministers.filter((m) => ["吏部", "户部", "礼部", "兵部", "刑部", "工部"].includes(m.office_type) && m.status === "active");
  if (group === "收藏") return ministers.filter((m) => m.favorite);
  return ministers; // 全部 → everyone
}

const statusGroups = [
  { key: "active", label: "在任" },
  { key: "imprisoned", label: "下狱" },
  { key: "exiled", label: "流放" },
  { key: "dismissed", label: "罢黜" },
  { key: "retired", label: "致仕" },
  { key: "dead", label: "已故" },
];

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
      if (target && target.closest('button, a, input, textarea, select, [role="dialog"], .court-drawer, .map-intel-panel, .modal-scroll, .fullscreen-modal, .situation-panel, .chat-main, .center-layer, .center-modal, .officials-body, .minister-list')) return;
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
                    <span>{node.label}</span>
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
            {node.armies.map((army) => (
              <tr key={army.id}>
                <td>{army.name}</td>
                <td>{army.troop_type}</td>
                <td>{army.manpower}</td>
                <td>{monthlyAmount(army.maintenance_per_turn)}</td>
                <td>{army.morale}</td>
                <td>{army.arrears}</td>
              </tr>
            ))}
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

createRoot(document.getElementById("root")!).render(<App />);
