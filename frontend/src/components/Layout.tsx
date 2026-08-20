import { Layout, Menu, Avatar, Dropdown, Tag, Drawer, Button } from "antd";
import {
  AppstoreOutlined,
  MessageOutlined,
  WhatsAppOutlined,
  ClockCircleOutlined,
  ApiOutlined,
  CreditCardOutlined,
  SettingOutlined,
  LogoutOutlined,
  BulbOutlined,
  HeartOutlined,
  MenuOutlined,
  SmileOutlined,
  TeamOutlined,
} from "@ant-design/icons";
import { useLocation, useNavigate } from "react-router-dom";
import { useState, type ReactNode } from "react";
import { Brand } from "./Brand";
import { useAuth } from "../auth/AuthContext";

const { Sider, Content, Header } = Layout;

const items = [
  { key: "/", icon: <AppstoreOutlined />, label: "Overview" },
  { key: "/chat", icon: <MessageOutlined />, label: "Chat" },
  { key: "/connect", icon: <WhatsAppOutlined />, label: "WhatsApp" },
  { key: "/schedules", icon: <ClockCircleOutlined />, label: "Schedules" },
  { key: "/skills", icon: <BulbOutlined />, label: "Skills" },
  { key: "/memory", icon: <HeartOutlined />, label: "Memory" },
  { key: "/personality", icon: <SmileOutlined />, label: "Personality" },
  { key: "/integrations", icon: <ApiOutlined />, label: "Integrations" },
  { key: "/billing", icon: <CreditCardOutlined />, label: "Billing" },
  { key: "/settings", icon: <SettingOutlined />, label: "Settings" },
];

const statusColor: Record<string, string> = {
  active: "green",
  trial: "cyan",
  past_due: "orange",
  canceled: "red",
};

export function Shell({ children }: { children: ReactNode }) {
  const nav = useNavigate();
  const loc = useLocation();
  const { me, logout } = useAuth();
  const [drawerOpen, setDrawerOpen] = useState(false);
  const selectedKeys = [loc.pathname === "/" ? "/" : "/" + loc.pathname.split("/")[1]];

  // Visibility only — the API is the real gate (403 for non-admins).
  const navItems = me?.is_admin
    ? [...items, { key: "/crm", icon: <TeamOutlined />, label: "CRM" }]
    : items;

  // CRM is a standalone page: open it in a new tab instead of in-app nav.
  const handleNav = (key: string) => {
    if (key === "/crm") window.open("/crm", "_blank", "noopener");
    else nav(key);
  };

  return (
    <Layout style={{ minHeight: "100vh" }}>
      <Sider
        width={232}
        style={{ borderRight: "1px solid var(--mai-line)", padding: "8px 0" }}
        breakpoint="lg"
        collapsedWidth={0}
        trigger={null}
      >
        <div style={{ padding: "18px 20px 12px" }}>
          <Brand size={24} />
        </div>
        <Menu
          mode="inline"
          selectedKeys={selectedKeys}
          onClick={(e) => handleNav(e.key)}
          items={navItems}
          style={{ background: "transparent", borderInlineEnd: "none", padding: "0 12px" }}
        />
        <div style={{ position: "absolute", bottom: 16, left: 20, right: 20 }}>
          <div className="mai-mono" style={{ fontSize: 11, color: "var(--mai-muted)" }}>
            your assistant, on WhatsApp
          </div>
        </div>
      </Sider>

      {/* Mobile navigation drawer (opened by the header hamburger) */}
      <Drawer
        placement="left"
        width={250}
        open={drawerOpen}
        onClose={() => setDrawerOpen(false)}
        closable={false}
        styles={{ body: { padding: "12px 0", background: "var(--mai-bg-soft)" },
                  header: { display: "none" } }}
      >
        <div style={{ padding: "8px 20px 12px" }}>
          <Brand size={24} />
        </div>
        <Menu
          mode="inline"
          selectedKeys={selectedKeys}
          onClick={(e) => { handleNav(e.key); setDrawerOpen(false); }}
          items={navItems}
          style={{ background: "transparent", borderInlineEnd: "none", padding: "0 12px" }}
        />
      </Drawer>

      <Layout>
        <Header
          className="mai-header-safe"
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "flex-end",
            gap: 14,
            borderBottom: "1px solid var(--mai-line)",
            paddingInline: 20,
          }}
        >
          <Button
            type="text"
            className="mai-burger"
            icon={<MenuOutlined />}
            onClick={() => setDrawerOpen(true)}
            aria-label="Menu"
            style={{ marginRight: "auto", color: "var(--mai-text)" }}
          />
          {me && (
            <Tag color={statusColor[me.subscription_status] || "default"} className="mai-plan-tag" style={{ borderRadius: 20 }}>
              {me.plan} · {me.subscription_status}
            </Tag>
          )}
          <Dropdown
            menu={{
              items: [{ key: "logout", icon: <LogoutOutlined />, label: "Sign out", onClick: logout }],
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
              <Avatar style={{ background: "linear-gradient(135deg,#5effb0,#21d4a8)", color: "#06231a" }}>
                {(me?.email?.[0] || "n").toUpperCase()}
              </Avatar>
              <span className="mai-user-email" style={{ color: "var(--mai-muted)", fontSize: 13 }}>{me?.email}</span>
            </div>
          </Dropdown>
        </Header>
        <Content className="mai-content" style={{ padding: 28, maxWidth: 1080, width: "100%", margin: "0 auto" }}>
          {children}
        </Content>
      </Layout>
    </Layout>
  );
}
