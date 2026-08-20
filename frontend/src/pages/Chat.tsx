import { Button, Input, App as AntApp } from "antd";
import { SendOutlined, WhatsAppOutlined, ClockCircleOutlined, GlobalOutlined } from "@ant-design/icons";
import { useRef, useState } from "react";
import { api, apiError } from "../api/client";
import { Brand } from "../components/Brand";

// Session-only: no id or persistence. History is held in React state and
// passed with each request so the agent has context. A page refresh clears it.
type Msg = { role: "user" | "assistant"; source?: string; text: string };

export default function Chat() {
  const { message } = AntApp.useApp();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  async function send() {
    const text = input.trim();
    if (!text || busy) return;
    setInput("");
    // Capture history *before* adding the new user turn so the backend sees
    // the prior conversation (not the message being sent right now).
    const history = msgs.slice(-12).map(m => ({ role: m.role, text: m.text }));
    setMsgs((m) => [...m, { role: "user", source: "web", text }]);
    setBusy(true);
    setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 30);
    try {
      const { data } = await api.post("/api/chat", { message: text, history });
      setMsgs((m) => [...m, { role: "assistant", source: "web", text: data.reply }]);
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setBusy(false);
      setTimeout(() => endRef.current?.scrollIntoView({ behavior: "smooth" }), 50);
    }
  }

  function sourceTag(s?: string) {
    const map: Record<string, { icon: JSX.Element; label: string; color: string }> = {
      web: { icon: <GlobalOutlined />, label: "Web", color: "var(--mai-muted)" },
      whatsapp: { icon: <WhatsAppOutlined />, label: "WhatsApp", color: "#5effb0" },
      schedule: { icon: <ClockCircleOutlined />, label: "Scheduled", color: "#7fd4ff" },
    };
    const m = map[s || "web"] || map.web;
    return (
      <span style={{ fontSize: 10, color: m.color, display: "inline-flex", alignItems: "center", gap: 4 }}>
        {m.icon}
        {m.label}
      </span>
    );
  }

  return (
    <div className="rise mai-chat-container" style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 130px)" }}>
      <h1 className="mai-display" style={{ fontSize: 26, marginBottom: 12 }}>Chat</h1>
      <div
        className="chat-scroll"
        style={{
          flex: 1, overflowY: "auto", padding: 18,
          border: "1px solid var(--mai-line)", borderRadius: 16,
          background: "var(--mai-bg-soft)", display: "flex", flexDirection: "column", gap: 12,
        }}
      >
        {msgs.length === 0 && (
          <div style={{ margin: "auto", color: "var(--mai-muted)", textAlign: "center" }}>
            <Brand size={22} withText={false} />
            <p style={{ marginTop: 10 }}>Say hi to Mai — this conversation is kept only for this session (a refresh starts fresh).</p>
          </div>
        )}
        {msgs.map((m, i) => (
          <div key={`local-${i}`}
            style={{ display: "flex", justifyContent: m.role === "user" ? "flex-end" : "flex-start", gap: 8, alignItems: "flex-end" }}>
            {m.role === "assistant" && <Brand size={18} withText={false} />}
            <div style={{ maxWidth: "78%", display: "flex", flexDirection: "column",
              alignItems: m.role === "user" ? "flex-end" : "flex-start" }}>
              <div style={{ marginBottom: 4 }}>{sourceTag(m.source)}</div>
              <div className={`bubble ${m.role === "user" ? "user" : "mai"}`}>{m.text}</div>
            </div>
          </div>
        ))}
        <div ref={endRef} />
      </div>
      <div className="mai-chat-input-row" style={{ display: "flex", gap: 10, marginTop: 14 }}>
        <Input.TextArea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onPressEnter={(e) => { if (!e.shiftKey) { e.preventDefault(); send(); } }}
          placeholder="Message Mai…  (Enter to send)"
          autoSize={{ minRows: 1, maxRows: 4 }}
          size="large"
        />
        <Button type="primary" size="large" icon={<SendOutlined />} loading={busy} onClick={send}>
          <span className="mai-btn-label">Send</span>
        </Button>
      </div>
    </div>
  );
}
