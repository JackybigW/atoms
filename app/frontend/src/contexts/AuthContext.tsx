import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import { client, resetClient } from "@/lib/api";
import { authApi } from "@/lib/auth";

interface UserProfile {
  id: number;
  user_id: string;
  display_name: string;
  avatar_url: string;
  credits: number;
  plan: string;
  email?: string;
}

interface AuthContextType {
  user: UserProfile | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: () => Promise<void>;
  loginWithPassword: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refreshProfile: () => Promise<void>;
}

const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  isAuthenticated: false,
  login: async () => {},
  loginWithPassword: async () => {},
  logout: async () => {},
  refreshProfile: async () => {},
});

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<UserProfile | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  const fetchProfile = useCallback(async (token?: string) => {
    try {
      const profileRes = await client.apiCall.invoke({
        url: "/api/v1/profile/me",
        method: "GET",
        options: token ? { headers: { Authorization: `Bearer ${token}` } } : {},
      });
      if (profileRes?.data && !profileRes.data.error) {
        return profileRes.data;
      }
    } catch (err) {
      console.error("Failed to fetch profile:", err);
    }
    return null;
  }, []);

  const checkAuth = useCallback(async () => {
    try {
      const storedToken = localStorage.getItem("token");

      if (storedToken) {
        // Try JWT-based auth (email/password login) — use fetch directly to
        // avoid SDK axios instance having a stale (empty) Authorization header
        // from app initialization time.
        const meRes = await fetch("/api/v1/auth/me", {
          headers: { Authorization: `Bearer ${storedToken}` },
        });
        if (meRes.ok) {
          const authData = await meRes.json();
          const profileRes = await fetch("/api/v1/profile/me", {
            headers: { Authorization: `Bearer ${storedToken}` },
          });
          const profile = profileRes.ok ? await profileRes.json() : null;
          setUser({
            id: profile?.id ?? 0,
            user_id: authData.id || authData.user_id || "",
            display_name: profile?.display_name || authData.name || authData.email?.split("@")[0] || "User",
            avatar_url: profile?.avatar_url || "",
            credits: profile?.credits ?? 25,
            plan: profile?.plan || "free",
            email: authData.email,
          });
          return;
        } else {
          // Token invalid/expired — clean it up
          localStorage.removeItem("token");
        }
      }

      // Fall back to OIDC session
      const authRes = await client.auth.me();
      if (authRes?.data) {
        const profile = await fetchProfile();
        if (profile) {
          setUser({
            ...profile,
            email: authRes.data.email,
          });
        } else {
          setUser({
            id: 0,
            user_id: authRes.data.id || "",
            display_name: authRes.data.email?.split("@")[0] || "User",
            avatar_url: "",
            credits: 25,
            plan: "free",
            email: authRes.data.email,
          });
        }
      } else {
        setUser(null);
      }
    } catch {
      setUser(null);
    } finally {
      setIsLoading(false);
    }
  }, [fetchProfile]);

  useEffect(() => {
    checkAuth();
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  const login = async () => {
    await authApi.login();
  };

  const loginWithPassword = async (email: string, password: string) => {
    const res = await client.apiCall.invoke({
      url: "/api/v1/auth/login/password",
      method: "POST",
      data: { email, password },
    });
    const data = res?.data;
    if (data?.detail) throw new Error(data.detail);
    if (!data?.token) throw new Error("Sign in failed");
    localStorage.setItem("token", data.token);
    resetClient(); // force SDK to re-read the new token on next entity call
    await checkAuth();
  };

  const logout = async () => {
    localStorage.removeItem("token");
    resetClient(); // force SDK to drop the old token on next entity call
    await client.auth.logout();
    setUser(null);
  };

  const refreshProfile = async () => {
    const profile = await fetchProfile();
    if (profile) {
      setUser((prev) => (prev ? { ...prev, ...profile } : null));
    }
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isAuthenticated: !!user,
        login,
        loginWithPassword,
        logout,
        refreshProfile,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error("useAuth must be used within an AuthProvider");
  }
  return context;
}