import { Card, Col, Row, Statistic, Steps, Tag, Button } from "antd";
import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { WhatsAppOutlined, MessageOutlined } from "@ant-design/icons";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";

type Account = {
  plan: string;
  subscription_status: string;
  usage: { messages_today: number; tokens_30d: number };
};

export default function Dashboard() {
  const { me } = useAuth();
  const nav = useNavigate();
  const [acc, setAcc] = useState<Account | null>(null);
  const [waStatus, setWaStatus] = useState<string>("pending");
  const [google, setGoogle] = useState(false);

  useEffect(() => {
    api.get("/api/account").then((r) => setAcc(r.data)).catch(() => {});
    api.get("/api/whatsapp/connection").then((r) => setWaStatus(r.data.status)).catch(() => {});
    api.get("/api/credentials").then((r) => setGoogle((r.data.services || []).length > 0)).catch(() => {});
  }, []);

  const connected = waStatus === "connected";
  const step = connected ? (google ? 3 : 2) : 1;

  return (
    <div className="rise">
      <h1 className="mai-display" style={{ fontSize: 30, marginBottom: 4 }}>
        Good to see you{me?.display_name ? `, ${me.display_name}` : ""}.
      </h1>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        Here's how your Mai is doing.
      </p>

      <Row gutter={[16, 16]} style={{ marginTop: 12 }}>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic
              title="Plan"
              value={acc?.plan || "—"}
              suffix={
                acc?.subscription_status ? (
                  <Tag color={acc.subscription_status === "active" ? "green" : "cyan"}
                       style={{ marginInlineStart: 8, verticalAlign: "middle" }}>
                    {acc.subscription_status}
                  </Tag>
                ) : null
              }
            />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="Messages today" value={acc?.usage.messages_today ?? 0} prefix={<MessageOutlined />} />
          </Card>
        </Col>
        <Col xs={24} sm={8}>
          <Card>
            <Statistic title="Tokens (30d)" value={acc?.usage.tokens_30d ?? 0} />
          </Card>
        </Col>
      </Row>

      <Card style={{ marginTop: 16 }} title="Get set up">
        <Steps
          direction="vertical"
          current={step}
          items={[
            { title: "Account created", description: "You're in." },
            {
              title: "Connect WhatsApp",
              description: connected ? (
                <Tag color="green" icon={<WhatsAppOutlined />}>Connected</Tag>
              ) : (
                <Button size="small" type="primary" onClick={() => nav("/connect")}>
                  Scan QR to connect
                </Button>
              ),
            },
            {
              title: "Connect a service (optional)",
              description: google ? (
                <Tag color="green">Connected</Tag>
              ) : (
                <Button size="small" onClick={() => nav("/integrations")}>Add credentials</Button>
              ),
            },
            { title: "Chat with Mai", description: <Button size="small" onClick={() => nav("/chat")}>Open chat</Button> },
          ]}
        />
      </Card>
    </div>
  );
}
