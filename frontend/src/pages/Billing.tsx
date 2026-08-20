import { Button, Card, Tag, App as AntApp } from "antd";
import { useState } from "react";
import { api, apiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

export default function Billing() {
  const { me } = useAuth();
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState("");

  async function go(path: string, key: string) {
    setLoading(key);
    try {
      const { data } = await api.post(path);
      const url = data.checkout_url || data.portal_url;
      if (url) window.location.href = url;
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setLoading("");
    }
  }

  const active = me?.subscription_status === "active";

  return (
    <div className="rise" style={{ maxWidth: 560, marginInline: "auto" }}>
      <h1 className="mai-display" style={{ fontSize: 26 }}>Billing</h1>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>Powered by Polar.</p>
      <Card>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 18 }}>
          <div>
            <div style={{ fontSize: 18, fontWeight: 600 }}>{me?.plan} plan</div>
            <Tag color={active ? "green" : "cyan"} style={{ marginTop: 6 }}>{me?.subscription_status}</Tag>
          </div>
        </div>
        {active ? (
          <Button block size="large" loading={loading === "portal"} onClick={() => go("/api/billing/portal", "portal")}>
            Manage subscription
          </Button>
        ) : (
          <Button block type="primary" size="large" loading={loading === "checkout"} onClick={() => go("/api/billing/checkout", "checkout")}>
            Subscribe
          </Button>
        )}
        <p style={{ color: "var(--mai-muted)", fontSize: 12, marginTop: 14, marginBottom: 0 }}>
          Without an active subscription Mai pauses and your schedules stop firing.
        </p>
      </Card>
    </div>
  );
}
