import { Button, Card, Form, Select, Input, Switch, App as AntApp } from "antd";
import { useEffect, useState } from "react";
import { api, apiError } from "../api/client";
import { useAuth } from "../auth/AuthContext";

// A compact, useful set of IANA timezones. Users can type to search.
const TZS = [
  "UTC", "America/New_York", "America/Chicago", "America/Denver", "America/Los_Angeles",
  "America/Sao_Paulo", "Europe/London", "Europe/Paris", "Europe/Berlin", "Europe/Madrid",
  "Asia/Jerusalem", "Asia/Dubai", "Asia/Kolkata", "Asia/Singapore", "Asia/Tokyo",
  "Australia/Sydney",
];

export default function Settings() {
  const { me } = useAuth();
  const { message } = AntApp.useApp();
  const [form] = Form.useForm();
  const [saving, setSaving] = useState(false);
  const [autoMemory, setAutoMemory] = useState(false);
  const [memToggling, setMemToggling] = useState(false);

  useEffect(() => {
    api.get("/api/settings").then((r) => form.setFieldsValue(r.data)).catch(() => {});
    api.get("/api/account").then((r) => setAutoMemory(r.data.auto_memory ?? false)).catch(() => {});
  }, []);

  async function save() {
    const v = await form.validateFields();
    setSaving(true);
    try {
      await api.patch("/api/settings", v);
      message.success("Saved");
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setSaving(false);
    }
  }

  async function toggleAutoMemory(checked: boolean) {
    setMemToggling(true);
    try {
      await api.patch("/api/account", { auto_memory: checked });
      setAutoMemory(checked);
      message.success(checked ? "Memory learning enabled" : "Memory learning disabled");
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setMemToggling(false);
    }
  }

  return (
    <div className="rise" style={{ maxWidth: 560, marginInline: "auto" }}>
      <h1 className="mai-display" style={{ fontSize: 26 }}>Settings</h1>
      <Card>
        <Form form={form} layout="vertical">
          <Form.Item label="Email">
            <Input value={me?.email} disabled />
          </Form.Item>
          <Form.Item name="display_name" label="Display name">
            <Input placeholder="What should Mai call you?" />
          </Form.Item>
          <Form.Item
            name="timezone"
            label="Timezone"
            extra="Required for scheduling — '8am' is meaningless without it."
          >
            <Select
              showSearch
              placeholder="Select your timezone"
              options={TZS.map((t) => ({ value: t, label: t }))}
            />
          </Form.Item>
          <Button type="primary" loading={saving} onClick={save}>Save changes</Button>
        </Form>
      </Card>

      <Card style={{ marginTop: 16 }}>
        <div style={{ display: "flex", alignItems: "flex-start", gap: 12 }}>
          <Switch
            checked={autoMemory}
            loading={memToggling}
            onChange={toggleAutoMemory}
          />
          <div>
            <div style={{ fontWeight: 500 }}>Auto-learn about me (memory)</div>
            <div style={{ color: "var(--color-text-secondary, #888)", fontSize: 13, marginTop: 2 }}>
              Off by default. When on, Mai builds a private understanding of you from recent
              messages — kept in memory only, never stored.
            </div>
          </div>
        </div>
      </Card>
    </div>
  );
}
