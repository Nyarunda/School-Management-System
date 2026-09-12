import { useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { Alert, Badge, Button, Card, Group, Modal, NumberInput, SimpleGrid, Stack, Table, Text, TextInput, Title } from "@mantine/core";
import { IconAlertCircle, IconDownload } from "@tabler/icons-react";
import { api, apiDownload, saveBlob } from "../api/client";
import { ActionDialog } from "../components/ActionDialog";
import { notify } from "../components/notifications/notify";
import { Empty, ErrorState, Loading, PageHeader } from "../components/ui";

type ReportParamType = "uuid" | "date" | "string" | "int";
type ReportDefinition = {
	code: string; label: string; permission_group: string;
	parameters: Record<string, { type: ReportParamType; required: boolean }>;
	can_view: boolean; can_export: boolean;
};
type ReportPreview = { columns: [string, string][]; rows: Record<string, unknown>[]; has_more: boolean };
type ExportStatus = "PENDING" | "PROCESSING" | "PROCESSED" | "FAILED";
type ReportExportJob = {
	id: string; report_code: string; status: ExportStatus; row_count: number | null;
	created_at: string; processed_at: string | null; last_error: string; download_available: boolean;
};

const PREVIEW_PAGE_SIZE = 25;

function Failure({ error }: { error: unknown }) {
	return error ? <Alert color="red" variant="light" icon={<IconAlertCircle size={16} />}>{error instanceof Error ? error.message : "The action failed"}</Alert> : null;
}

export function ReportsPage() {
	const catalogue = useQuery({ queryKey: ["reports-catalogue"], queryFn: () => api<ReportDefinition[]>("/reports/catalogue/") });
	const [selected, setSelected] = useState<ReportDefinition | null>(null);
	return <>
		<PageHeader eyebrow="Reporting" title="Report catalogue" description="Reports available under your current permissions. Exports run in the background and are ready within about a minute." />
		{catalogue.isLoading ? <Loading label="Loading catalogue" /> : catalogue.isError ? <ErrorState error={catalogue.error} retry={() => void catalogue.refetch()} /> : !catalogue.data?.length ? <Empty title="No reports available" /> :
			<SimpleGrid cols={{ base: 1, sm: 2, lg: 3 }}>
				{catalogue.data.map(report => <Card key={report.code} withBorder padding="lg">
					<Stack gap="xs">
						<Group justify="space-between" wrap="nowrap">
							<Title order={4} size="h5">{report.label}</Title>
							{report.can_export && <Badge color="teal" variant="light">Export</Badge>}
						</Group>
						<Text size="sm" c="dimmed">{report.permission_group}</Text>
						<Button variant="light" onClick={() => setSelected(report)}>Open</Button>
					</Stack>
				</Card>)}
			</SimpleGrid>}
		{selected && <ReportWorkspace report={selected} onClose={() => setSelected(null)} />}
	</>;
}

function ReportWorkspace({ report, onClose }: { report: ReportDefinition; onClose: () => void }) {
	const [params, setParams] = useState<Record<string, string>>({});
	const [activeParams, setActiveParams] = useState<Record<string, string> | null>(null);
	const [page, setPage] = useState(1);
	const [exportOpen, setExportOpen] = useState(false);
	const [idempotencyKey, setIdempotencyKey] = useState("");
	const [job, setJob] = useState<ReportExportJob | null>(null);

	const preview = useQuery({
		queryKey: ["report-preview", report.code, activeParams, page],
		queryFn: () => api<ReportPreview>(`/reports/${report.code}/preview/`, { params: { ...activeParams, page, page_size: PREVIEW_PAGE_SIZE } }),
		enabled: !!activeParams,
	});

	const requestExport = useMutation({
		mutationFn: () => api<ReportExportJob>(`/reports/${report.code}/export/`, { method: "POST", body: JSON.stringify({ ...params, idempotency_key: idempotencyKey }) }),
		onSuccess: created => { setJob(created); setExportOpen(false); notify.success("Export queued"); },
		onError: error => notify.error("Export could not be requested", error),
	});

	const jobStatus = useQuery({
		queryKey: ["report-export-job", job?.id],
		queryFn: () => api<ReportExportJob>(`/reports/exports/${job!.id}/`),
		enabled: !!job,
		refetchInterval: query => {
			const status = query.state.data?.status;
			return status === "PENDING" || status === "PROCESSING" ? 3000 : false;
		},
	});
	const currentJob = jobStatus.data ?? job;

	const download = useMutation({
		mutationFn: () => apiDownload(`/reports/exports/${currentJob!.id}/download/`),
		onSuccess: ({ blob, filename }) => saveBlob(blob, filename),
		onError: error => notify.error("Export could not be downloaded", error),
	});

	const setParam = (key: string, value: string) => setParams(current => ({ ...current, [key]: value }));
	const missingRequired = Object.entries(report.parameters).some(([key, def]) => def.required && !params[key]);

	return <Modal opened fullScreen title={report.label} onClose={onClose}>
		<Stack gap="md">
			<Stack gap="xs">
				{Object.entries(report.parameters).map(([key, def]) => {
					const label = key.replace(/_/g, " ").replace(/^./, c => c.toUpperCase()) + (def.required ? " *" : "");
					if (def.type === "date") return <TextInput key={key} type="date" label={label} value={params[key] ?? ""} onChange={e => setParam(key, e.currentTarget.value)} />;
					if (def.type === "int") return <NumberInput key={key} label={label} value={params[key] ?? ""} onChange={value => setParam(key, value === "" ? "" : String(value))} />;
					return <TextInput key={key} label={label} placeholder={def.type === "uuid" ? "UUID" : undefined} value={params[key] ?? ""} onChange={e => setParam(key, e.currentTarget.value)} />;
				})}
			</Stack>
			<Group>
				{report.can_view && <Button variant="light" disabled={missingRequired} onClick={() => { setActiveParams(params); setPage(1); }}>Preview</Button>}
				{report.can_export && <Button disabled={missingRequired} onClick={() => { setIdempotencyKey(crypto.randomUUID()); setExportOpen(true); }}>Request export</Button>}
			</Group>

			{activeParams && (
				preview.isLoading ? <Loading label="Loading preview" /> :
				preview.isError ? <ErrorState error={preview.error} retry={() => void preview.refetch()} /> :
				!preview.data?.rows.length ? <Empty title="No rows for these parameters" /> :
				<Stack gap="xs">
					<Table.ScrollContainer minWidth={480}>
						<Table striped withTableBorder>
							<Table.Thead><Table.Tr>{preview.data.columns.map(([field, header]) => <Table.Th key={field}>{header}</Table.Th>)}</Table.Tr></Table.Thead>
							<Table.Tbody>{preview.data.rows.map((row, index) => <Table.Tr key={index}>{preview.data!.columns.map(([field]) => <Table.Td key={field}>{String(row[field] ?? "—")}</Table.Td>)}</Table.Tr>)}</Table.Tbody>
						</Table>
					</Table.ScrollContainer>
					<Group justify="flex-end">
						<Button size="xs" variant="default" disabled={page <= 1} onClick={() => setPage(p => p - 1)}>Previous</Button>
						<Text size="xs" c="dimmed">Page {page}</Text>
						<Button size="xs" variant="default" disabled={!preview.data.has_more} onClick={() => setPage(p => p + 1)}>Next</Button>
					</Group>
				</Stack>
			)}

			{currentJob && <Card withBorder padding="md">
				<Stack gap="xs">
					<Group justify="space-between">
						<Badge color={currentJob.status === "PROCESSED" ? "teal" : currentJob.status === "FAILED" ? "red" : "yellow"}>{currentJob.status}</Badge>
						{currentJob.row_count != null && <Text size="xs" c="dimmed">{currentJob.row_count} rows</Text>}
					</Group>
					{(currentJob.status === "PENDING" || currentJob.status === "PROCESSING") && <Text size="sm" c="dimmed">Export is queued — this can take up to about a minute.</Text>}
					{currentJob.status === "FAILED" && <Alert color="red" variant="light">{currentJob.last_error || "This export failed."}</Alert>}
					{currentJob.download_available && <Button leftSection={<IconDownload size={16} />} loading={download.isPending} onClick={() => download.mutate()}>Download CSV</Button>}
				</Stack>
			</Card>}
		</Stack>

		<ActionDialog open={exportOpen} title="Request export" description="This queues a background export with the parameters above. It is not instant." confirmLabel="Request export" busy={requestExport.isPending} onClose={() => setExportOpen(false)} onSubmit={e => { e.preventDefault(); requestExport.mutate(); }}>
			<Failure error={requestExport.error} />
			<Stack gap={4}>
				{Object.entries(params).filter(([, value]) => value).map(([key, value]) => <Text key={key} size="sm"><b>{key}:</b> {value}</Text>)}
			</Stack>
		</ActionDialog>
	</Modal>;
}
