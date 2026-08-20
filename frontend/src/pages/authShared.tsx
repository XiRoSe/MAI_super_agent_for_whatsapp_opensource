import { Brand } from "../components/Brand";
import { Segmented } from "antd";
import { useNavigate } from "react-router-dom";

export function MobileAuthHero() {
  return (
    <div className="mai-auth-hero rise">
      <Brand size={22} />
      <p className="mai-auth-hero-headline mai-display">
        Your assistant, <span className="mai-wordmark">on WhatsApp.</span>
      </p>
      <p className="mai-auth-hero-sub">
        Answers, schedules, and actions — on your own number.
      </p>
    </div>
  );
}

const features = [
  "Remembers you across every conversation",
  "Schedules things in plain language — “every weekday at 8am”",
  "Reads your calendar & email, drafts replies you approve",
  "Lives on your own WhatsApp number",
];

export function AuthModeToggle({ mode }: { mode: "login" | "register" }) {
  const navigate = useNavigate();
  return (
    <div className="mai-auth-toggle">
      <Segmented
        block
        options={[
          { label: "Sign in", value: "login" },
          { label: "Create account", value: "register" },
        ]}
        value={mode}
        onChange={(val) => {
          const v = val as "login" | "register";
          if (v !== mode) navigate(`/${v}`);
        }}
      />
    </div>
  );
}

export function RegisterStepHint() {
  return (
    <div className="mai-auth-step">
      <span className="mai-auth-step-dot">→</span> Step 1 of 2 · next, connect WhatsApp
    </div>
  );
}

export function AuthAside() {
  return (
    <div
      className="rise mai-auth-aside"
      style={{
        position: "relative",
        padding: "56px 56px",
        display: "flex",
        flexDirection: "column",
        justifyContent: "center",
        borderRight: "1px solid var(--mai-line)",
      }}
    >
      <div style={{ marginBottom: 40 }}>
        <Brand size={28} />
      </div>
      <h2
        className="mai-display"
        style={{ fontSize: 46, lineHeight: 1.05, margin: 0, maxWidth: 460 }}
      >
        Your assistant,
        <br />
        <span className="mai-wordmark">on WhatsApp.</span>
      </h2>
      <p style={{ color: "var(--mai-muted)", maxWidth: 420, marginTop: 18, fontSize: 15 }}>
        Mai answers conversationally, learns what matters to you, takes real actions,
        and messages you on schedule — so it feels like an assistant, not a bot.
      </p>
      <ul style={{ listStyle: "none", padding: 0, marginTop: 28, display: "grid", gap: 14 }}>
        {features.map((f) => (
          <li key={f} style={{ display: "flex", gap: 12, alignItems: "flex-start", color: "var(--mai-text)" }}>
            <span style={{ color: "var(--mai-accent)", marginTop: 2 }}>✦</span>
            <span style={{ fontSize: 14.5 }}>{f}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
