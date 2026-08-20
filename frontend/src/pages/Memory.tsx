import { App as AntApp, Button, Card, Empty, Form, Input, Modal, Popconfirm, Select, Tag } from "antd";
import { PlusOutlined, EditOutlined, DeleteOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { api, apiError } from "../api/client";

type Mem = { id: string; text: string; topics: string[] };

export default function Memory() {
  const { message } = AntApp.useApp();
  const [mems, setMems] = useState<Mem[]>([]);
  const [loading, setLoading] = useState(true);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Mem | null>(null);
  const [form] = Form.useForm();

  async function load() {
    try {
      setMems((await api.get("/api/memories")).data.memories);
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setLoading(false);
    }
  }
  useEffect(() => { load(); }, []);

  function openNew() {
    setEditing(null);
    form.resetFields();
    setOpen(true);
  }
  function openEdit(m: Mem) {
    setEditing(m);
    form.setFieldsValue({ text: m.text, topics: m.topics || [] });
    setOpen(true);
  }

  async function save() {
    const v = await form.validateFields();
    const body = { text: v.text, topics: v.topics || [] };
    try {
      if (editing) {
        await api.patch(`/api/memories/${encodeURIComponent(editing.id)}`, body);
        message.success("Memory updated");
      } else {
        await api.post("/api/memories", body);
        message.success("Memory added");
      }
      setOpen(false);
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  async function remove(id: string) {
    try {
      await api.delete(`/api/memories/${encodeURIComponent(id)}`);
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  return (
    <div className="rise" style={{ maxWidth: 760, marginInline: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="mai-display" style={{ fontSize: 26 }}>Memory</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>New memory</Button>
      </div>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        What Mai remembers about you across chats. Add, edit, or delete anything.
      </p>

      {loading ? null : mems.length === 0 ? (
        <Card><Empty description="Mai hasn't learned anything about you yet" /></Card>
      ) : (
        <div style={{ display: "grid", gap: 10 }}>
          {mems.map((m) => (
            <Card key={m.id} size="small">
              <div style={{ display: "flex", justifyContent: "space-between", gap: 12, alignItems: "flex-start" }}>
                <div style={{ minWidth: 0 }}>
                  <div>{m.text}</div>
                  {m.topics?.length > 0 && (
                    <div style={{ marginTop: 6 }}>
                      {m.topics.map((t) => <Tag key={t} color="cyan">{t}</Tag>)}
                    </div>
                  )}
                </div>
                <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(m)} />
                  <Popconfirm title="Forget this?" onConfirm={() => remove(m.id)}>
                    <Button size="small" danger icon={<DeleteOutlined />} />
                  </Popconfirm>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal title={editing ? "Edit memory" : "New memory"} open={open}
             onOk={save} okText="Save" onCancel={() => setOpen(false)} destroyOnClose>
        <Form form={form} layout="vertical">
          <Form.Item name="text" label="Memory" rules={[{ required: true, message: "Enter something to remember" }]}>
            <Input.TextArea placeholder="e.g. Prefers morning meetings" autoSize={{ minRows: 2, maxRows: 6 }} />
          </Form.Item>
          <Form.Item name="topics" label="Topics" extra="Optional tags to group related memories.">
            <Select mode="tags" placeholder="preferences, work" tokenSeparators={[","]} open={false} />
          </Form.Item>
        </Form>
      </Modal>
    </div>
  );
}
