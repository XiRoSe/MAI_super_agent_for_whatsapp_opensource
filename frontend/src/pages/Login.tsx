import { Button, Form, Input, App as AntApp } from "antd";
import { MailOutlined, LockOutlined } from "@ant-design/icons";
import { Link, Navigate, useNavigate } from "react-router-dom";
import { useState } from "react";
import { Brand } from "../components/Brand";
import { useAuth } from "../auth/AuthContext";
import { apiError } from "../api/client";
import { AuthAside, MobileAuthHero, AuthModeToggle } from "./authShared";

export default function Login() {
  const { login, me } = useAuth();
  const nav = useNavigate();
  const { message } = AntApp.useApp();
  const [loading, setLoading] = useState(false);
  if (me) return <Navigate to="/" replace />;

  async function onFinish(v: { email: string; password: string }) {
    setLoading(true);
    try {
      await login(v.email, v.password);
      nav("/");
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="mai-aurora mai-auth">
      <AuthAside />
      <div style={{ display: "grid", placeItems: "center", padding: 24 }}>
        <div style={{ width: 360, maxWidth: "100%" }}>
          <MobileAuthHero />
          <div className="rise" style={{ width: 360, maxWidth: "100%" }}>
            <div className="mai-auth-formwrap">
              <AuthModeToggle mode="login" />
              <div style={{ marginBottom: 28 }}>
                <Brand size={26} />
              </div>
              <h1 className="mai-display" style={{ fontSize: 30, margin: "0 0 6px" }}>
                Welcome back
              </h1>
              <p style={{ color: "var(--mai-muted)", marginTop: 0, marginBottom: 24 }}>
                Sign in to your Mai command center.
              </p>
              <Form layout="vertical" onFinish={onFinish} requiredMark={false}>
                <Form.Item name="email" label="Email" rules={[{ required: true, type: "email" }]}>
                  <Input
                    size="large"
                    placeholder="you@example.com"
                    autoComplete="email"
                    prefix={<MailOutlined />}
                  />
                </Form.Item>
                <Form.Item name="password" label="Password" rules={[{ required: true }]}>
                  <Input.Password
                    size="large"
                    placeholder="••••••••"
                    autoComplete="current-password"
                    prefix={<LockOutlined />}
                  />
                </Form.Item>
                <Button type="primary" htmlType="submit" size="large" block loading={loading}>
                  Sign in
                </Button>
              </Form>
            </div>
          </div>
          <p className="mai-auth-xlink" style={{ color: "var(--mai-muted)", marginTop: 20, textAlign: "center" }}>
            New to Mai?{" "}
            <Link to="/register" style={{ color: "var(--mai-accent)" }}>
              Create account
            </Link>
          </p>
        </div>
      </div>
    </div>
  );
}
