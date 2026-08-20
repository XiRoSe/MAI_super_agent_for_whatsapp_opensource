import axios from "axios";

// Backend base URL. Empty string => same origin (the backend serves the SPA).
// Unset (dev) => local AgentOS app. Set to a full URL for a split deployment.
const envUrl = import.meta.env.VITE_API_URL;
const baseURL = envUrl !== undefined ? envUrl : "http://localhost:7777";

export const api = axios.create({ baseURL });

const TOKEN_KEY = "mai_token";

export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function setToken(token: string) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  (err) => {
    if (err?.response?.status === 401 && getToken()) {
      clearToken();
      if (!location.pathname.startsWith("/login")) location.href = "/login";
    }
    return Promise.reject(err);
  }
);

export function apiError(e: unknown): string {
  const anyE = e as any;
  return anyE?.response?.data?.detail || anyE?.message || "Something went wrong";
}
