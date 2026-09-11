import { createContext, useContext, useEffect, useMemo, useState } from "react";
import { api, authStore } from "../api/client";

export type Membership = { tenant: { id: string; name: string; slug: string }; role: string; permissions: string[] };
export type Session = { user: { id: string; username: string; name: string; is_platform_admin: boolean }; active_tenant: { id: string; name: string; slug: string; enabled_modules: string[] } | null; memberships: Membership[]; permissions: string[] };
export type AccessRequirement={module?:string;anyPermissions?:string[];allPermissions?:string[]};
export function canAccess(session:Session|null,requirement:AccessRequirement={}){const permissions=new Set(session?.permissions??[]);const modules=new Set(session?.active_tenant?.enabled_modules??[]);return (!requirement.module||modules.has(requirement.module))&&(!requirement.anyPermissions?.length||requirement.anyPermissions.some(p=>permissions.has(p)))&&(!requirement.allPermissions?.length||requirement.allPermissions.every(p=>permissions.has(p)));}
type AuthState = { session: Session | null; loading: boolean; error: string | null; platformAccess: boolean; login(u:string,p:string):Promise<void>; logout():Promise<void>; selectTenant(s:string):Promise<void>; refresh():Promise<void> };
const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(Boolean(authStore.token()));
  const [error, setError] = useState<string | null>(null);
  async function loadSession(slug?: string) {
    const next = await api<Session>("/session/", { tenant: slug ?? authStore.tenant() ?? undefined });
    authStore.setTenant(slug ?? next.active_tenant?.slug ?? null); setSession(next);
  }
  async function refresh() { setLoading(true); setError(null); try { await loadSession(); } catch (caught) { authStore.setToken(null); setSession(null); setError(caught instanceof Error ? caught.message : "Could not load session"); } finally { setLoading(false); } }
  useEffect(() => { if (authStore.token()) void refresh(); }, []);
  async function login(username:string,password:string) {
    const result=await api<{token:string}>("/auth/login/",{method:"POST",tenant:false,body:JSON.stringify({username,password})});
    authStore.setToken(result.token);
    // Clear any tenant remembered from a previous login on this browser --
    // otherwise loadSession() sends a stale X-Tenant-Slug the newly
    // authenticated user may have no membership in (403), even though the
    // login itself just succeeded. A fresh login should resolve to this
    // user's own first membership, same as the backend's own fallback.
    authStore.setTenant(null);
    await loadSession();
  }
  async function logout() { try { await api("/auth/logout/",{method:"POST",tenant:false}); } finally { authStore.setToken(null);authStore.setTenant(null);setSession(null); } }
  async function selectTenant(slug:string) { authStore.setTenant(slug); await loadSession(slug); }
  const platformAccess = session?.user.is_platform_admin ?? false;
  const value=useMemo(()=>({session,loading,error,platformAccess,login,logout,selectTenant,refresh}),[session,loading,error,platformAccess]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
export function useAuth(){const value=useContext(AuthContext);if(!value)throw new Error("AuthProvider is missing");return value;}
export function useAccess(){const {session}=useAuth();return {canAccess:(r:AccessRequirement)=>canAccess(session,r),can:(p?:string)=>canAccess(session,{allPermissions:p?[p]:[]}),canAny:(p?:string[])=>canAccess(session,{anyPermissions:p}),moduleEnabled:(m?:string)=>canAccess(session,{module:m})};}
