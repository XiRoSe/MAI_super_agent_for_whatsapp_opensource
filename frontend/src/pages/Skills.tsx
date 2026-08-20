import { App as AntApp, Button, Card, Empty, Form, Input, Modal, Popconfirm, Switch, Tag } from "antd";
import { PlusOutlined, EditOutlined, DeleteOutlined, ReloadOutlined, BulbOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { api, apiError } from "../api/client";

type Skill = {
  id: number; name: string; description: string; content: string;
  enabled: boolean; is_default: boolean; updated_at: string;
};

export default function Skills() {
  const { message } = AntApp.useApp();
  const [skills, setSkills] = useState<Skill[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Skill | null>(null);
  const [form] = Form.useForm();

  async function load() {
    try {
      setSkills((await api.get("/api/skills")).data.skills);
    } catch (e) {
      message.error(apiError(e));
    }
  }
  useEffect(() => { load(); }, []);

  function openNew() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }
  function openEdit(s: Skill) {
    setEditing(s);
    form.setFieldsValue({ name: s.name, description: s.description, content: s.content });
    setOpen(true);
  }

  async function save() {
    const v = await form.validateFields();
    try {
      if (editing) {
        await api.patch(`/api/skills/${editing.id}`, v);
        message.success("Skill updated");
      } else {
        await api.post("/api/skills", v);
        message.success("Skill added");
      }
      setOpen(false);
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  async function toggle(s: Skill, enabled: boolean) {
    setSkills((arr) => arr.map((x) => (x.id === s.id ? { ...x, enabled } : x)));
    try {
      await api.patch(`/api/skills/${s.id}`, { enabled });
    } catch (e) {
      message.error(apiError(e));
      load();
    }
  }

  async function remove(s: Skill) {
    try {
      await api.delete(`/api/skills/${s.id}`);
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  async function resetDefaults() {
    try {
      await api.post("/api/skills/reset");
      message.success("Restored any missing default skills");
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  return (
    <div className="rise">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="mai-display" style={{ fontSize: 26 }}>Skills</h1>
        <div style={{ display: "flex", gap: 8 }}>
          <Button icon={<ReloadOutlined />} onClick={resetDefaults}>Restore defaults</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>New skill</Button>
        </div>
      </div>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        Reusable know-how Mai pulls in only when a task matches. Toggle, edit, or add your own.
      </p>

      {skills.length === 0 ? (
        <Card><Empty description="No skills yet" /></Card>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {skills.map((s) => (
            <Card key={s.id} size="small" style={{ opacity: s.enabled ? 1 : 0.55 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <BulbOutlined style={{ color: "var(--mai-accent)" }} />
                    <b>{s.name}</b>
                    {s.is_default && <Tag color="cyan">default</Tag>}
                  </div>
                  <div style={{ color: "var(--mai-muted)", fontSize: 13, marginTop: 4 }}>{s.description}</div>
                </div>
                <div style={{ display: "flex", gap: 8, alignItems: "center", flexShrink: 0 }}>
                  <Switch checked={s.enabled} onChange={(v) => toggle(s, v)} size="small" />
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(s)} />
                  <Popconfirm title="Delete this skill?" onConfirm={() => remove(s)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal title={editing ? `Edit: ${editing.name}` : "New skill"} open={open}
             onOk={save} okText="Save" onCancel={() => setOpen(false)} width={640} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="name" label="Name (short id)" rules={[{ required: true }]}
                     extra="kebab-case, e.g. 'meeting-notes'">
            <Input placeholder="meeting-notes" />
          </Form.Item>
          <Form.Item name="description" label="When to use it" rules={[{ required: true }]}
                     extra="Mai reads this to decide when the skill is relevant.">
            <Input.TextArea placeholder="Use when the user asks to take notes from a meeting…" autoSize={{ minRows: 2 }} />
          </Form.Item>
          <Form.Item name="content" label="Instructions (Markdown)" rules={[{ required: true }]}>
            <Input.TextArea placeholder="# Steps&#10;1. …" autoSize={{ minRows: 8, maxRows: 20 }} className="mai-mono" />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
