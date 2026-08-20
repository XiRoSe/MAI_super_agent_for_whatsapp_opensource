import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, clearToken, getToken, setToken } from "../api/client";

type Me = {
  account_id: number;
  email: string;
  display_name: string | null;
  plan: string;
  subscription_status: string;
  is_admin: boolean;
};

type AuthCtx = {
  me: Me | null;
  loading: boolean;
  login: (email: string, password: string) => Promise<void>;
  register: (email: string, password: string) => Promise<void>;
  logout: () => void;
  refresh: () => Promise<void>;
};

const Ctx = createContext<AuthCtx>(null as unknown as AuthCtx);
export const useAuth = () => useContext(Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  async function refresh() {
    if (!getToken()) {
      setMe(null);
      setLoading(false);
      return;
    }
    try {
      // /auth/me carries identity; is_admin lives on the account resource.
      // Admin lookup is best-effort and visibility-only — admin APIs 403 regardless.
      const [{ data }, isAdmin] = await Promise.all([
        api.get<Omit<Me, "is_admin">>("/auth/me"),
        api
          .get<{ is_admin?: boolean }>("/api/account")
          .then((r) => r.data?.is_admin === true)
          .catch(() => false),
      ]);
      setMe({ ...data, is_admin: isAdmin });
    } catch {
      setMe(null);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function login(email: string, password: string) {
    const { data } = await api.post("/auth/login", { email, password });
    setToken(data.access_token);
    await refresh();
  }
  async function register(email: string, password: string) {
    const { data } = await api.post("/auth/register", { email, password });
    setToken(data.access_token);
    await refresh();
  }
  function logout() {
    clearToken();
    setMe(null);
    location.href = "/login";
  }

  return (
    <Ctx.Provider value={{ me, loading, login, register, logout, refresh }}>
      {children}
    </Ctx.Provider>
  );
}
