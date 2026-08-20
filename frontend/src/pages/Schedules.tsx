import { Button, Card, Empty, Form, Input, Modal, Segmented, Select, Tag, App as AntApp, DatePicker } from "antd";
import { PlusOutlined, ClockCircleOutlined, EditOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import dayjs, { type Dayjs } from "dayjs";
import { api, apiError } from "../api/client";

type Schedule = {
  id: number; name: string; kind: string; schedule_type: string;
  cron: string | null; run_at: string | null; timezone: string; action_text: string;
};

export default function Schedules() {
  const { message } = AntApp.useApp();
  const [items, setItems] = useState<Schedule[]>([]);
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [type, setType] = useState<"once" | "recurring">("once");
  const [form] = Form.useForm();

  async function load() {
    try {
      const { data } = await api.get("/api/schedules");
      setItems(data.schedules);
    } catch (e) {
      message.error(apiError(e));
    }
  }
  useEffect(() => { load(); }, []);

  function openNew() {
    setEditing(null);
    setType("once");
    form.resetFields();
    form.setFieldsValue({ kind: "static" });
    setOpen(true);
  }

  function openEdit(s: Schedule) {
    setEditing(s);
    setType(s.schedule_type as "once" | "recurring");
    form.setFieldsValue({
      name: s.name, kind: s.kind, action_text: s.action_text,
      cron: s.cron || undefined,
      run_at: s.run_at ? dayjs(s.run_at) : undefined,
    });
    setOpen(true);
  }

  async function save() {
    const v = await form.validateFields();
    const body: any = {
      name: v.name, kind: v.kind, action_text: v.action_text, schedule_type: type,
    };
    if (type === "once") body.run_at = (v.run_at as Dayjs).format("YYYY-MM-DDTHH:mm");
    else body.cron = v.cron;
    try {
      if (editing) {
        await api.patch(`/api/schedules/${editing.id}`, body);
        message.success("Schedule updated");
      } else {
        await api.post("/api/schedules", body);
        message.success("Schedule created");
      }
      setOpen(false);
      form.resetFields();
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  async function cancel(id: number) {
    try {
      await api.delete(`/api/schedules/${id}`);
      load();
    } catch (e) {
      message.error(apiError(e));
    }
  }

  return (
    <div className="rise">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="mai-display" style={{ fontSize: 26 }}>Schedules</h1>
        <Button type="primary" icon={<PlusOutlined />} onClick={openNew}>New schedule</Button>
      </div>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        One-off reminders or recurring tasks. Dynamic ones re-run Mai at fire time.
      </p>

      {items.length === 0 ? (
        <Card><Empty description="No schedules yet" /></Card>
      ) : (
        <div style={{ display: "grid", gap: 12 }}>
          {items.map((s) => (
            <Card key={s.id} size="small">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                <div style={{ minWidth: 0 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
                    <ClockCircleOutlined style={{ color: "var(--mai-accent)" }} />
                    <b>{s.name}</b>
                    <Tag color={s.kind === "dynamic" ? "cyan" : "default"}>{s.kind}</Tag>
                  </div>
                  <div className="mai-mono" style={{ color: "var(--mai-muted)", fontSize: 12, marginTop: 4 }}>
                    {s.schedule_type === "once" ? `once · ${s.run_at}` : `cron · ${s.cron}`} · {s.timezone}
                  </div>
                  <div style={{ color: "var(--mai-muted)", fontSize: 13, marginTop: 4 }}>{s.action_text}</div>
                </div>
                <div style={{ display: "flex", gap: 8, flexShrink: 0 }}>
                  <Button size="small" icon={<EditOutlined />} onClick={() => openEdit(s)}>Edit</Button>
                  <Button danger size="small" onClick={() => cancel(s.id)}>Cancel</Button>
                </div>
              </div>
            </Card>
          ))}
        </div>
      )}

      <Modal title={editing ? `Edit: ${editing.name}` : "New schedule"} open={open} onOk={save}
             onCancel={() => setOpen(false)} okText={editing ? "Save" : "Create"} destroyOnClose>
        <Segmented
          options={[{ label: "One-off", value: "once" }, { label: "Recurring", value: "recurring" }]}
          value={type}
          onChange={(v) => setType(v as any)}
          style={{ marginBottom: 16 }}
        />
        <Form form={form} layout="vertical" initialValues={{ kind: "static" }}>
          <Form.Item name="name" label="Name" rules={[{ required: true }]}>
            <Input placeholder="morning brief" />
          </Form.Item>
          <Form.Item name="kind" label="Type">
            <Select
              options={[
                { value: "static", label: "Static — send a fixed message" },
                { value: "dynamic", label: "Dynamic — re-run Mai and send the result" },
              ]}
            />
          </Form.Item>
          <Form.Item name="action_text" label="Message / instruction" rules={[{ required: true }]}>
            <Input.TextArea placeholder="Summarize my day and first meeting" autoSize={{ minRows: 2 }} />
          </Form.Item>
          {type === "once" ? (
            <Form.Item name="run_at" label="When" rules={[{ required: true }]}>
              <DatePicker showTime format="YYYY-MM-DD HH:mm" style={{ width: "100%" }} disabledDate={(d) => d < dayjs().startOf("day")} />
            </Form.Item>
          ) : (
            <Form.Item name="cron" label="Cron (5-field, your timezone)" rules={[{ required: true }]} extra="e.g. weekdays 8am = 0 8 * * 1-5">
              <Input placeholder="0 8 * * 1-5" className="mai-mono" />
            </Form.Item>
          )}
        </Form>
      </Modal>
    </div>
  );
}
