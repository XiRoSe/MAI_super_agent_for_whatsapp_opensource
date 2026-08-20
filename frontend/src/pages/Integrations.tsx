import { Button, Card, Empty, Form, Input, Modal, Tag, App as AntApp } from "antd";
import { PlusOutlined, ApiOutlined, DeleteOutlined, EditOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { api, apiError } from "../api/client";

type Svc = { service: string; label: string | null; created_at: string };

// Suggested services — but the field is free-form, connect anything.
const SUGGESTED = ["google", "outlook", "notion", "github", "slack", "stripe"];

export default function Integrations() {
  const { message } = AntApp.useApp();
  const [services, setServices] = useState<Svc[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Svc | null>(null);
  const [form] = Form.useForm();

  async function load() {
    try {
      setServices((await api.get("/api/credentials")).data.services);
    } catch (e) {
      message.error(apiError(e));
    }
  }
  useEffect(() => { load(); }, []);

  function openCreate() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }

  function openEdit(svc: Svc) {
    setEditing(svc);
    form.setFieldsValue({ service: svc.service, label: svc.label ?? "", credentials: "" });
    setOpen(true);
  }

  function closeModal() {
    setOpen(false);
    setEditing(null);
    form.resetFields();
  }

  async function save() {
    const v = await form.validateFields();
    try {
      if (editing) {
        // PATCH — only send credentials if user typed something
        const body: { label: string | null; credentials?: string } = { label: v.label || null };
        if (v.credentials && v.credentials.trim() !== "") {
          body.credentials = v.credentials;
        }
        await api.patch(`/api/credentials/${editing.service}`, body);
        message.success(`Updated ${editing.service}`);
      } else {
        await api.post("/api/credentials", v);
        message.success(`Saved ${v.service}`);
      }
      closeModal();
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  async function remove(service: string) {
    try {
      await api.delete(`/api/credentials/${service}`);
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  return (
    <div className="rise" style={{ maxWidth: 720, marginInline: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="mai-display" style={{ fontSize: 26 }}>Integrations</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={openCreate}>Connect a service</Button>
      </div>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        Give Mai credentials for any service (a key, a token, or a JSON). Mai stores
        them encrypted and uses the matching <b>skill</b> when a task comes up.
      </p>

      {services.length === 0 ? (
        <Card><Empty description="No services connected yet" /></Card>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {services.map((s) => (
            <Card key={s.service} size="small">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <div style={{ display: "flex", gap: 12, alignItems: "center" }}>
                  <ApiOutlined style={{ color: "var(--mai-accent)", fontSize: 18 }} />
                  <div>
                    <div style={{ fontWeight: 600, textTransform: "capitalize" }}>{s.service}</div>
                    {s.label && <div style={{ color: "var(--mai-muted)", fontSize: 12 }}>{s.label}</div>}
                  </div>
                  <Tag color="green">connected</Tag>
                </div>
                <div style={{ display: "flex", gap: 8 }}>
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(s)} />
                  <Button danger size="small" icon={<DeleteOutlined />} onClick={() => remove(s.service)} />
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal
        title={editing ? `Edit ${editing.service}` : "Connect a service"}
        open={open}
        onOk={save}
        okText="Save"
        onCancel={closeModal}
        destroyOnClose
      >
        <Form form={form} layout="vertical">
          <Form.Item
            name="service"
            label="Service"
            rules={editing ? [] : [{ required: true }]}
            extra={
              editing
                ? "To rename a service, remove it and add it again."
                : `Suggestions: ${SUGGESTED.join(", ")}`
            }
          >
            <Input placeholder="google" disabled={!!editing} />
          </Form.Item>
          <Form.Item name="label" label="Label (optional)">
            <Input placeholder="e.g. me@gmail.com" />
          </Form.Item>
          <Form.Item
            name="credentials"
            label="Credentials"
            rules={editing ? [] : [{ required: true }]}
            extra={
              editing
                ? "Leave blank to keep the current credentials — or paste new ones to replace them."
                : "A raw API key/token, or a JSON object like {\"api_key\": \"…\"}"
            }
          >
            <Input.TextArea
              placeholder={editing ? "(unchanged)" : '{"api_key": "..."}'}
              autoSize={{ minRows: 3, maxRows: 8 }}
              className="mai-mono"
            />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
