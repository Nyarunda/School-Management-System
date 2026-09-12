import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, FileInput, Stack, Text, TextInput } from "@mantine/core";
import { IconAlertCircle } from "@tabler/icons-react";
import { api, apiDownload, Page, saveBlob } from "../api/client";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { notify } from "../components/notifications/notify";

type Document = { id: string; document_type: string; original_filename: string | null; content_type: string | null; size_bytes: number | null; uploaded_at: string | null };

// Mirrors apps/documents/services.py's ALLOWED_CONTENT_TYPES/MAX_UPLOAD_SIZE_BYTES
// exactly, so an operator gets immediate feedback instead of waiting for a 400.
const ALLOWED_CONTENT_TYPES = ["application/pdf", "image/jpeg", "image/png", "text/csv"];
const MAX_UPLOAD_SIZE_BYTES = 10 * 1024 * 1024;
const DOCUMENT_TYPE_SUGGESTIONS = ["ID copy", "Birth certificate", "Certificate", "Contract", "Report"];

function formatSize(bytes: number | null): string {
	if (bytes == null) return "—";
	if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
	return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function Failure({ error }: { error: unknown }) {
	return error ? <Alert color="red" variant="light" icon={<IconAlertCircle size={16} />}>{error instanceof Error ? error.message : "The action failed"}</Alert> : null;
}

export function DocumentsPanel({ basePath, ownerId, viewPermission, managePermission, can }: {
	basePath: "/students" | "/staff/employees"; ownerId: string;
	viewPermission: string; managePermission: string; can: (permission?: string) => boolean;
}) {
	const qc = useQueryClient();
	const listPath = `${basePath}/${ownerId}/documents/`;
	const q = useQuery({ queryKey: ["documents", listPath], queryFn: () => api<Page<Document>>(listPath), enabled: can(viewPermission) });

	const [uploadOpen, setUploadOpen] = useState(false);
	const [documentType, setDocumentType] = useState("");
	const [file, setFile] = useState<File | null>(null);
	const [uploadError, setUploadError] = useState("");
	const openUpload = () => { setDocumentType(""); setFile(null); setUploadError(""); setUploadOpen(true); };
	const upload = useMutation({
		mutationFn: () => {
			const body = new FormData();
			body.append("document_type", documentType);
			body.append("file", file!);
			return api<Document>(listPath, { method: "POST", body });
		},
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["documents", listPath] }); setUploadOpen(false); notify.success("Document uploaded"); },
		onError: error => notify.error("Document could not be uploaded", error),
	});
	const submitUpload = () => {
		if (!file) { setUploadError("Choose a file to upload."); return; }
		if (!ALLOWED_CONTENT_TYPES.includes(file.type)) { setUploadError("Only PDF, JPEG, PNG, or CSV files are accepted."); return; }
		if (file.size > MAX_UPLOAD_SIZE_BYTES) { setUploadError("Files must be 10 MB or smaller."); return; }
		setUploadError("");
		upload.mutate();
	};

	const [downloadingId, setDownloadingId] = useState<string | null>(null);
	const download = useMutation({
		mutationFn: async (doc: Document) => { setDownloadingId(doc.id); return apiDownload(`${listPath}${doc.id}/download/`); },
		onSuccess: ({ blob, filename }) => saveBlob(blob, filename),
		onError: error => notify.error("Document could not be downloaded", error),
		onSettled: () => setDownloadingId(null),
	});

	const [deleteTarget, setDeleteTarget] = useState<Document | null>(null);
	const remove = useMutation({
		mutationFn: () => api(`${listPath}${deleteTarget!.id}/`, { method: "DELETE" }),
		onSuccess: () => { void qc.invalidateQueries({ queryKey: ["documents", listPath] }); setDeleteTarget(null); notify.success("Document deleted"); },
		onError: error => notify.error("Document could not be deleted", error),
	});

	const canManage = can(managePermission);
	const columns: Column<Document>[] = [
		{ key: "filename", header: "File", cell: r => r.original_filename ?? <Text c="dimmed" size="sm">Expired — file no longer available</Text> },
		{ key: "type", header: "Type", cell: r => r.document_type },
		{ key: "size", header: "Size", cell: r => formatSize(r.size_bytes) },
		{ key: "uploaded", header: "Uploaded", cell: r => r.uploaded_at ? new Date(r.uploaded_at).toLocaleDateString() : "—" },
		{ key: "action", header: "", cell: r => <>
			{r.original_filename && <Button size="xs" variant="subtle" loading={downloadingId === r.id} onClick={() => download.mutate(r)}>Download</Button>}
			{canManage && <Button size="xs" variant="subtle" color="red" onClick={() => setDeleteTarget(r)}>Delete</Button>}
		</> },
	];

	if (!can(viewPermission)) return null;

	return <Stack gap="sm">
		<Stack gap={0} align="flex-end"><Button size="xs" disabled={!canManage} onClick={openUpload}>+ Upload document</Button></Stack>
		<DataTable columns={columns} rows={q.data?.results ?? []} rowKey={r => r.id} loading={q.isLoading} error={q.error} retry={() => void q.refetch()} count={q.data?.count} showDensityToggle={false} />

		<ActionDialog open={uploadOpen} title="Upload document" confirmLabel="Upload" busy={upload.isPending} onClose={() => setUploadOpen(false)} onSubmit={e => { e.preventDefault(); submitUpload(); }}>
			<Failure error={uploadError ? new Error(uploadError) : upload.error} />
			<Stack gap="sm">
				<TextInput required label="Document type" list="document-type-suggestions" maxLength={80} value={documentType} onChange={e => setDocumentType(e.currentTarget.value)} />
				<datalist id="document-type-suggestions">{DOCUMENT_TYPE_SUGGESTIONS.map(s => <option key={s} value={s} />)}</datalist>
				<FileInput required label="File" description="PDF, JPEG, PNG, or CSV, up to 10 MB." accept="application/pdf,image/jpeg,image/png,text/csv" value={file} onChange={setFile} />
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!deleteTarget} title="Delete document" danger confirmLabel="Delete permanently" busy={remove.isPending} onClose={() => setDeleteTarget(null)} onSubmit={e => { e.preventDefault(); remove.mutate(); }}>
			<Text size="sm">Delete this document permanently? The stored file will also be deleted. This cannot be undone.</Text>
		</ActionDialog>
	</Stack>;
}
