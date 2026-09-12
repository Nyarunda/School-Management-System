import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import "./styles.css";

type Student = { id: string; admission_number: string; full_name: string; campus: string | null; status: string };
type Page<T> = { count: number; results: T[] };
type Session = { user: { name: string }; active_tenant: { slug: string; name: string } | null; permissions: string[] };
type FinanceSummary = { balance: string | number; invoices: Array<{ id: string; invoice_number: string; status: string; total: string | number; lines: Array<{ description: string }> }>; ledger: Array<{ id: string; entry_type: string; amount: string | number; posted_at: string }> };

const queryClient = new QueryClient();
const requestedTenantSlug = import.meta.env.VITE_TENANT_SLUG ?? "school-a";

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`/api/v1${path}`, { headers: { "X-Tenant-Slug": requestedTenantSlug }, credentials: "include" });
  if (!response.ok) throw new Error(response.status === 401 ? "Sign in to continue" : response.status === 403 ? "You do not have permission for this workspace" : `Request failed (${response.status})`);
  return response.json() as Promise<T>;
}

function App() { return <QueryClientProvider client={queryClient}><StudentsWorkspace /></QueryClientProvider>; }

function StudentsWorkspace() {
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [tab, setTab] = useState("Overview");
  const session = useQuery({ queryKey: ["session", requestedTenantSlug], queryFn: () => getJson<Session>("/session/") });
  const tenantSlug = session.data?.active_tenant?.slug ?? requestedTenantSlug;
  const students = useQuery({ queryKey: ["students", tenantSlug], queryFn: () => getJson<Page<Student>>("/students/"), enabled: session.isSuccess && Boolean(session.data.active_tenant) });
  const selected = students.data?.results.find((student) => student.id === selectedId) ?? students.data?.results[0] ?? null;

  return <main className="shell">
    <header className="workspace-header"><div><p className="eyebrow">{session.data?.active_tenant?.name ?? tenantSlug} / Students</p><h1>Student lifecycle</h1></div><span className="tenant-pill">{session.data?.user.name ?? "Live API"}</span></header>
    {session.isLoading && <div className="panel empty-state"><h2>Loading session</h2><p>Resolving your active school and permissions.</p></div>}
    {session.isError && <div className="panel empty-state error-state"><h2>Sign-in required</h2><p>{session.error.message}</p></div>}
    {session.data && !session.data.active_tenant && <div className="panel empty-state"><h2>No active school</h2><p>Your account has no active school membership.</p></div>}
    {students.isLoading && <div className="panel empty-state"><h2>Loading students</h2><p>Fetching the active tenant workspace.</p></div>}
    {students.isError && <div className="panel empty-state error-state"><h2>Student workspace unavailable</h2><p>{students.error.message}</p></div>}
    {students.data && students.data.results.length === 0 && <div className="panel empty-state"><h2>No students yet</h2><p>Students admitted into this tenant will appear here.</p></div>}
    {selected && <div className="workspace-grid"><section className="panel student-list"><div className="panel-heading"><h2>Students</h2><span>{students.data?.count ?? 0} total</span></div>{students.data?.results.map((student) => <button className={`student-row ${selected.id === student.id ? "selected" : ""}`} key={student.id} onClick={() => { setSelectedId(student.id); setTab("Overview"); }}><span><strong>{student.full_name}</strong><small>{student.admission_number} · {student.campus ?? "No campus"}</small></span><em>{student.status}</em></button>)}</section><section className="panel student-detail"><div className="student-heading"><div><p className="eyebrow">{selected.admission_number}</p><h2>{selected.full_name}</h2><p>{selected.campus ?? "No campus"} · {selected.status.toLowerCase()} student</p></div><span className="status-pill">{selected.status}</span></div><nav className="tabs" aria-label="Student details">{["Overview", "Academics", "Fees", "Guardians", "Documents", "Activity"].map((item) => <button className={tab === item ? "active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}</nav>{tab === "Overview" && <Overview />}{tab === "Academics" && <Info title="Academics" text="Academic placement is available from the API-backed academic workspace." />}{tab === "Fees" && <Fees studentId={selected.id} tenantSlug={tenantSlug} />}{tab === "Guardians" && <Info title="Guardians" text="No guardians linked yet." />}{tab === "Documents" && <Info title="Documents" text="No documents available." />}{tab === "Activity" && <Info title="Activity" text="Activity events will appear here." />}</section></div>}
  </main>;
}

function Fees({ studentId, tenantSlug }: { studentId: string; tenantSlug: string }) {
  const finance = useQuery({ queryKey: ["student-finance", tenantSlug, studentId], queryFn: () => getJson<FinanceSummary>(`/finance/students/${studentId}/finance/`) });
  if (finance.isLoading) return <div className="empty-state"><h3>Loading fees</h3><p>Preparing the student account.</p></div>;
  if (finance.isError) return <div className="empty-state error-state"><h3>Fees unavailable</h3><p>{finance.error.message}</p></div>;
  if (!finance.data) return null;
  return <div className="fees-workspace"><div className="finance-summary"><div><small>Outstanding balance</small><strong>KES {finance.data.balance}</strong></div><div><small>Invoices</small><strong>{finance.data.invoices.length}</strong></div><div><small>Ledger entries</small><strong>{finance.data.ledger.length}</strong></div></div><section><div className="section-heading"><h3>Invoices</h3><span>Payments are not connected yet</span></div>{finance.data.invoices.length === 0 ? <p className="muted">No invoices recorded.</p> : <div className="finance-list">{finance.data.invoices.map((invoice) => <div className="finance-row" key={invoice.id}><span><strong>{invoice.invoice_number}</strong><small>{invoice.lines.map((line) => line.description).join(", ") || "Invoice"}</small></span><span><strong>KES {invoice.total}</strong><em>{invoice.status}</em></span></div>)}</div>}</section><section><div className="section-heading"><h3>Ledger</h3><span>Source of truth</span></div>{finance.data.ledger.length === 0 ? <p className="muted">No ledger entries recorded.</p> : <div className="finance-list">{finance.data.ledger.map((entry) => <div className="finance-row" key={entry.id}><span><strong>{entry.entry_type === "DEBIT" ? "Invoice" : "Credit note"}</strong><small>{new Date(entry.posted_at).toLocaleDateString()}</small></span><strong className={entry.entry_type === "CREDIT" ? "credit" : "debit"}>{entry.entry_type === "CREDIT" ? "-" : "+"}KES {entry.amount}</strong></div>)}</div>}</section></div>;
}

function Overview() { return <div className="overview"><div><small>Status</small><strong>Active</strong></div><div><small>Attendance</small><strong>--</strong></div><div><small>Average</small><strong>--</strong></div></div>; }
function Info({ title, text }: { title: string; text: string }) { return <div className="empty-state"><h3>{title}</h3><p>{text}</p></div>; }

createRoot(document.getElementById("root")!).render(<StrictMode><App /></StrictMode>);
