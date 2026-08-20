import { App as AntApp, Card, Col, Descriptions, Row, Statistic, Table, Tag } from "antd";
import type { ColumnsType } from "antd/es/table";
import { useEffect, useState } from "react";
import { Navigate } from "react-router-dom";
import { api, apiError } from "../api/client";
import { Brand } from "../components/Brand";
import { useAuth } from "../auth/AuthContext";

type Customer = {
  account_id: number;
  email: string | null;
  name: string | null;
  whatsapp_number: string | null;
  timezone: string | null;
  plan: string;
  status: string | null;
  joined: string | null;
  messages_sent: number;
  replies: number;
  schedules: number;
  schedules_active: number;
  skills: number;
  memory_depth: number;
  tokens_30d: number;
  last_conversation: string | null;
};

type Summary = {
  total_customers: number;
  active_7d: number;
  total_messages_sent: number;
  total_schedules: number;
  total_skills: number;
};

const statusColor: Record<string, string> = {
  active: "green",
  trial: "cyan",
  past_due: "orange",
  canceled: "red",
};

function relativeTime(iso: string | null): string {
  if (!iso) return "never";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "never";
  const secs = Math.floor((Date.now() - then) / 1000);
  if (secs < 60) return "just now";
  const mins = Math.floor(secs / 60);
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  if (days < 30) return `${days}d ago`;
  const months = Math.floor(days / 30);
  if (months < 12) return `${months}mo ago`;
  return `${Math.floor(months / 12)}y ago`;
}

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, "")}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}k`;
  return String(n);
}

function fmtDate(iso: string | null): string {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "—";
  return d.toLocaleDateString();
}

function fmtDateTime(iso: string | null): string {
  if (!iso) return "never";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "never";
  return d.toLocaleString();
}

export default function CRM() {
  const { message } = AntApp.useApp();
  const { me } = useAuth();
  const [rows, setRows] = useState<Customer[]>([]);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [loading, setLoading] = useState(true);

  async function load() {
    try {
      const { data } = await api.get("/api/crm/customers");
      setRows(data.customers);
      setSummary(data.summary);
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => {
    load();
  }, []);

  const columns: ColumnsType<Customer> = [
    {
      title: "Customer",
      dataIndex: "name",
      key: "name",
      render: (_: unknown, r: Customer) => (
        <div style={{ minWidth: 0 }}>
          <div style={{ fontWeight: 600 }}>{r.name || "—"}</div>
          {r.email && (
            <div style={{ color: "var(--mai-muted)", fontSize: 12 }}>{r.email}</div>
          )}
        </div>
      ),
    },
    {
      title: "WhatsApp",
      dataIndex: "whatsapp_number",
      key: "whatsapp_number",
      render: (v: string | null) => v || <span style={{ color: "var(--mai-muted)" }}>—</span>,
    },
    {
      title: "Plan",
      key: "plan",
      render: (_: unknown, r: Customer) => (
        <Tag color={statusColor[r.status || ""] || "default"} style={{ borderRadius: 12 }}>
          {r.plan}
          {r.status ? ` · ${r.status}` : ""}
        </Tag>
      ),
    },
    {
      title: "Messages",
      dataIndex: "messages_sent",
      key: "messages_sent",
      align: "right",
      sorter: (a: Customer, b: Customer) => a.messages_sent - b.messages_sent,
    },
    {
      title: "Schedules",
      key: "schedules",
      align: "right",
      render: (_: unknown, r: Customer) => `${r.schedules_active}/${r.schedules}`,
    },
    {
      title: "Skills",
      dataIndex: "skills",
      key: "skills",
      align: "right",
    },
    {
      title: "Memory",
      key: "memory",
      render: (_: unknown, r: Customer) =>
        r.memory_depth > 0 ? (
          <Tag color="purple">🧠 {r.memory_depth}</Tag>
        ) : (
          <span style={{ color: "var(--mai-muted)" }}>—</span>
        ),
    },
    {
      title: "Last seen",
      key: "last_conversation",
      render: (_: unknown, r: Customer) => (
        <span style={{ color: r.last_conversation ? undefined : "var(--mai-muted)" }}>
          {relativeTime(r.last_conversation)}
        </span>
      ),
    },
    {
      title: "Joined",
      key: "joined",
      render: (_: unknown, r: Customer) => fmtDate(r.joined),
    },
  ];

  // Standalone page guard: admin only (API is already 403-gated).
  if (me && !me.is_admin) {
    return <Navigate to="/" replace />;
  }

  return (
    <div style={{ minHeight: "100vh", background: "var(--mai-bg)" }}>
      <header
        style={{
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 14,
          height: 64,
          paddingInline: 24,
          borderBottom: "1px solid var(--mai-line)",
        }}
      >
        <Brand size={24} />
        <span className="mai-mono" style={{ color: "var(--mai-muted)", fontSize: 13 }}>
          {me?.email}
        </span>
      </header>

      <div
        className="rise"
        style={{ maxWidth: 1320, width: "100%", margin: "0 auto", padding: 28 }}
      >
        <h1 className="mai-display" style={{ fontSize: 26 }}>CRM</h1>
        <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
          Every customer on the platform and how they're using Mai.
        </p>

        <Row gutter={[12, 12]} style={{ marginBottom: 16 }}>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Total customers" value={summary?.total_customers ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Active (7d)" value={summary?.active_7d ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Messages received" value={summary?.total_messages_sent ?? 0} />
          </Card>
        </Col>
        <Col xs={12} md={6}>
          <Card size="small">
            <Statistic title="Schedules" value={summary?.total_schedules ?? 0} />
          </Card>
        </Col>
      </Row>

      <Card size="small">
        <div className="mai-table-wrap">
        <Table<Customer>
          rowKey="account_id"
          loading={loading}
          dataSource={rows}
          columns={columns}
          size="middle"
          pagination={{ pageSize: 20, hideOnSinglePage: true }}
          scroll={{ x: "max-content" }}
          expandable={{
            expandedRowRender: (r: Customer) => (
              <Descriptions
                size="small"
                column={{ xs: 1, sm: 2, md: 3 }}
                bordered
                items={[
                  { key: "email", label: "Email", children: r.email || "—" },
                  { key: "wa", label: "WhatsApp", children: r.whatsapp_number || "—" },
                  { key: "tz", label: "Timezone", children: r.timezone || "—" },
                  {
                    key: "plan",
                    label: "Plan / status",
                    children: `${r.plan}${r.status ? ` · ${r.status}` : ""}`,
                  },
                  { key: "joined", label: "Joined", children: fmtDate(r.joined) },
                  { key: "sent", label: "Messages sent", children: r.messages_sent },
                  { key: "replies", label: "Replies", children: r.replies },
                  {
                    key: "sched",
                    label: "Schedules (active/total)",
                    children: `${r.schedules_active}/${r.schedules}`,
                  },
                  { key: "skills", label: "Skills", children: r.skills },
                  { key: "mem", label: "Memory depth", children: r.memory_depth || 0 },
                  { key: "tok", label: "Tokens (30d)", children: fmtNum(r.tokens_30d) },
                  {
                    key: "last",
                    label: "Last conversation",
                    children: fmtDateTime(r.last_conversation),
                  },
                ]}
              />
            ),
          }}
        />
        </div>
      </Card>
      </div>
    </div>
  );
}
