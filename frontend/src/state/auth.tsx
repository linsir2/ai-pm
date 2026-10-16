/* 身份与会话：一个 AuthProvider 管「现在是谁」，会话真相在 cookie（`pm_session`），
   `/me` 只用来在刷新后把它读回来——token 永不落 localStorage。

   401 的拦截**只此一处**：client 回 401 时通知这里清身份，App 的守卫随即渲染登录页。
   Task 8 的管理后台用 `useAuth()` 取 role 判权限。 */

import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { api, setUnauthorizedHandler } from "../api/client";
import type { AuthUser } from "../api/client";

export type { AuthUser };

export interface AuthApi {
  /** 当前用户；未登录为 null。 */
  user: AuthUser | null;
  /** `/me` 恢复是否跑完。false 时界面该显示"正在读身份"，别急着判未登录。 */
  ready: boolean;
  /** 登录成功即把身份落进内存（cookie 由后端写）。 */
  login: (username: string, password: string) => Promise<AuthUser>;
  /** 注册：返回新用户（通常 status=pending，还不能登录）。 */
  register: (username: string, password: string) => Promise<AuthUser>;
  /** 登出：先叫后端吊销会话与 cookie，再清内存身份（失败也清）。 */
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthApi | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [ready, setReady] = useState(false);

  // 刷新恢复：cookie 还在就 `/me` 读回身份；401 由 client 通知（也会清回 null）。
  useEffect(() => {
    let alive = true;
    void api
      .me()
      .then((me) => {
        if (alive) setUser(me);
      })
      .catch(() => {
        if (alive) setUser(null);
      })
      .finally(() => {
        if (alive) setReady(true);
      });
    return () => {
      alive = false;
    };
  }, []);

  // 任一请求撞 401 → 清身份（App 守卫会跳登录页）。
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const me = await api.login(username, password);
    setUser(me);
    return me;
  }, []);

  const register = useCallback(
    (username: string, password: string) => api.register(username, password),
    [],
  );

  const logout = useCallback(async () => {
    try {
      await api.logout();
    } finally {
      setUser(null);
    }
  }, []);

  const value = useMemo<AuthApi>(
    () => ({ user, ready, login, register, logout }),
    [user, ready, login, register, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthApi {
  const context = useContext(AuthContext);
  if (context === null) {
    throw new Error("useAuth 必须在 <AuthProvider> 内使用");
  }
  return context;
}
