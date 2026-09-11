import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Badge, Box, Button, Card, Divider, Grid, Group, NumberInput, Paper, PasswordInput, Select, SimpleGrid, Stack, Table, Text, ThemeIcon, TextInput, Title, UnstyledButton } from "@mantine/core";
import { IconArrowRight, IconBell, IconCalendar, IconCheck, IconFileText, IconInbox, IconLock, IconRefresh, IconSparkles, IconUser, IconUsers, IconWallet } from "@tabler/icons-react";
import { api, Page } from "../api/client";
import { useAccess, useAuth } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { DocumentsPanel } from "../features/documents";
import { Column, DataTable } from "../components/DataTable";
import { KeyValueGrid, KeyValueItem, KeyValueSection } from "../components/KeyValueGrid";
import { notify } from "../components/notifications/notify";
import { RecordHeader } from "../components/RecordHeader";
import { RecordTabs } from "../components/RecordTabs";
import { Empty, ErrorState, go, Icon, Loading, PageHeader, StatusBadge } from "../components/ui";
import { WorkspaceHeader } from "../components/WorkspaceHeader";

type Row=Record<string,unknown>;
const money=new Intl.NumberFormat("en-KE",{style:"currency",currency:"KES",maximumFractionDigits:0});
const kes=new Intl.NumberFormat("en-KE",{style:"currency",currency:"KES",minimumFractionDigits:2});
const pretty=(key:string)=>key.replace(/_/g," ").replace(/\b\w/g,(x:string)=>x.toUpperCase());
const whenDateTime=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v)):"—";
const whenDate=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium"}).format(new Date(v)):"—";
function display(value:unknown):React.ReactNode{if(value===null||value===undefined||value==="")return <span className="quiet">—</span>;if(typeof value==="boolean")return <StatusBadge value={value}/>;if(Array.isArray(value))return value.length?`${value.length} items`:<span className="quiet">None</span>;if(typeof value==="object"){const r=value as Row;return String(r.name??r.label??r.username??r.id??"Details")};const text=String(value);if(/^(active|inactive|draft|issued|paid|pending|approved|published|failed|rejected|open|submitted|processed|unmatched|matched)$/i.test(text))return <StatusBadge value={text}/>;return text}

export function LoginPage() {
  const { login } = useAuth();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      await login(username, password);
      go("/");
    } catch (caught) {
      notify.error("Sign in failed", caught);
    } finally {
      setBusy(false);
    }
  }

  function handleDemoFill(user: string, pass: string) {
    setUsername(user);
    setPassword(pass);
  }

  return (
    <main className="auth-page">
      <section className="auth-brand" style={{
        background: "linear-gradient(135deg, #0b1329 0%, #162447 40%, #1f4068 100%)",
        position: "relative",
        overflow: "hidden",
      }}>
        <Box style={{
          position: "absolute", top: -80, right: -80, width: 320, height: 320,
          borderRadius: "50%", background: "radial-gradient(circle, rgba(99,102,241,0.18) 0%, rgba(0,0,0,0) 70%)",
          pointerEvents: "none"
        }} />
        <Box style={{
          position: "absolute", bottom: -60, left: -60, width: 280, height: 280,
          borderRadius: "50%", background: "radial-gradient(circle, rgba(16,185,129,0.12) 0%, rgba(0,0,0,0) 70%)",
          pointerEvents: "none"
        }} />

        <Stack justify="space-between" h="100%" style={{ position: "relative", zIndex: 1 }}>
          <Group gap="sm" wrap="nowrap">
            <img src="/logo.png" alt="" width={42} height={42} style={{ borderRadius: 12, boxShadow: "0 8px 24px rgba(99,102,241,0.4)", objectFit: "cover" }} />
            <Box>
              <Text fw={800} size="lg" c="white" style={{ letterSpacing: "-0.02em" }}>Stemic Schools</Text>
              <Text size="xs" c="indigo.2" fw={500}>Next-Gen School ERP & Fintech</Text>
            </Box>
          </Group>

          <Stack gap="md" my="auto" style={{ maxWidth: 520 }}>
            <Badge color="indigo" variant="light" size="lg" radius="sm" style={{ width: "fit-content" }} leftSection={<IconSparkles size={14} />}>
              Kenyan Enterprise School Operations
            </Badge>

            <Title order={1} c="white" style={{ fontSize: "2.6rem", lineHeight: 1.15, fontWeight: 800, letterSpacing: "-0.03em" }}>
              Run the school day with complete financial & academic clarity.
            </Title>

            <Text size="sm" c="gray.3" lh={1.6}>
              Automated M-Pesa & Bank Paybill collection, CBC assessment rubrics, 8-4-4 exam analytics, and immutable ledger audit trails—unified under one multi-tenant workspace.
            </Text>

            <Stack gap="xs" mt="sm">
              {[
                "Instant WhatsApp & SMS Fee Receipts",
                "Auditable, Evidence-Verified M-Pesa Reconciliation",
                "Longitudinal Academic Trajectory & Report Cards",
                "Campus-Scoped Access & Audit Trails",
              ].map((feat) => (
                <Group gap="xs" key={feat}>
                  <ThemeIcon size={20} radius="xl" color="indigo" variant="light">
                    <IconCheck size={13} />
                  </ThemeIcon>
                  <Text size="xs" c="gray.2" fw={500}>{feat}</Text>
                </Group>
              ))}
            </Stack>
          </Stack>

          <Group gap="xs" wrap="wrap">
            <Badge variant="outline" color="gray" c="gray.4" radius="xl" size="sm">Multi-Tenant Isolated</Badge>
            <Badge variant="outline" color="gray" c="gray.4" radius="xl" size="sm">RBAC Protected</Badge>
            <Badge variant="outline" color="gray" c="gray.4" radius="xl" size="sm">Audit Ready</Badge>
          </Group>
        </Stack>
      </section>

      <section className="auth-form-wrap">
        <Paper p="xl" radius="md" withBorder style={{ width: "min(420px, 100%)", boxShadow: "0 12px 36px rgba(0,0,0,0.06)" }}>
          <form onSubmit={submit}>
            <Stack gap="md">
              <Box>
                <Text size="xs" fw={700} tt="uppercase" c="indigo" style={{ letterSpacing: "0.08em" }}>
                  Workspace Sign In
                </Text>
                <Title order={2} size="h3" fw={800} mt={2}>
                  Welcome back
                </Title>
                <Text size="xs" c="dimmed" mt={2}>
                  Enter your credentials provided by your school administrator.
                </Text>
              </Box>

              <TextInput
                label="Username or Email"
                placeholder="e.g. demo-admin"
                autoFocus
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.currentTarget.value)}
                required
                leftSection={<IconUser size={16} color="var(--mantine-color-dimmed)" />}
              />

              <PasswordInput
                label="Password"
                placeholder="Enter your password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.currentTarget.value)}
                required
                leftSection={<IconLock size={16} color="var(--mantine-color-dimmed)" />}
              />

              <Button type="submit" color="indigo" size="sm" radius="sm" fullWidth loading={busy} rightSection={<IconArrowRight size={16} />}>
                Sign in to workspace
              </Button>

              <Divider label="Quick Demo Access" labelPosition="center" my="xs" />

              <Group justify="center" gap="xs">
                <Button variant="default" size="xs" onClick={() => handleDemoFill("demo-admin", "demo-pass-12345")}>
                  ⚡ Fill Demo Admin
                </Button>
                <Button variant="default" size="xs" onClick={() => handleDemoFill("demo-teacher", "demo-pass-12345")}>
                  ⚡ Fill Demo Teacher
                </Button>
              </Group>

              <Text size="xs" c="dimmed" ta="center" mt="xs">
                Invited to a school? Use the link in your email invitation.
              </Text>
            </Stack>
          </form>
        </Paper>
      </section>
    </main>
  );
}


export function InvitePage(){const token=new URLSearchParams(location.search).get("token")??"";const [password,setPassword]=useState("");const [message,setMessage]=useState("");const [busy,setBusy]=useState(false);async function submit(e:FormEvent){e.preventDefault();setBusy(true);try{const r=await api<{token:string}>("/auth/invites/accept/",{method:"POST",tenant:false,body:JSON.stringify({token,password})});localStorage.setItem("school-erp-token",r.token);location.href="/"}catch(e){setMessage(e instanceof Error?e.message:"Invitation could not be accepted")}finally{setBusy(false)}}return <main className="center-page"><form className="auth-form card" onSubmit={submit}><div className="brand standalone"><img className="brand-mark" src="/logo.png" alt="" style={{objectFit:"cover"}} /><strong>Stemic Schools</strong></div><div><p className="eyebrow">School invitation</p><h2>Set up your access</h2><p>Choose a password if this is your first Stemic Schools account. Existing users may leave it blank.</p></div>{!token&&<div className="form-error">This invitation link has no token.</div>}{message&&<div className="form-error">{message}</div>}<label>Password<input type="password" autoComplete="new-password" value={password} onChange={e=>setPassword(e.target.value)}/></label><button className="button primary wide" disabled={!token||busy}>{busy?"Activating…":"Accept invitation"}</button></form></main>}

function MetricCard({ label, value, meta, tone, icon: IconComponent }: { label: string; value: React.ReactNode; meta: string; tone: "blue" | "teal" | "yellow" | "violet"; icon: any }) {
  return (
    <Paper p="md" radius="md" withBorder style={{ boxShadow: "0 1px 3px rgba(0,0,0,0.04)" }}>
      <Group justify="space-between" align="flex-start" wrap="nowrap">
        <Stack gap={2}>
          <Text size="xs" fw={700} c="dimmed" tt="uppercase" style={{ letterSpacing: "0.05em" }}>
            {label}
          </Text>
          <Text size="xl" fw={800} c="dark.8">
            {value}
          </Text>
          <Text size="xs" c="dimmed">
            {meta}
          </Text>
        </Stack>
        <ThemeIcon size={38} radius="md" color={tone} variant="light">
          <IconComponent size={20} />
        </ThemeIcon>
      </Group>
    </Paper>
  );
}

export function DashboardPage() {
  const { session } = useAuth();
  const { can } = useAccess();
  const students = useQuery({
    queryKey: ["students-count"],
    queryFn: () => api<Page<Row>>("/students/", { params: { page_size: 1 } }),
    enabled: can("students.view"),
  });
  const invoices = useQuery({
    queryKey: ["invoice-count"],
    queryFn: () => api<Page<Row>>("/finance/invoices/", { params: { page_size: 5 } }),
    enabled: can("finance.invoice.view"),
  });
  const payments = useQuery({
    queryKey: ["payment-count"],
    queryFn: () => api<Page<Row>>("/finance/payments/", { params: { page_size: 5 } }),
    enabled: can("finance.payment.view"),
  });
  const incoming = useQuery({
    queryKey: ["incoming-count"],
    queryFn: () => api<Page<Row>>("/finance/incoming-payments/", { params: { page_size: 5 } }),
    enabled: can("finance.reconciliation.view"),
  });

  const firstName = session?.user?.name.split(" ")[0] ?? "";

  return (
    <Stack gap="lg">
      <WorkspaceHeader
        eyebrow={session?.active_tenant?.name ?? "School Workspace"}
        title={`Good day, ${firstName}`}
        description="Here is the live operating picture for your school workspace."
        action={
          <Button variant="default" size="xs" leftSection={<IconRefresh size={14} />} onClick={() => location.reload()}>
            Refresh workspace
          </Button>
        }
      />

      <SimpleGrid cols={{ base: 1, sm: 2, md: 4 }} spacing="md">
        <MetricCard label="Students" value={students.data?.count ?? "—"} meta="Enrolled in campus scope" tone="blue" icon={IconUsers} />
        <MetricCard label="Invoices" value={invoices.data?.count ?? "—"} meta="Billing records issued" tone="teal" icon={IconFileText} />
        <MetricCard label="Payments" value={payments.data?.count ?? "—"} meta="Recorded fee receipts" tone="violet" icon={IconWallet} />
        <MetricCard label="Needs Matching" value={incoming.data?.count ?? "—"} meta="Incoming payment queue" tone="yellow" icon={IconInbox} />
      </SimpleGrid>

      <Grid>
        <Grid.Col span={{ base: 12, md: 7 }}>
          <Paper p="md" radius="md" withBorder h="100%">
            <Group justify="space-between" mb="sm">
              <Box>
                <Title order={3} size="h4" fw={700}>Recent Invoices</Title>
                <Text size="xs" c="dimmed">Latest billing activity</Text>
              </Box>
              <Button variant="subtle" size="xs" color="indigo" rightSection={<IconArrowRight size={14} />} onClick={() => go("/finance/invoices")}>
                View all
              </Button>
            </Group>
            <MiniRows rows={invoices.data?.results ?? []} primary="invoice_number" secondary="status" value="total" />
          </Paper>
        </Grid.Col>

        <Grid.Col span={{ base: 12, md: 5 }}>
          <Paper p="md" radius="md" withBorder h="100%">
            <Box mb="sm">
              <Title order={3} size="h4" fw={700}>Action Queue</Title>
              <Text size="xs" c="dimmed">Operational tasks needing attention</Text>
            </Box>
            <Stack gap="xs">
              <UnstyledButton onClick={() => go("/finance/incoming")} style={{ borderRadius: 8, padding: 10, border: "1px solid var(--mantine-color-gray-2)", transition: "background 150ms ease" }}>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap="sm" wrap="nowrap">
                    <ThemeIcon size={34} radius="md" color="yellow" variant="light">
                      <IconInbox size={18} />
                    </ThemeIcon>
                    <Box>
                      <Text size="sm" fw={600}>Incoming Payments</Text>
                      <Text size="xs" c="dimmed">Review and match incoming receipts</Text>
                    </Box>
                  </Group>
                  <Badge color="yellow" variant="light">{incoming.data?.count ?? "—"}</Badge>
                </Group>
              </UnstyledButton>

              <UnstyledButton onClick={() => go("/communications/inbox")} style={{ borderRadius: 8, padding: 10, border: "1px solid var(--mantine-color-gray-2)" }}>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap="sm" wrap="nowrap">
                    <ThemeIcon size={34} radius="md" color="indigo" variant="light">
                      <IconBell size={18} />
                    </ThemeIcon>
                    <Box>
                      <Text size="sm" fw={600}>Notifications</Text>
                      <Text size="xs" c="dimmed">Open your personal inbox</Text>
                    </Box>
                  </Group>
                  <IconArrowRight size={16} color="var(--mantine-color-dimmed)" />
                </Group>
              </UnstyledButton>

              <UnstyledButton onClick={() => go("/leave")} style={{ borderRadius: 8, padding: 10, border: "1px solid var(--mantine-color-gray-2)" }}>
                <Group justify="space-between" wrap="nowrap">
                  <Group gap="sm" wrap="nowrap">
                    <ThemeIcon size={34} radius="md" color="teal" variant="light">
                      <IconCalendar size={18} />
                    </ThemeIcon>
                    <Box>
                      <Text size="sm" fw={600}>Leave Requests</Text>
                      <Text size="xs" c="dimmed">Review staff leave submissions</Text>
                    </Box>
                  </Group>
                  <IconArrowRight size={16} color="var(--mantine-color-dimmed)" />
                </Group>
              </UnstyledButton>
            </Stack>
          </Paper>
        </Grid.Col>
      </Grid>
    </Stack>
  );
}

function MiniRows({ rows, primary, secondary, value }: { rows: Row[]; primary: string; secondary: string; value: string }) {
  if (!rows.length) return <Empty title="No recent billing activity" message="New invoices will appear here." />;
  return (
    <Stack gap={0}>
      {rows.slice(0, 5).map((row, i) => (
        <Group key={String(row.id ?? i)} justify="space-between" py="xs" style={{ borderBottom: i < 4 ? "1px solid var(--mantine-color-gray-1)" : undefined }}>
          <Box>
            <Text size="sm" fw={600}>{display(row[primary])}</Text>
            <Box mt={2}>{display(row[secondary])}</Box>
          </Box>
          <Text size="sm" fw={700}>
            {value === "total" ? money.format(Number(row[value] ?? 0)) : display(row[value])}
          </Text>
        </Group>
      ))}
    </Stack>
  );
}


export type ResourceConfig={title:string;description:string;endpoint:string;permission?:string;columns:string[];eyebrow?:string;actionLabel?:string};
export function ResourcePage({config}:{config:ResourceConfig}){const [page,setPage]=useState(1);const [search,setSearch]=useState("");const query=useQuery({queryKey:[config.endpoint,page,search],queryFn:()=>api<Page<Row>|Row[]>(config.endpoint,{params:{page,search:search||undefined}})});const data=Array.isArray(query.data)?query.data:query.data?.results??[];const count=Array.isArray(query.data)?query.data.length:query.data?.count??0;return <><PageHeader eyebrow={config.eyebrow} title={config.title} description={config.description} action={config.actionLabel?<button className="button primary">+ {config.actionLabel}</button>:undefined}/><section className="card data-card"><div className="table-tools"><label className="search-field"><Icon name="search"/><input placeholder={`Search ${config.title.toLowerCase()}`} value={search} onChange={e=>{setSearch(e.target.value);setPage(1)}}/></label><span>{count.toLocaleString()} records</span></div>{query.isLoading?<Loading/>:query.isError?<ErrorState error={query.error} retry={()=>void query.refetch()}/>:!data.length?<Empty/>:<div className="table-scroll"><table><thead><tr>{config.columns.map(column=><th key={column}>{pretty(column)}</th>)}</tr></thead><tbody>{data.map((row,index)=><tr key={String(row.id??index)}>{config.columns.map(column=><td key={column}>{display(row[column])}</td>)}</tr>)}</tbody></table></div>} {!Array.isArray(query.data)&&query.data&&<div className="pagination"><button disabled={!query.data.previous} onClick={()=>setPage(p=>p-1)}>Previous</button><span>Page {page}</span><button disabled={!query.data.next} onClick={()=>setPage(p=>p+1)}>Next</button></div>}</section></>}

export function StudentsPage(){
 const [page,setPage]=useState(1);
 const query=useQuery({queryKey:["students",page],queryFn:()=>api<Page<Row>>("/students/",{params:{page}})});
 const columns:Column<Row>[]=[
  {key:"admission_number",header:"Admission No.",cell:r=>display(r.admission_number)},
  {key:"full_name",header:"Student",cell:r=>display(r.full_name)},
  {key:"campus",header:"Campus",cell:r=>display(r.campus)},
  {key:"status",header:"Status",cell:r=>display(r.status)},
 ];
 return <>
  <WorkspaceHeader title="Student Directory" description="Manage enrolled students and open their complete student record."/>
  <DataTable title="Students" columns={columns} rows={query.data?.results??[]} rowKey={r=>String(r.id)}
   loading={query.isLoading} error={query.error} retry={()=>query.refetch()} onRefresh={()=>query.refetch()}
   onRow={student=>{sessionStorage.setItem(`student:${student.id}`,JSON.stringify(student));go(`/students/${student.id}`)}}
   page={page} count={query.data?.count} previous={Boolean(query.data?.previous)} next={Boolean(query.data?.next)} onPage={setPage}/>
 </>;
}

type StudentFinanceSummary={
	summary:{outstanding_balance:string;total_invoiced:string;total_credited:string;total_paid:string;unapplied_cash:string};
	recent_invoices:{id:string;invoice_number:string;status:string;total:string;issued_at:string|null;created_at:string}[];
	recent_payments:{id:string;payment_method:string;amount:string;status:string;received_at:string;receipt:{receipt_number:string}|null;unallocated_amount:string}[];
	recent_ledger_entries:{id:string;entry_type:string;amount:string;posted_at:string}[];
};
type FeeStatementRow={entry_date:string;description:string;debit:string;credit:string;running_balance:string};
function FeeStatementDialog({studentId,open,onClose}:{studentId:string;open:boolean;onClose:()=>void}){
	const [asOf,setAsOf]=useState(()=>new Date().toISOString().slice(0,10));
	const [page,setPage]=useState(1);
	const statement=useQuery({
		queryKey:["fee-statement",studentId,asOf,page],
		queryFn:()=>api<{columns:[string,string][];rows:FeeStatementRow[];has_more:boolean}>("/reports/finance.fee_statement/preview/",{params:{student_id:studentId,as_of:asOf,page,page_size:25}}),
		enabled:open,
	});
	return <ActionDialog open={open} title="Fee statement" description="A running balance of every invoice, credit note, payment allocation and reversal posted for this student, as of the chosen date." confirmLabel="Close" onClose={onClose} onSubmit={e=>{e.preventDefault();onClose()}}>
		<Stack gap="sm">
			<TextInput type="date" label="As of" value={asOf} onChange={e=>{setAsOf(e.currentTarget.value);setPage(1)}}/>
			{statement.isLoading?<Loading label="Loading statement"/>:statement.isError?<ErrorState error={statement.error} retry={()=>void statement.refetch()}/>:
				!statement.data?.rows.length?<Empty title="No activity" message="No ledger activity has posted for this student as of this date."/>:
				<Stack gap="xs">
					<Table.ScrollContainer minWidth={480}>
						<Table withTableBorder verticalSpacing="xs">
							<Table.Thead><Table.Tr><Table.Th>Date</Table.Th><Table.Th>Description</Table.Th><Table.Th>Debit</Table.Th><Table.Th>Credit</Table.Th><Table.Th>Balance</Table.Th></Table.Tr></Table.Thead>
							<Table.Tbody>{statement.data.rows.map((row,index)=><Table.Tr key={index}><Table.Td>{whenDate(row.entry_date)}</Table.Td><Table.Td>{row.description||"—"}</Table.Td><Table.Td>{Number(row.debit)?kes.format(Number(row.debit)):"—"}</Table.Td><Table.Td>{Number(row.credit)?kes.format(Number(row.credit)):"—"}</Table.Td><Table.Td fw={600}>{kes.format(Number(row.running_balance))}</Table.Td></Table.Tr>)}</Table.Tbody>
						</Table>
					</Table.ScrollContainer>
					<Group justify="flex-end">
						<Button size="xs" variant="default" disabled={page<=1} onClick={()=>setPage(p=>p-1)}>Previous</Button>
						<Text size="xs" c="dimmed">Page {page}</Text>
						<Button size="xs" variant="default" disabled={!statement.data.has_more} onClick={()=>setPage(p=>p+1)}>Next</Button>
					</Group>
				</Stack>}
		</Stack>
	</ActionDialog>;
}
function StudentFinancePanel({studentId,query}:{studentId:string;query:{isLoading:boolean;isError:boolean;error:unknown;data?:StudentFinanceSummary}}){
	const {can}=useAccess();
	const paymentMethods=useQuery({queryKey:["payment-methods-lookup"],queryFn:()=>api<Page<{id:string;name:string}>>("/finance/payment-methods/",{params:{page_size:100}}),enabled:!!query.data});
	const methodName=(id:string)=>paymentMethods.data?.results.find(m=>m.id===id)?.name??id;
	const [statementOpen,setStatementOpen]=useState(false);
	if(query.isLoading)return <Loading label="Loading finance summary"/>;
	if(query.isError)return <ErrorState error={query.error}/>;
	if(!query.data)return <Empty/>;
	const data=query.data;
	return <Stack gap="lg">
		<KeyValueSection title="Balance" action={can("reports.finance.view")?<Button size="xs" variant="default" onClick={()=>setStatementOpen(true)}>Fee statement</Button>:undefined}>
			<KeyValueGrid>
				<KeyValueItem label="Outstanding balance" value={kes.format(Number(data.summary.outstanding_balance))}/>
				<KeyValueItem label="Total invoiced" value={kes.format(Number(data.summary.total_invoiced))}/>
				<KeyValueItem label="Total credited" value={kes.format(Number(data.summary.total_credited))}/>
				<KeyValueItem label="Total paid" value={kes.format(Number(data.summary.total_paid))}/>
				<KeyValueItem label="Unapplied cash" value={kes.format(Number(data.summary.unapplied_cash))}/>
			</KeyValueGrid>
		</KeyValueSection>
		<FeeStatementDialog studentId={studentId} open={statementOpen} onClose={()=>setStatementOpen(false)}/>
		<Box>
			<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={6}>Recent invoices</Text>
			{!data.recent_invoices.length?<Text size="sm" c="dimmed">No invoices yet.</Text>:
				<Table withTableBorder verticalSpacing="xs">
					<Table.Thead><Table.Tr><Table.Th>Invoice</Table.Th><Table.Th>Status</Table.Th><Table.Th>Total</Table.Th><Table.Th>Issued</Table.Th></Table.Tr></Table.Thead>
					<Table.Tbody>{data.recent_invoices.map(row=><Table.Tr key={row.id}><Table.Td ff="monospace">{row.invoice_number}</Table.Td><Table.Td><StatusBadge value={row.status}/></Table.Td><Table.Td>{kes.format(Number(row.total))}</Table.Td><Table.Td>{whenDateTime(row.issued_at)}</Table.Td></Table.Tr>)}</Table.Tbody>
				</Table>}
		</Box>
		<Box>
			<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={6}>Recent payments</Text>
			{!data.recent_payments.length?<Text size="sm" c="dimmed">No payments yet.</Text>:
				<Table withTableBorder verticalSpacing="xs">
					<Table.Thead><Table.Tr><Table.Th>Method</Table.Th><Table.Th>Status</Table.Th><Table.Th>Amount</Table.Th><Table.Th>Unallocated</Table.Th><Table.Th>Received</Table.Th><Table.Th>Receipt</Table.Th></Table.Tr></Table.Thead>
					<Table.Tbody>{data.recent_payments.map(row=><Table.Tr key={row.id}><Table.Td>{methodName(row.payment_method)}</Table.Td><Table.Td><StatusBadge value={row.status}/></Table.Td><Table.Td>{kes.format(Number(row.amount))}</Table.Td><Table.Td>{kes.format(Number(row.unallocated_amount))}</Table.Td><Table.Td>{whenDateTime(row.received_at)}</Table.Td><Table.Td ff="monospace">{row.receipt?.receipt_number??"—"}</Table.Td></Table.Tr>)}</Table.Tbody>
				</Table>}
		</Box>
		<Box>
			<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={6}>Recent ledger entries</Text>
			{!data.recent_ledger_entries.length?<Text size="sm" c="dimmed">No ledger entries yet.</Text>:
				<Table withTableBorder verticalSpacing="xs">
					<Table.Thead><Table.Tr><Table.Th>Type</Table.Th><Table.Th>Amount</Table.Th><Table.Th>Posted</Table.Th></Table.Tr></Table.Thead>
					<Table.Tbody>{data.recent_ledger_entries.map(row=><Table.Tr key={row.id}><Table.Td><StatusBadge value={row.entry_type}/></Table.Td><Table.Td>{kes.format(Number(row.amount))}</Table.Td><Table.Td>{whenDateTime(row.posted_at)}</Table.Td></Table.Tr>)}</Table.Tbody>
				</Table>}
		</Box>
	</Stack>;
}

type StudentAttendanceSummary={
	window_days:number;
	status_counts:Record<string,number>;
	marked_sessions:number;
	recent_records:{id:string;session_date:string;status:string;remarks:string}[];
};
function StudentAttendancePanel({query}:{query:{isLoading:boolean;isError:boolean;error:unknown;data?:StudentAttendanceSummary}}){
	if(query.isLoading)return <Loading label="Loading attendance summary"/>;
	if(query.isError)return <ErrorState error={query.error}/>;
	if(!query.data)return <Empty/>;
	const data=query.data;
	return <Stack gap="lg">
		<KeyValueSection title={`Last ${data.window_days} days`}>
			<KeyValueGrid>
				{Object.entries(data.status_counts).map(([status,count])=><KeyValueItem key={status} label={pretty(status.toLowerCase())} value={count}/>)}
				<KeyValueItem label="Marked sessions" value={data.marked_sessions}/>
			</KeyValueGrid>
		</KeyValueSection>
		<Box>
			<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={6}>Recent records</Text>
			{!data.recent_records.length?<Text size="sm" c="dimmed">No attendance recorded yet.</Text>:
				<Table withTableBorder verticalSpacing="xs">
					<Table.Thead><Table.Tr><Table.Th>Date</Table.Th><Table.Th>Status</Table.Th><Table.Th>Remarks</Table.Th></Table.Tr></Table.Thead>
					<Table.Tbody>{data.recent_records.map(row=><Table.Tr key={row.id}><Table.Td>{whenDate(row.session_date)}</Table.Td><Table.Td><StatusBadge value={row.status}/></Table.Td><Table.Td>{row.remarks||"—"}</Table.Td></Table.Tr>)}</Table.Tbody>
				</Table>}
		</Box>
	</Stack>;
}

type StudentAssessmentSummary={
	window_days:number;
	mark_status_counts:Record<string,number>;
	average_percentage:string|null;
	recent_results:{id:string;scheduled_date:string;assessment_name:string;mark_status:string;score:string|null;grade:string;remarks:string}[];
};
function StudentAssessmentsPanel({query}:{query:{isLoading:boolean;isError:boolean;error:unknown;data?:StudentAssessmentSummary}}){
	if(query.isLoading)return <Loading label="Loading assessment summary"/>;
	if(query.isError)return <ErrorState error={query.error}/>;
	if(!query.data)return <Empty/>;
	const data=query.data;
	return <Stack gap="lg">
		<KeyValueSection title={`Last ${data.window_days} days`}>
			<KeyValueGrid>
				{Object.entries(data.mark_status_counts).map(([status,count])=><KeyValueItem key={status} label={pretty(status.toLowerCase())} value={count}/>)}
				<KeyValueItem label="Average score" value={data.average_percentage?`${Number(data.average_percentage).toFixed(1)}%`:"—"}/>
			</KeyValueGrid>
		</KeyValueSection>
		<Box>
			<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={6}>Recent results</Text>
			{!data.recent_results.length?<Text size="sm" c="dimmed">No scored assessments yet.</Text>:
				<Table withTableBorder verticalSpacing="xs">
					<Table.Thead><Table.Tr><Table.Th>Date</Table.Th><Table.Th>Assessment</Table.Th><Table.Th>Score</Table.Th><Table.Th>Grade</Table.Th><Table.Th>Remarks</Table.Th></Table.Tr></Table.Thead>
					<Table.Tbody>{data.recent_results.map(row=><Table.Tr key={row.id}><Table.Td>{whenDate(row.scheduled_date)}</Table.Td><Table.Td>{row.assessment_name}</Table.Td><Table.Td>{row.score??"—"}</Table.Td><Table.Td>{row.grade||"—"}</Table.Td><Table.Td>{row.remarks||"—"}</Table.Td></Table.Tr>)}</Table.Tbody>
				</Table>}
		</Box>
	</Stack>;
}

export function StudentPage({id}:{id:string}){const student=JSON.parse(sessionStorage.getItem(`student:${id}`)??"{}") as Row;const {can,moduleEnabled}=useAccess();const tabs=["Overview",...(moduleEnabled("finance")&&can("finance.student_account.view")?["Fees"]:[]),...(moduleEnabled("attendance")&&can("attendance.record.view")?["Attendance"]:[]),...(moduleEnabled("assessments")&&can("assessment.record.view")?["Assessments"]:[]),...(moduleEnabled("documents")&&can("students.document.view")?["Documents"]:[]),"Guardians","Activity"];const [tab,setTab]=useState("Overview");const finance=useQuery({queryKey:["student-finance",id],queryFn:()=>api<StudentFinanceSummary>(`/finance/students/${id}/finance/`),enabled:tab==="Fees"});const attendance=useQuery({queryKey:["student-attendance",id],queryFn:()=>api<StudentAttendanceSummary>(`/attendance/students/${id}/summary/`),enabled:tab==="Attendance"});const assessments=useQuery({queryKey:["student-assessment",id],queryFn:()=>api<StudentAssessmentSummary>(`/assessments/students/${id}/summary/`),enabled:tab==="Assessments"});return <>
 <RecordHeader backLabel="Student directory" onBack={()=>go("/students")}
  initials={String(student.full_name??"ST").split(" ").map(x=>x[0]).join("").slice(0,2).toUpperCase()}
  eyebrow={String(student.admission_number??"Student record")} title={String(student.full_name??"Student")}
  subtitle={String(student.campus??"Campus not assigned")} status={String(student.status??"Active")}/>
 <RecordTabs tabs={tabs} value={tab} onChange={setTab}/>
 <Paper p="lg">
  {tab==="Overview"&&<KeyValueSection title="Overview"><KeyValueGrid><KeyValueItem label="Admission number" value={display(student.admission_number)}/><KeyValueItem label="Campus" value={display(student.campus)}/><KeyValueItem label="Current status" value={display(student.status)}/></KeyValueGrid></KeyValueSection>}
  {tab==="Fees"&&<StudentFinancePanel studentId={id} query={finance}/>}
  {tab==="Documents"&&<DocumentsPanel basePath="/students" ownerId={id} viewPermission="students.document.view" managePermission="students.document.manage" can={can}/>}
  {tab==="Attendance"&&<StudentAttendancePanel query={attendance}/>}
  {tab==="Assessments"&&<StudentAssessmentsPanel query={assessments}/>}
  {(tab==="Guardians"||tab==="Activity")&&<Empty title={`${tab} is not available yet`} message={`The backend does not currently expose a ${tab.toLowerCase()} endpoint.`}/>}
 </Paper>
</>}

export function StaffPage(){const query=useQuery({queryKey:["employees"],queryFn:()=>api<Page<Row>>("/staff/employees/")});return <><PageHeader eyebrow="Staff & HR" title="Employees" description="Open an employee record to manage employment, qualifications, documents and leave."/><section className="card data-card">{query.isLoading?<Loading/>:query.isError?<ErrorState error={query.error}/>:!query.data?.results.length?<Empty/>:<div className="student-cards">{query.data.results.map(employee=><button key={String(employee.id)} onClick={()=>go(`/staff/${employee.id}`)}><span className="student-avatar">{`${String(employee.first_name??"").slice(0,1)}${String(employee.last_name??"").slice(0,1)}`}</span><span><strong>{String(employee.first_name??"")} {String(employee.last_name??"")}</strong><small>{display(employee.employee_number)} · {display(employee.job_title)}</small></span><StatusBadge value={String(employee.status??"Active")}/><b>›</b></button>)}</div>}</section></>}

export function EmployeePage({id}:{id:string}){const {can}=useAccess();const detail=useQuery({queryKey:["employee",id],queryFn:()=>api<Row>(`/staff/employees/${id}/`)});const tabs=["Overview","Qualifications","Documents",...(can("leave.request.view")||can("leave.balance.adjust")?["Leave"]:[]),...(can("staff.user_link.manage")?["User access"]:[])];const [tab,setTab]=useState("Overview");const qualifications=useQuery({queryKey:["employee-qualifications",id],queryFn:()=>api<Page<Row>|Row[]>(`/staff/employees/${id}/qualifications/`),enabled:tab==="Qualifications"});if(detail.isLoading)return <Loading label="Opening employee record"/>;if(detail.isError)return <ErrorState error={detail.error}/>;const employee=detail.data??{};return <>
 <RecordHeader backLabel="Employee directory" onBack={()=>go("/staff")}
  initials={`${String(employee.first_name??"").slice(0,1)}${String(employee.last_name??"").slice(0,1)}`.toUpperCase()}
  eyebrow={String(employee.employee_number??"Employee record")} title={`${String(employee.first_name??"")} ${String(employee.last_name??"")}`}
  subtitle={`${String(employee.job_title??"Job title not assigned")} · ${String(employee.department??"No department")}`} status={String(employee.status??"Active")}/>
 <RecordTabs tabs={tabs} value={tab} onChange={setTab}/>
 <Paper p="lg">
  {tab==="Overview"&&<KeyValueSection title="Overview"><KeyValueGrid>{["employment_type","hire_date","campus","email","phone_number","user_account"].map(key=><KeyValueItem key={key} label={pretty(key)} value={display(employee[key])}/>)}</KeyValueGrid></KeyValueSection>}
  {tab==="Qualifications"&&<JsonPanel query={qualifications}/>}
  {tab==="Documents"&&<DocumentsPanel basePath="/staff/employees" ownerId={id} viewPermission="staff.view" managePermission="staff.manage" can={can}/>}
  {tab==="Leave"&&<LeavePanel employeeId={id}/>}
  {tab==="User access"&&<KeyValueSection title="User access"><KeyValueGrid><KeyValueItem label="Linked account" value={display(employee.user_account)}/><KeyValueItem label="Account management" value="Available to authorized administrators"/></KeyValueGrid></KeyValueSection>}
 </Paper>
</>}
type LeaveTypeOption={id:string;name:string};
type LeaveBalanceEntry={id:string;entry_type:string;days:number;reason:string;created_at:string};
type LeaveBalance={balance:number;entries:LeaveBalanceEntry[]};
function LeavePanel({employeeId}:{employeeId:string}){
	const [leaveTypeId,setLeaveTypeId]=useState("");
	const [year,setYear]=useState<number|"">(new Date().getFullYear());
	const leaveTypes=useQuery({queryKey:["leave-types-for-balance"],queryFn:()=>api<Page<LeaveTypeOption>>("/leave/types/",{params:{page_size:100}})});
	const balance=useQuery({
		queryKey:["employee-leave-balance",employeeId,leaveTypeId,year],
		queryFn:()=>api<LeaveBalance>(`/leave/employees/${employeeId}/balance/`,{params:{leave_type:leaveTypeId,year}}),
		enabled:!!leaveTypeId&&year!=="",
	});
	return <Stack gap="md">
		<Group gap="sm" align="end">
			<Select label="Leave type" placeholder={leaveTypes.isLoading?"Loading…":"Select leave type"} disabled={leaveTypes.isLoading}
				data={(leaveTypes.data?.results??[]).map(t=>({value:t.id,label:t.name}))} value={leaveTypeId||null} onChange={value=>setLeaveTypeId(value??"")}/>
			<NumberInput label="Year" w={120} value={year} onChange={value=>setYear(value===""||value===undefined?"":Number(value))}/>
		</Group>
		{leaveTypes.isError&&<Alert color="red" variant="light">{leaveTypes.error instanceof Error?leaveTypes.error.message:"Leave types could not be loaded"}</Alert>}
		{!leaveTypeId?<Text size="sm" c="dimmed">Select a leave type to view the balance.</Text>:
			balance.isLoading?<Loading label="Loading balance"/>:balance.isError?<ErrorState error={balance.error}/>:<>
				<Text size="sm">Balance: <Text span fw={700}>{balance.data?.balance} days</Text></Text>
				{!balance.data?.entries.length?<Empty title="No ledger entries" message="No adjustments recorded for this leave type and year."/>:
					<Table withTableBorder verticalSpacing="xs">
						<Table.Thead><Table.Tr><Table.Th>Type</Table.Th><Table.Th>Days</Table.Th><Table.Th>Reason</Table.Th><Table.Th>Date</Table.Th></Table.Tr></Table.Thead>
						<Table.Tbody>{balance.data!.entries.map(e=><Table.Tr key={e.id}><Table.Td>{e.entry_type}</Table.Td><Table.Td>{e.days}</Table.Td><Table.Td>{e.reason||"—"}</Table.Td><Table.Td>{new Date(e.created_at).toLocaleDateString()}</Table.Td></Table.Tr>)}</Table.Tbody>
					</Table>}
			</>}
	</Stack>;
}
function Info({label,value}:{label:string;value:unknown}){return <div className="info-block"><small>{label}</small><strong>{display(value)}</strong></div>}
function JsonPanel({query}:{query:{isLoading:boolean;isError:boolean;error:unknown;data?:unknown}}){if(query.isLoading)return <Loading/>;if(query.isError)return <ErrorState error={query.error}/>;if(!query.data)return <Empty/>;const value=query.data as Row;const entries=Array.isArray(value)?value:Object.entries(value);return <div className="overview-grid">{Array.isArray(entries)&&entries.slice(0,12).map((entry,index)=>Array.isArray(entry)?<Info key={entry[0]} label={pretty(entry[0])} value={entry[1]}/>:<Info key={index} label={`Record ${index+1}`} value={entry}/>)}</div>}

export function SetupPage(){const {can}=useAccess();const setups=[{name:"Finance",endpoint:"/finance/setup/",permission:"finance.setup.view"},{name:"Leave",endpoint:"/leave/setup/",permission:"leave.setup.view"},{name:"Documents",endpoint:"/documents/setup/",permission:"documents.setup.view",managePermission:"documents.setup.manage"}].filter(x=>can(x.permission));return <><PageHeader eyebrow="Administration" title="Module setup" description="Configuration is grouped by operational domain. Communications setup has moved to Communication → Providers & channels."/><div className="setup-grid">{setups.map(x=><SetupCard key={x.name} {...x}/>)}</div></>}
function SetupCard({name,endpoint,managePermission}:{name:string;endpoint:string;managePermission?:string}){
	const {can}=useAccess();
	const qc=useQueryClient();
	const q=useQuery({queryKey:[endpoint],queryFn:()=>api<Row>(endpoint)});
	const [editOpen,setEditOpen]=useState(false);
	const [retentionDays,setRetentionDays]=useState<number|"">("");
	const canManage=!!managePermission&&can(managePermission);
	const save=useMutation({
		mutationFn:()=>api<Row>(endpoint,{method:"PATCH",body:JSON.stringify({default_retention_days:retentionDays===""?null:retentionDays})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:[endpoint]});setEditOpen(false);notify.success(`${name} setup updated`)},
		onError:error=>notify.error(`${name} setup could not be updated`,error),
	});
	return <section className="card setup-card">
		<div className="card-heading"><div><h2>{name}</h2><p>Current configuration</p></div>{canManage&&<button className="button secondary" onClick={()=>{setRetentionDays((q.data?.default_retention_days as number|null)??"");setEditOpen(true)}}>Edit</button>}</div>
		{q.isLoading?<Loading label="Loading setup"/>:q.isError?<ErrorState error={q.error}/>:<div className="config-list">{Object.entries(q.data??{}).slice(0,6).map(([k,v])=><div key={k}><span>{pretty(k)}</span><strong>{display(v)}</strong></div>)}</div>}
		{canManage&&<ActionDialog open={editOpen} title={`Edit ${name.toLowerCase()} setup`} confirmLabel="Save" busy={save.isPending} onClose={()=>setEditOpen(false)} onSubmit={e=>{e.preventDefault();save.mutate()}}>
			<Stack gap="sm">
				<NumberInput label="Default retention (days)" description="Leave blank for no automatic expiry." min={1} value={retentionDays} onChange={value=>setRetentionDays(value===""?"":Number(value))}/>
			</Stack>
		</ActionDialog>}
	</section>;
}

export function PlatformHome(){const modules=useQuery({queryKey:["platform-modules"],queryFn:()=>api<Row[]>("/platform/modules/",{tenant:false})});const [form,setForm]=useState({name:"",slug:"",admin_email:""});const [result,setResult]=useState("");async function provision(e:FormEvent){e.preventDefault();try{const data=await api<Row>("/platform/tenants/",{method:"POST",tenant:false,body:JSON.stringify(form)});setResult(`Created ${String(data.name)} (${String(data.slug)})`);setForm({name:"",slug:"",admin_email:""})}catch(e){setResult(e instanceof Error?e.message:"Provisioning failed")}}return <><PageHeader eyebrow="Platform administration" title="Tenant operations" description="Provision schools and control the modules available to each tenant."/><div className="platform-grid"><section className="card panel-card"><div className="card-heading"><div><h2>Provision a school</h2><p>Creates the tenant and first administrator invitation.</p></div></div><form className="form-grid" onSubmit={provision}><label>School name<input value={form.name} onChange={e=>setForm({...form,name:e.target.value})} required/></label><label>Tenant slug<input value={form.slug} onChange={e=>setForm({...form,slug:e.target.value.toLowerCase().replace(/[^a-z0-9-]/g,"-")})} required/></label><label className="span-two">First administrator email<input type="email" value={form.admin_email} onChange={e=>setForm({...form,admin_email:e.target.value})} required/></label><div className="span-two form-actions">{result&&<p>{result}</p>}<button className="button platform-button">Provision school</button></div></form></section><section className="card panel-card"><div className="card-heading"><div><h2>Module catalogue</h2><p>Capabilities available to subscription plans.</p></div></div>{modules.isLoading?<Loading/>:modules.isError?<ErrorState error={modules.error}/>:<div className="module-list">{(Array.isArray(modules.data)?modules.data:[]).map(module=><div key={String(module.code)}><span className="metric-icon violet"><Icon name="layers"/></span><span><strong>{display(module.label)}</strong><small>{display(module.code)}</small></span></div>)}</div>}</section></div></>}

export const resources:Record<string,ResourceConfig>={
 "/finance/fees":{eyebrow:"Finance · Setup",title:"Fee structures",description:"Approved charging schedules by academic year and level.",endpoint:"/finance/fee-structures/",columns:["name","academic_year","academic_level","is_approved","created_at"]},
 "/finance/invoices":{eyebrow:"Finance · Billing",title:"Invoices",description:"Issued and draft student charges.",endpoint:"/finance/invoices/",columns:["invoice_number","student","status","total","issued_at"]},
 "/finance/payments":{eyebrow:"Finance · Collections",title:"Payments",description:"Recorded receipts and allocation status.",endpoint:"/finance/payments/",columns:["receipt_number","student","amount","unallocated_amount","received_at"]},
 "/finance/incoming":{eyebrow:"Finance · Reconciliation",title:"Incoming payments",description:"Review money received before it reaches a student account.",endpoint:"/finance/incoming-payments/",columns:["provider","external_reference","payer_phone","amount","status","received_at"]},
 "/finance/mpesa":{eyebrow:"Finance · M-Pesa",title:"Callback inbox",description:"Verify and process provider callbacks with a complete audit trail.",endpoint:"/finance/mpesa/callbacks/",columns:["event_type","status","merchant_request_id","checkout_request_id","received_at"]},
 "/attendance":{eyebrow:"Academics",title:"Attendance sessions",description:"Open registers, record attendance and submit completed sessions.",endpoint:"/attendance/sessions/",columns:["attendance_date","class_group","status","opened_by","submitted_at"]},
 "/assessments":{eyebrow:"Academics",title:"Assessments",description:"Marks entry, review, approval and publication.",endpoint:"/assessments/assessments/",columns:["name","class_group","subject","status","assessment_date"]},
 "/timetable":{eyebrow:"Academics",title:"Timetable",description:"Class, teacher and room schedules for the active term.",endpoint:"/timetable/entries/",columns:["day_of_week","period","class_group","subject","teacher","room"]},
 "/staff":{eyebrow:"Staff & HR",title:"Employees",description:"Employment records within your campus scope.",endpoint:"/staff/employees/",columns:["employee_number","full_name","job_title","department","status"]},
 "/leave":{eyebrow:"Staff & HR",title:"Leave requests",description:"Employee requests and approval workflow.",endpoint:"/leave/requests/",columns:["employee","leave_type","starts_on","ends_on","status"]},
 "/administration/users":{eyebrow:"Administration",title:"Users",description:"Tenant memberships, roles and campus scope.",endpoint:"/tenancy/memberships/",columns:["user","role","campus","is_active","joined_at"]},
 "/administration/roles":{eyebrow:"Administration",title:"Roles & permissions",description:"Define the actions each school role can perform.",endpoint:"/tenancy/roles/",columns:["name","permissions"]},
 "/platform/plans":{eyebrow:"Platform administration",title:"Subscription plans",description:"Module bundles assigned to schools.",endpoint:"/platform/plans/",columns:["name","module_codes","is_default","is_active","created_at"]},
 "/platform/audit":{eyebrow:"Platform administration",title:"Platform audit trail",description:"Recorded cross-tenant administrative actions.",endpoint:"/platform/audit-events/",columns:["actor","action","resource_type","resource_id","created_at"]},
};

export function MissingPage(){return <><PageHeader title="Page not found" description="This workspace does not exist or is not available to your account."/><div className="card"><Empty title="Nothing at this address" message="Use the navigation to return to an available module."/></div></>}
