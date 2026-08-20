import { Navigate, Route, Routes } from "react-router-dom";
import { Spin } from "antd";
import { useAuth } from "./auth/AuthContext";
import { Shell } from "./components/Layout";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import Chat from "./pages/Chat";
import Connect from "./pages/Connect";
import Schedules from "./pages/Schedules";
import Integrations from "./pages/Integrations";
import Skills from "./pages/Skills";
import Memory from "./pages/Memory";
import Personality from "./pages/Personality";
import Billing from "./pages/Billing";
import Settings from "./pages/Settings";
import CRM from "./pages/CRM";

function Protected({ children }: { children: JSX.Element }) {
  const { me, loading } = useAuth();
  if (loading)
    return (
      <div style={{ display: "grid", placeItems: "center", height: "100vh" }}>
        <Spin size="large" />
      </div>
    );
  return me ? children : <Navigate to="/login" replace />;
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route
        path="/crm"
        element={
          <Protected>
            <CRM />
          </Protected>
        }
      />
      <Route
        path="/*"
        element={
          <Protected>
            <Shell>
              <Routes>
                <Route path="/" element={<Dashboard />} />
                <Route path="/chat" element={<Chat />} />
                <Route path="/connect" element={<Connect />} />
                <Route path="/schedules" element={<Schedules />} />
                <Route path="/skills" element={<Skills />} />
                <Route path="/memory" element={<Memory />} />
                <Route path="/personality" element={<Personality />} />
                <Route path="/integrations" element={<Integrations />} />
                <Route path="/billing" element={<Billing />} />
                <Route path="/settings" element={<Settings />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Shell>
          </Protected>
        }
      />
    </Routes>
  );
}
