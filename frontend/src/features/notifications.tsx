import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Badge, Button, Card, Group, PasswordInput, Select, Stack, Switch, Text, Textarea, TextInput } from "@mantine/core";
import { IconAlertCircle } from "@tabler/icons-react";
import { api, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { DetailDrawer } from "../components/DetailDrawer";
import { notify } from "../components/notifications/notify";
import { Empty, Loading, PageHeader, StatusBadge } from "../components/ui";

type Channel = "SMS" | "EMAIL" | "IN_APP";
const CHANNELS: Channel[] = ["SMS", "EMAIL", "IN_APP"];
const channelLabel = (channel: string) => channel.replace("_", "-");

function Failure({ error }: { error: unknown }) {
	return error ? <Alert color="red" variant="light" icon={<IconAlertCircle size={16} />}>{error instanceof Error ? error.message : "The action failed"}</Alert> : null;
}

// --- My notifications ---------------------------------------------------

type UserNotification = { id: string; title: string; message: string; resource_type: string; resource_id: string; read_at: string | null; created_at: string };

export function NotificationInboxPage() {
	const qc = useQueryClient();
	const [page, setPage] = useState(1);
	const q = useQuery({ queryKey: ["notification-inbox", page], queryFn: () => api<Page<UserNotification>>("/notifications/inbox/", { params: { page } }) });
	const [selected, setSelected] = useState<UserNotification | null>(null);
	const markRead = useMutation({
		mutationFn: (id: string) => api<UserNotification>(`/notifications/inbox/${id}/read/`, { method: "POST" }),
		onSuccess: updated => { void qc.invalidateQueries({ queryKey: ["notification-inbox"] }); setSelected(updated); },
		onError: error => notify.error("Notification could not be marked as read", error),
	});
	const columns: Column<UserNotification>[] = [
		{ key: "status", header: "", cell: r => r.read_at ? null : <Badge size="xs" color="blue">New</Badge> },
		{ key: "title", header: "Notification", cell: r => <><strong>{r.title}</strong><small className="cell-sub">{r.message}</small></> },
		{ key: "created", header: "Received", cell: r => new Date(r.created_at).toLocaleString() },
	];
	return <>
		<PageHeader eyebrow="Communication" title="My notifications" description="Messages addressed to your account." />
		<DataTable columns={columns} rows={q.data?.results ?? []} rowKey={r => r.id} loading={q.isLoading} error={q.error} retry={() => void q.refetch()} count={q.data?.count} page={page} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage} onRow={setSelected} />
		<ActionDialog open={!!selected} title={selected?.title ?? "Notification"} confirmLabel={selected?.read_at ? "Close" : "Mark as read"} busy={markRead.isPending} onClose={() => setSelected(null)} onSubmit={e => { e.preventDefault(); selected?.read_at ? setSelected(null) : markRead.mutate(selected!.id); }}>
			<Text size="sm">{selected?.message}</Text>
		</ActionDialog>
	</>;
}

// --- Templates ------------------------------------------------------------

type Template = { id: string; code: string; name: string; channel: Channel; subject: string; body: string; is_active: boolean };

export function NotificationTemplatesPage() {
	const { can } = useAccess();
	const qc = useQueryClient();
	const [page, setPage] = useState(1);
	const q = useQuery({ queryKey: ["notification-templates", page], queryFn: () => api<Page<Template>>("/notifications/templates/", { params: { page } }) });
	const [dialog, setDialog] = useState<"create" | Template | null>(null);
	const [form, setForm] = useState({ code: "", name: "", channel: "SMS" as Channel, subject: "", body: "", is_active: true });
	const openCreate = () => { setForm({ code: "", name: "", channel: "SMS", subject: "", body: "", is_active: true }); setDialog("create"); };
	const openEdit = (template: Template) => { setForm({ code: template.code, name: template.name, channel: template.channel, subject: template.subject, body: template.body, is_active: template.is_active }); setDialog(template); };
	const create = useMutation({
		mutationFn: () => api<Template>("/notifications/templates/", { method: "POST", body: JSON.stringify(form) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-templates"] }); setDialog(null); notify.success("Template created"); },
		onError: error => notify.error("Template could not be created", error),
	});
	const update = useMutation({
		mutationFn: () => api<Template>(`/notifications/templates/${(dialog as Template).id}/`, { method: "PATCH", body: JSON.stringify({ name: form.name, subject: form.subject, body: form.body, is_active: form.is_active }) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-templates"] }); setDialog(null); notify.success("Template updated"); },
		onError: error => notify.error("Template could not be updated", error),
	});
	const isEdit = dialog !== null && dialog !== "create";
	const columns: Column<Template>[] = [
		{ key: "code", header: "Code", cell: r => <code>{r.code}</code> },
		{ key: "name", header: "Name", cell: r => r.name },
		{ key: "channel", header: "Channel", cell: r => <Badge variant="light">{channelLabel(r.channel)}</Badge> },
		{ key: "active", header: "Status", cell: r => <StatusBadge value={r.is_active} /> },
	];
	return <>
		<PageHeader eyebrow="Communication" title="Templates" description="Reusable message content bound to notification rules." action={can("notifications.templates.manage") ? <Button onClick={openCreate}>+ New template</Button> : undefined} />
		<DataTable columns={columns} rows={q.data?.results ?? []} rowKey={r => r.id} loading={q.isLoading} error={q.error} retry={() => void q.refetch()} count={q.data?.count} page={page} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage} onRow={can("notifications.templates.manage") ? openEdit : undefined} />
		<ActionDialog open={dialog === "create"} title="Create template" confirmLabel="Create template" busy={create.isPending} onClose={() => setDialog(null)} onSubmit={e => { e.preventDefault(); create.mutate(); }}>
			<Failure error={create.error} />
			<Stack gap="sm">
				<TextInput required label="Code" maxLength={80} value={form.code} onChange={e => setForm({ ...form, code: e.currentTarget.value })} />
				<TextInput required label="Name" maxLength={120} value={form.name} onChange={e => setForm({ ...form, name: e.currentTarget.value })} />
				<Select required label="Channel" data={CHANNELS.map(c => ({ value: c, label: channelLabel(c) }))} value={form.channel} onChange={value => setForm({ ...form, channel: (value as Channel) ?? form.channel })} />
				<TextInput label="Subject" maxLength={200} value={form.subject} onChange={e => setForm({ ...form, subject: e.currentTarget.value })} />
				<Textarea required label="Body" description="Use {{ variable }} placeholders — the available variables depend on which rule this template is bound to." value={form.body} onChange={e => setForm({ ...form, body: e.currentTarget.value })} />
				<Switch label="Active" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.currentTarget.checked })} />
			</Stack>
		</ActionDialog>
		<ActionDialog open={isEdit} title="Edit template" confirmLabel="Save changes" busy={update.isPending} onClose={() => setDialog(null)} onSubmit={e => { e.preventDefault(); update.mutate(); }}>
			<Failure error={update.error} />
			<Stack gap="sm">
				<TextInput label="Code" value={form.code} disabled />
				<TextInput required label="Name" maxLength={120} value={form.name} onChange={e => setForm({ ...form, name: e.currentTarget.value })} />
				<Select label="Channel" data={CHANNELS.map(c => ({ value: c, label: channelLabel(c) }))} value={form.channel} disabled />
				<TextInput label="Subject" maxLength={200} value={form.subject} onChange={e => setForm({ ...form, subject: e.currentTarget.value })} />
				<Textarea required label="Body" value={form.body} onChange={e => setForm({ ...form, body: e.currentTarget.value })} />
				<Switch label="Active" checked={form.is_active} onChange={e => setForm({ ...form, is_active: e.currentTarget.checked })} />
			</Stack>
		</ActionDialog>
	</>;
}

// --- Providers & channels ---------------------------------------------------

type Setup = { notifications_enabled: boolean; default_country_code: string };
type ChannelState = { channel: string; enabled: boolean };
type Provider = { channel: Channel; provider: string; sender_id: string; is_active: boolean; updated_at: string };

export function NotificationProvidersPage() {
	const { can } = useAccess();
	const qc = useQueryClient();
	const canManage = can("notifications.setup.manage");

	const setup = useQuery({ queryKey: ["notification-setup"], queryFn: () => api<Setup>("/notifications/setup/") });
	const [setupOpen, setSetupOpen] = useState(false);
	const [setupForm, setSetupForm] = useState({ notifications_enabled: true, default_country_code: "" });
	const saveSetup = useMutation({
		mutationFn: () => api<Setup>("/notifications/setup/", { method: "PATCH", body: JSON.stringify(setupForm) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-setup"] }); setSetupOpen(false); notify.success("Notification setup updated"); },
		onError: error => notify.error("Notification setup could not be updated", error),
	});

	const channels = useQuery({ queryKey: ["notification-channels"], queryFn: async () => { const results = await Promise.all(CHANNELS.map(channel => api<ChannelState>(`/notifications/channels/${channel}/`))); return results; } });
	const toggleChannel = useMutation({
		mutationFn: (input: { channel: Channel; enabled: boolean }) => api<ChannelState>(`/notifications/channels/${input.channel}/`, { method: "PATCH", body: JSON.stringify({ enabled: input.enabled }) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-channels"] }); notify.success("Channel updated"); },
		onError: error => notify.error("Channel could not be updated", error),
	});

	const providers = useQuery({ queryKey: ["notification-providers"], queryFn: () => api<Provider[]>("/notifications/providers/") });
	const [providerChannel, setProviderChannel] = useState<Channel | null>(null);
	const [providerForm, setProviderForm] = useState({ sender_id: "", api_key: "", api_secret: "", provider: "", is_active: true });
	const openProvider = (channel: Channel) => {
		const existing = providers.data?.find(p => p.channel === channel);
		setProviderForm({ sender_id: existing?.sender_id ?? "", api_key: "", api_secret: "", provider: existing?.provider ?? "", is_active: existing?.is_active ?? true });
		setProviderChannel(channel);
	};
	const saveProvider = useMutation({
		mutationFn: () => api<Provider>("/notifications/providers/", { method: "POST", body: JSON.stringify({ channel: providerChannel, ...providerForm }) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-providers"] }); setProviderChannel(null); notify.success("Provider configuration saved"); },
		onError: error => notify.error("Provider configuration could not be saved", error),
	});

	return <>
		<PageHeader eyebrow="Communication" title="Providers & channels" description="General delivery configuration, per-channel enablement, and provider credentials." />
		<Stack gap="lg">
			<Card withBorder padding="lg">
				<Group justify="space-between" align="flex-start">
					<Stack gap={4}>
						<Text fw={600}>General setup</Text>
						{setup.isLoading ? <Loading label="Loading" /> : <Text size="sm" c="dimmed">Notifications {setup.data?.notifications_enabled ? "enabled" : "disabled"} · default country code {setup.data?.default_country_code || "not set"}</Text>}
					</Stack>
					{canManage && <Button variant="light" onClick={() => { setSetupForm(setup.data ?? { notifications_enabled: true, default_country_code: "" }); setSetupOpen(true); }}>Edit</Button>}
				</Group>
			</Card>

			<Card withBorder padding="lg">
				<Stack gap="md">
					<Text fw={600}>Channels</Text>
					{channels.isLoading ? <Loading label="Loading channels" /> : CHANNELS.map(channel => {
						const state = channels.data?.find(c => c.channel === channel);
						const provider = providers.data?.find(p => p.channel === channel);
						return <Group key={channel} justify="space-between" wrap="nowrap">
							<Group gap="sm">
								<Text fw={500}>{channelLabel(channel)}</Text>
								{provider && <Badge variant="light" color={provider.provider === "STUB" ? "gray" : "orange"}>{provider.provider}</Badge>}
							</Group>
							<Group gap="sm">
								{channel !== "IN_APP" && canManage && <Button size="xs" variant="default" onClick={() => openProvider(channel)}>Configure provider</Button>}
								<Switch disabled={!canManage} checked={state?.enabled ?? false} onChange={e => toggleChannel.mutate({ channel, enabled: e.currentTarget.checked })} />
							</Group>
						</Group>;
					})}
				</Stack>
			</Card>
		</Stack>

		<ActionDialog open={setupOpen} title="Edit notification setup" confirmLabel="Save" busy={saveSetup.isPending} onClose={() => setSetupOpen(false)} onSubmit={e => { e.preventDefault(); saveSetup.mutate(); }}>
			<Failure error={saveSetup.error} />
			<Stack gap="sm">
				<Switch label="Notifications enabled" checked={setupForm.notifications_enabled} onChange={e => setSetupForm({ ...setupForm, notifications_enabled: e.currentTarget.checked })} />
				<TextInput label="Default country code" maxLength={5} value={setupForm.default_country_code} onChange={e => setSetupForm({ ...setupForm, default_country_code: e.currentTarget.value })} />
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!providerChannel} title={`Configure ${providerChannel ? channelLabel(providerChannel) : ""} provider`} confirmLabel="Save" busy={saveProvider.isPending} onClose={() => setProviderChannel(null)} onSubmit={e => { e.preventDefault(); saveProvider.mutate(); }}>
			<Failure error={saveProvider.error} />
			<Stack gap="sm">
				<Text size="xs" c="dimmed">Only the STUB provider is currently wired for delivery; other values are stored but won't send anything yet.</Text>
				<TextInput label="Provider" maxLength={40} value={providerForm.provider} onChange={e => setProviderForm({ ...providerForm, provider: e.currentTarget.value })} />
				<TextInput label="Sender ID" maxLength={40} value={providerForm.sender_id} onChange={e => setProviderForm({ ...providerForm, sender_id: e.currentTarget.value })} />
				<PasswordInput label="API key" description="Leave blank to keep the current key unchanged." value={providerForm.api_key} onChange={e => setProviderForm({ ...providerForm, api_key: e.currentTarget.value })} />
				<PasswordInput label="API secret" description="Leave blank to keep the current secret unchanged." value={providerForm.api_secret} onChange={e => setProviderForm({ ...providerForm, api_secret: e.currentTarget.value })} />
				<Switch label="Active" checked={providerForm.is_active} onChange={e => setProviderForm({ ...providerForm, is_active: e.currentTarget.checked })} />
			</Stack>
		</ActionDialog>
	</>;
}

// --- Rules ------------------------------------------------------------------

type RecipientType = "GUARDIAN" | "EMPLOYEE";
type RecipientPolicy = "PRIMARY" | "PRIMARY_AND_EMERGENCY";
type Rule = { id: string; event_code: string; recipient_type: RecipientType; recipient_policy: RecipientPolicy | null; channel: Channel; template: string; enabled: boolean };

// Mirrors apps/notifications/catalogue.py:35-51 exactly -- only 3 events are
// wired this milestone, and each allows exactly one recipient type, so it is
// not a real user choice.
const EVENTS: { code: string; label: string; recipientType: RecipientType }[] = [
	{ code: "finance.payment.received", label: "Payment received", recipientType: "GUARDIAN" },
	{ code: "leave.request.approved", label: "Leave request approved", recipientType: "EMPLOYEE" },
	{ code: "attendance.student.absent", label: "Student marked absent", recipientType: "GUARDIAN" },
];
const eventLabel = (code: string) => EVENTS.find(e => e.code === code)?.label ?? code;

export function NotificationRulesPage() {
	const { can } = useAccess();
	const qc = useQueryClient();
	const [page, setPage] = useState(1);
	const q = useQuery({ queryKey: ["notification-rules", page], queryFn: () => api<Page<Rule>>("/notifications/rules/", { params: { page } }) });
	const templates = useQuery({ queryKey: ["notification-templates-lookup"], queryFn: () => api<Page<Template>>("/notifications/templates/", { params: { page_size: 100 } }), enabled: can("notifications.rules.manage") });
	const templateName = (id: string) => templates.data?.results.find(t => t.id === id)?.name ?? id;

	const [dialog, setDialog] = useState<"create" | Rule | null>(null);
	const [eventCode, setEventCode] = useState("");
	const [channel, setChannel] = useState<Channel | "">("");
	const [recipientPolicy, setRecipientPolicy] = useState<RecipientPolicy>("PRIMARY_AND_EMERGENCY");
	const [template, setTemplate] = useState("");
	const [enabled, setEnabled] = useState(true);

	const event = EVENTS.find(e => e.code === eventCode);
	const allowedChannels = event?.recipientType === "GUARDIAN" ? CHANNELS.filter(c => c !== "IN_APP") : CHANNELS;

	const openCreate = () => { setEventCode(""); setChannel(""); setRecipientPolicy("PRIMARY_AND_EMERGENCY"); setTemplate(""); setEnabled(true); setDialog("create"); };
	const openEdit = (rule: Rule) => { setEventCode(rule.event_code); setChannel(rule.channel); setRecipientPolicy(rule.recipient_policy ?? "PRIMARY_AND_EMERGENCY"); setTemplate(rule.template); setEnabled(rule.enabled); setDialog(rule); };

	const create = useMutation({
		mutationFn: () => api<Rule>("/notifications/rules/", { method: "POST", body: JSON.stringify({ event_code: eventCode, recipient_type: event!.recipientType, channel, template, enabled, ...(event!.recipientType === "GUARDIAN" ? { recipient_policy: recipientPolicy } : {}) }) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-rules"] }); setDialog(null); notify.success("Notification rule created"); },
		onError: error => notify.error("Notification rule could not be created", error),
	});
	const update = useMutation({
		mutationFn: () => api<Rule>(`/notifications/rules/${(dialog as Rule).id}/`, { method: "PATCH", body: JSON.stringify({ template, enabled, ...(event?.recipientType === "GUARDIAN" ? { recipient_policy: recipientPolicy } : {}) }) }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["notification-rules"] }); setDialog(null); notify.success("Notification rule updated"); },
		onError: error => notify.error("Notification rule could not be updated", error),
	});
	const isEdit = dialog !== null && dialog !== "create";

	const columns: Column<Rule>[] = [
		{ key: "event", header: "Event", cell: r => eventLabel(r.event_code) },
		{ key: "recipient", header: "Recipient", cell: r => r.recipient_type },
		{ key: "channel", header: "Channel", cell: r => <Badge variant="light">{channelLabel(r.channel)}</Badge> },
		{ key: "template", header: "Template", cell: r => templateName(r.template) },
		{ key: "enabled", header: "Status", cell: r => <StatusBadge value={r.enabled} /> },
	];

	return <>
		<PageHeader eyebrow="Communication" title="Notification rules" description="Bind a business event to a template and channel." action={can("notifications.rules.manage") ? <Button onClick={openCreate}>+ New rule</Button> : undefined} />
		<DataTable columns={columns} rows={q.data?.results ?? []} rowKey={r => r.id} loading={q.isLoading} error={q.error} retry={() => void q.refetch()} count={q.data?.count} page={page} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage} onRow={can("notifications.rules.manage") ? openEdit : undefined} />

		<ActionDialog open={dialog === "create"} title="Create notification rule" confirmLabel="Create rule" busy={create.isPending} onClose={() => setDialog(null)} onSubmit={e => { e.preventDefault(); create.mutate(); }}>
			<Failure error={create.error} />
			<Stack gap="sm">
				<Select required label="Event" data={EVENTS.map(e => ({ value: e.code, label: e.label }))} value={eventCode} onChange={value => { setEventCode(value ?? ""); setChannel(""); }} />
				{event && <TextInput label="Recipient type" value={event.recipientType} disabled description="Fixed by the event — not a free choice." />}
				{event && <Select required label="Channel" data={allowedChannels.map(c => ({ value: c, label: channelLabel(c) }))} value={channel} onChange={value => setChannel((value as Channel) ?? "")} />}
				{event?.recipientType === "GUARDIAN" && <Select label="Recipient policy" data={[{ value: "PRIMARY", label: "Primary guardian only" }, { value: "PRIMARY_AND_EMERGENCY", label: "Primary and emergency contacts" }]} value={recipientPolicy} onChange={value => setRecipientPolicy((value as RecipientPolicy) ?? recipientPolicy)} />}
				<Select required label="Template" data={(templates.data?.results ?? []).filter(t => t.channel === channel).map(t => ({ value: t.id, label: t.name }))} value={template} onChange={value => setTemplate(value ?? "")} disabled={!channel} />
				<Switch label="Enabled" checked={enabled} onChange={e => setEnabled(e.currentTarget.checked)} />
			</Stack>
		</ActionDialog>

		<ActionDialog open={isEdit} title="Edit notification rule" confirmLabel="Save changes" busy={update.isPending} onClose={() => setDialog(null)} onSubmit={e => { e.preventDefault(); update.mutate(); }}>
			<Failure error={update.error} />
			<Stack gap="sm">
				<TextInput label="Event" value={eventCode ? eventLabel(eventCode) : ""} disabled />
				<TextInput label="Channel" value={channel} disabled />
				{event?.recipientType === "GUARDIAN" && <Select label="Recipient policy" data={[{ value: "PRIMARY", label: "Primary guardian only" }, { value: "PRIMARY_AND_EMERGENCY", label: "Primary and emergency contacts" }]} value={recipientPolicy} onChange={value => setRecipientPolicy((value as RecipientPolicy) ?? recipientPolicy)} />}
				<Select required label="Template" data={(templates.data?.results ?? []).filter(t => t.channel === channel).map(t => ({ value: t.id, label: t.name }))} value={template} onChange={value => setTemplate(value ?? "")} />
				<Switch label="Enabled" checked={enabled} onChange={e => setEnabled(e.currentTarget.checked)} />
			</Stack>
		</ActionDialog>
	</>;
}

// --- Outbox -------------------------------------------------------------

type OutboxStatus = "PENDING" | "PROCESSING" | "PROCESSED" | "FAILED";
type OutboxEntry = { id: string; channel: Channel; recipient: string; message_type: string; status: OutboxStatus; attempts: number; last_error: string; created_at: string; processed_at: string | null };
type DeliveryAttempt = { id: string; attempt_number: number; provider: string; started_at: string; completed_at: string | null; status: string; provider_reference: string; error_code: string; error_message: string };

export function NotificationOutboxPage() {
	const { can } = useAccess();
	const qc = useQueryClient();
	const [page, setPage] = useState(1);
	const q = useQuery({ queryKey: ["notification-outbox", page], queryFn: () => api<Page<OutboxEntry>>("/notifications/outbox/", { params: { page } }) });
	const [selected, setSelected] = useState<OutboxEntry | null>(null);
	const deliveries = useQuery({ queryKey: ["notification-outbox-deliveries", selected?.id], queryFn: () => api<DeliveryAttempt[]>(`/notifications/outbox/${selected!.id}/deliveries/`), enabled: !!selected });
	const retry = useMutation({
		mutationFn: (id: string) => api<OutboxEntry>(`/notifications/outbox/${id}/retry/`, { method: "POST" }),
		onSuccess: updated => { void qc.invalidateQueries({ queryKey: ["notification-outbox"] }); setSelected(updated); notify.success("Notification queued for retry"); },
		onError: error => notify.error("Notification could not be retried", error),
	});
	const columns: Column<OutboxEntry>[] = [
		{ key: "channel", header: "Channel", cell: r => <Badge variant="light">{channelLabel(r.channel)}</Badge> },
		{ key: "recipient", header: "Recipient", cell: r => r.recipient || "—" },
		{ key: "type", header: "Message type", cell: r => r.message_type },
		{ key: "status", header: "Status", cell: r => <StatusBadge value={r.status} /> },
		{ key: "attempts", header: "Attempts", cell: r => r.attempts },
		{ key: "created", header: "Created", cell: r => new Date(r.created_at).toLocaleString() },
	];
	return <>
		<PageHeader eyebrow="Communication" title="Outbox" description="System-generated deliveries. This app configures and investigates delivery — it never composes a message directly." />
		<DataTable columns={columns} rows={q.data?.results ?? []} rowKey={r => r.id} loading={q.isLoading} error={q.error} retry={() => void q.refetch()} count={q.data?.count} page={page} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage} onRow={setSelected} />
		<DetailDrawer open={!!selected} title="Delivery investigation" description={selected ? `${channelLabel(selected.channel)} to ${selected.recipient || "—"}` : undefined} onClose={() => setSelected(null)}>
			{selected && <Stack gap="md">
				<Group><StatusBadge value={selected.status} /><Text size="sm" c="dimmed">{selected.attempts} attempt{selected.attempts === 1 ? "" : "s"}</Text></Group>
				{selected.last_error && <Alert color="red" variant="light">{selected.last_error}</Alert>}
				{selected.status === "FAILED" && can("notifications.retry") && <Button loading={retry.isPending} onClick={() => retry.mutate(selected.id)}>Retry</Button>}
				<Text fw={600} size="sm">Delivery attempts</Text>
				{deliveries.isLoading ? <Loading label="Loading attempts" /> : !deliveries.data?.length ? <Empty title="No delivery attempts yet" /> : <Stack gap="xs">
					{deliveries.data.map(attempt => <Card key={attempt.id} withBorder padding="sm">
						<Group justify="space-between"><Text size="sm">Attempt {attempt.attempt_number} · {attempt.provider}</Text><StatusBadge value={attempt.status} /></Group>
						{attempt.error_message && <Text size="xs" c="red">{attempt.error_message}</Text>}
					</Card>)}
				</Stack>}
			</Stack>}
		</DetailDrawer>
	</>;
}
