import { App as AntApp, Button, Card, Input, Popconfirm, Tag } from "antd";
import { ReloadOutlined, SaveOutlined, HeartOutlined } from "@ant-design/icons";
import { useEffect, useState } from "react";
import { api, apiError } from "../api/client";

type State = { content: string; updated_at: string | null; msg_count: number };

export default function Personality() {
  const { message } = AntApp.useApp();
  const [state, setState] = useState<State | null>(null);
  const [content, setContent] = useState("");
  const [saving, setSaving] = useState(false);

  async function load() {
    try {
      const { data } = await api.get<State>("/api/beliefs");
      setState(data);
      setContent(data.content);
    } catch (e) {
      message.error(apiError(e));
    }
  }
  useEffect(() => { load(); }, []);

  async function save() {
    setSaving(true);
    try {
      const { data } = await api.put("/api/beliefs", { content });
      setState(data);
      setContent(data.content);
      message.success("Saved Mai's perspective");
    } catch (e) {
      message.error(apiError(e));
    } finally {
      setSaving(false);
    }
  }

  async function reset() {
    try {
      const { data } = await api.post("/api/beliefs/reset");
      setState(data);
      setContent(data.content);
      message.success("Reset to the default voice");
    } catch (e) {
      message.error(apiError(e));
    }
  }

  return (
    <div className="rise" style={{ maxWidth: 760, marginInline: "auto" }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1 className="mai-display" style={{ fontSize: 26 }}>Personality</h1>
        <Popconfirm title="Reset Mai's perspective to the default?" onConfirm={reset}>
          <Button icon={<ReloadOutlined />}>Reset</Button>
        </Popconfirm>
      </div>
      <p style={{ color: "var(--mai-muted)", marginTop: 0 }}>
        Mai's evolving self-perspective — its personality and principles (not facts about
        you). It grows on its own every few messages; you can also shape it directly here.
      </p>

      <Card>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12 }}>
          <HeartOutlined style={{ color: "var(--mai-accent)" }} />
          <b>Mai's current perspective</b>
          {state && (
            <Tag color="cyan" style={{ marginLeft: "auto" }}>
              {state.msg_count} messages{state.updated_at ? ` · evolved ${new Date(state.updated_at).toLocaleDateString()}` : ""}
            </Tag>
          )}
        </div>
        <Input.TextArea
          value={content}
          onChange={(e) => setContent(e.target.value)}
          autoSize={{ minRows: 6, maxRows: 16 }}
          className="mai-mono"
          placeholder="- I'm warm and pragmatic…"
        />
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 14 }}>
          <Button type="primary" icon={<SaveOutlined />} loading={saving}
                  onClick={save} disabled={!state || content === state.content}>
            Save
          </Button>
        </div>
      </Card>
    </div>
  );
}
