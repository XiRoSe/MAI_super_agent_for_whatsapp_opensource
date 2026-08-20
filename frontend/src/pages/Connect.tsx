import { Button, Card, Tag, App as AntApp, Spin, Result, Alert, Progress } from "antd";
import { WhatsAppOutlined, ReloadOutlined } from "@ant-design/icons";
import { useEffect, useRef, useState } from "react";
import { api, apiError } from "../api/client";

type Conn = { session_id: string; status: string; phone_number?: string | null };

export default function Connect() {
  const { message } = AntApp.useApp();
  const [status, setStatus] = useState<string>("pending");
  const [phone, setPhone] = useState<string | null>(null);
  const [qr, setQr] = useState<string | null>(null);
  const [secondsLeft, setSecondsLeft] = useState(0);
  const [duration, setDuration] = useState(30);
  const [linking, setLinking] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const statusPoll = useRef<number | null>(null);
  const countdown = useRef<number | null>(null);
  const connected = status === "connected";

  function stopTimers() {
    if (statusPoll.current) window.clearInterval(statusPoll.current);
    if (countdown.current) window.clearInterval(countdown.current);
    statusPoll.current = null;
    countdown.current = null;
  }

  async function checkStatus() {
    try {
      const { data } = await api.get<Conn>("/api/whatsapp/connection");
      setStatus(data.status);
      setPhone(data.phone_number || null);
      if (data.status === "connected") {
        setQr(null);
        stopTimers();
      }
    } catch {
      /* transient — keep polling */
    }
  }

  // Fetch a fresh QR (also called automatically when the current one expires).
  async function refreshQr(manual = false) {
    setLinking(true);
    setError(null);
    try {
      const { data } = await api.post("/api/whatsapp/connect");
      if (data.status === "error") {
        setError(data.error || "Couldn't start the WhatsApp connection.");
        setQr(null);
        return;
      }
      if (data.status === "connected") {
        setStatus("connected");
        setPhone(data.phone_number || null);
        setQr(null);
        stopTimers();
        return;
      }
      setQr(data.qr || null);
      const dur = data.qr_duration || 30;
      setDuration(dur);
      setSecondsLeft(dur);
      setStatus("connecting");
    } catch (e) {
      setError(apiError(e));
    } finally {
      setLinking(false);
    }
  }

  // Start linking: fetch QR, then poll status (3s) and run a 1s countdown that
  // auto-refreshes the QR when it expires — until the device reports connected.
  async function startLinking() {
    await refreshQr(true);
    stopTimers();
    statusPoll.current = window.setInterval(checkStatus, 3000);
    countdown.current = window.setInterval(() => {
      setSecondsLeft((s) => {
        if (s <= 1) {
          // expired -> pull a fresh QR (unless already connected)
          refreshQr();
          return duration;
        }
        return s - 1;
      });
    }, 1000);
  }

  useEffect(() => {
    checkStatus();
    return stopTimers;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function doDisconnect() {
    try {
      await api.post("/api/whatsapp/disconnect");
      stopTimers();
      setStatus("disconnected");
      setQr(null);
      setPhone(null);
    } catch (e) {
      message.error(apiError(e));
    }
  }

  return (
    <div className="rise" style={{ maxWidth: 640, marginInline: "auto" }}>
      <h1 className="mai-display" style={{ fontSize: 26, marginBottom: 4 }}>WhatsApp</h1>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        Link your own WhatsApp number. Mai lives on that number — no Meta account, no fees.
      </p>

      <Card>
        {error && <Alert type="error" showIcon message={error} style={{ marginBottom: 16 }}
          action={<Button size="small" onClick={() => startLinking()}>Retry</Button>} />}

        {connected ? (
          <Result
            status="success"
            icon={<WhatsAppOutlined style={{ color: "var(--mai-accent)" }} />}
            title="WhatsApp connected"
            subTitle={phone ? `Linked number: ${phone}. Mai replies here now.` : "Your number is linked. Mai replies here now."}
            extra={<Button danger onClick={doDisconnect}>Disconnect</Button>}
          />
        ) : qr ? (
          <div style={{ textAlign: "center" }}>
            <p style={{ color: "var(--mai-muted)" }}>
              On your phone: WhatsApp → <b>Linked devices</b> → <b>Link a device</b>, then scan:
            </p>
            <div className="mai-glow" style={{ display: "inline-block", padding: 16, borderRadius: 16, background: "#fff" }}>
              <img src={qr} alt="WhatsApp QR" width={248} height={248} style={{ display: "block" }} />
            </div>
            <div style={{ marginTop: 16, maxWidth: 260, marginInline: "auto" }}>
              <Progress
                percent={Math.round((secondsLeft / duration) * 100)}
                showInfo={false}
                strokeColor="var(--mai-accent)"
                trailColor="var(--mai-line)"
              />
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginTop: 8 }}>
                <span style={{ color: "var(--mai-muted)", fontSize: 13 }}>
                  <Spin size="small" style={{ marginRight: 8 }} />
                  Waiting for scan… {linking ? "(refreshing)" : `QR refreshes in ${secondsLeft}s`}
                </span>
                <Button size="small" icon={<ReloadOutlined />} loading={linking} onClick={() => refreshQr(true)}>
                  New QR
                </Button>
              </div>
            </div>
          </div>
        ) : (
          <div style={{ textAlign: "center", padding: "20px 0" }}>
            <Tag color="default" style={{ marginBottom: 16 }}>{status}</Tag>
            <div>
              <Button type="primary" size="large" icon={<WhatsAppOutlined />} loading={linking} onClick={startLinking}>
                Connect WhatsApp
              </Button>
            </div>
          </div>
        )}
      </Card>
    </div>
  );
}
