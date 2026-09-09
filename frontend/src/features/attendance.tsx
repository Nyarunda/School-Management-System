import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Group, Modal, Select, Stack, Table, Text, TextInput, Tooltip } from "@mantine/core";
import { api, ApiError, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { FilterBar } from "../components/FilterBar";
import { WorkspaceHeader } from "../components/WorkspaceHeader";
import { notify } from "../components/notifications/notify";
import { Empty, ErrorState, Loading, StatusBadge } from "../components/ui";

type Session={id:string;class_group:string;session_date:string;opened_by:string;opened_at:string;last_submitted_at:string|null;status:"OPEN"|"SUBMITTED";submitted_by:string|null;submitted_at:string|null};
type RecordStatus="NOT_MARKED"|"PRESENT"|"ABSENT"|"LATE"|"EXCUSED"|"SICK"|"SCHOOL_ACTIVITY";
type AttendanceRecord={id:string;student:string;status:RecordStatus;remarks:string;recorded_by:string|null;updated_at:string};
type SessionDetail={session:Session;records:AttendanceRecord[]};
const statuses:RecordStatus[]=["NOT_MARKED","PRESENT","ABSENT","LATE","EXCUSED","SICK","SCHOOL_ACTIVITY"];
const statusOptions=statuses.map(s=>({value:s,label:label(s)}));
function label(value:string){return value.toLowerCase().replace(/_/g," ").replace(/^./,c=>c.toUpperCase())}
const when=(value:string|null)=>value?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(value)):"—";
const errorText=(error:unknown)=>error instanceof ApiError?error.message:error instanceof Error?error.message:"The action failed";

export function AttendancePage(){
	const {can}=useAccess();
	const [page,setPage]=useState(1);
	const [from,setFrom]=useState("");
	const [to,setTo]=useState("");
	const [selected,setSelected]=useState<string|null>(null);
	const sessions=useQuery({queryKey:["attendance-sessions",page,from,to],queryFn:()=>api<Page<Session>>("/attendance/sessions/",{params:{page,from:from||undefined,to:to||undefined}})});
	const columns:Column<Session>[]=[
		{key:"date",header:"Date",cell:r=><Text size="sm" fw={600}>{r.session_date}</Text>},
		{key:"class",header:"Class group",cell:r=><Text size="sm" ff="monospace">{r.class_group}</Text>},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},
		{key:"opened",header:"Opened",cell:r=>when(r.opened_at)},
		{key:"submitted",header:"Last submitted",cell:r=>when(r.last_submitted_at)},
	];
	return <>
		<WorkspaceHeader
			eyebrow="Academics · Attendance"
			title="Attendance registers"
			description="Open a daily class register, save the full roster, and submit it for the school record."
			action={can("attendance.session.manage")?
				<Tooltip label="Requires a class-group catalogue endpoint" multiline w={230}>
					<Button variant="default" disabled>Open register unavailable</Button>
				</Tooltip>:undefined}
		/>
		<Alert color="yellow" variant="light" mb="md">
			<Text size="sm" fw={600}>Register creation needs backend catalogue support.</Text>
			<Text size="sm" c="dimmed">The attendance API accepts a class-group ID, but there is no authorised class-group list endpoint for teachers to select from.</Text>
		</Alert>
		<DataTable title="Attendance sessions" columns={columns} rows={sessions.data?.results??[]} rowKey={r=>r.id} loading={sessions.isLoading} error={sessions.error} retry={()=>void sessions.refetch()} onRefresh={()=>void sessions.refetch()}
			count={sessions.data?.count} page={page} previous={!!sessions.data?.previous} next={!!sessions.data?.next} onPage={setPage} onRow={r=>setSelected(r.id)}
			toolbar={<FilterBar>
				<TextInput type="date" label="From" size="xs" w={140} value={from} onChange={e=>{setFrom(e.currentTarget.value);setPage(1)}}/>
				<TextInput type="date" label="To" size="xs" w={140} value={to} onChange={e=>{setTo(e.currentTarget.value);setPage(1)}}/>
			</FilterBar>}
		/>
		{selected&&<RegisterWorkspace id={selected} onClose={()=>setSelected(null)}/>}
	</>;
}

function RegisterWorkspace({id,onClose}:{id:string;onClose:()=>void}){
	const {can}=useAccess();
	const canEdit=can("attendance.session.manage");
	const qc=useQueryClient();
	const detail=useQuery({queryKey:["attendance-session",id],queryFn:()=>api<SessionDetail>(`/attendance/sessions/${id}/`)});
	const [entries,setEntries]=useState<AttendanceRecord[]>([]);
	const [confirmSubmit,setConfirmSubmit]=useState(false);
	useEffect(()=>{if(detail.data)setEntries(detail.data.records)},[detail.data]);
	const changed=useMemo(()=>JSON.stringify(entries.map(r=>[r.student,r.status,r.remarks]))!==JSON.stringify((detail.data?.records??[]).map(r=>[r.student,r.status,r.remarks])),[entries,detail.data]);
	const save=useMutation({
		mutationFn:()=>api<AttendanceRecord[]>(`/attendance/sessions/${id}/records/`,{method:"POST",body:JSON.stringify({entries:entries.map(({student,status,remarks})=>({student,status,remarks}))})}),
		onSuccess:records=>{setEntries(records);void qc.invalidateQueries({queryKey:["attendance-session",id]});void qc.invalidateQueries({queryKey:["attendance-sessions"]});notify.success("Attendance register saved")},
		onError:error=>notify.error("Attendance register could not be saved",error),
	});
	const submit=useMutation({
		mutationFn:async()=>{if(changed)await api(`/attendance/sessions/${id}/records/`,{method:"POST",body:JSON.stringify({entries:entries.map(({student,status,remarks})=>({student,status,remarks}))})});return api<Session>(`/attendance/sessions/${id}/submit/`,{method:"POST"})},
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["attendance-session",id]});void qc.invalidateQueries({queryKey:["attendance-sessions"]});setConfirmSubmit(false);notify.success("Attendance register submitted")},
		onError:error=>notify.error("Attendance register could not be submitted",error),
	});
	const setAll=(status:RecordStatus)=>setEntries(rows=>rows.map(row=>({...row,status})));
	const update=(student:string,patch:Partial<AttendanceRecord>)=>setEntries(rows=>rows.map(row=>row.student===student?{...row,...patch}:row));
	const unmarked=entries.filter(r=>r.status==="NOT_MARKED").length;

	return <Modal opened fullScreen closeOnClickOutside={false} closeOnEscape={false} title={detail.data?.session.session_date??"Attendance register"} onClose={onClose}>
		{detail.isLoading?<Loading label="Opening register"/>:detail.isError?<ErrorState error={detail.error}/>:!entries.length?<Empty title="This roster is empty" message="The session contains no snapshotted students."/>:
			<Stack gap="md">
				<Group justify="space-between" wrap="wrap" gap="sm">
					<Group gap="sm">
						<Text size="sm" c="dimmed">Class group <Text span ff="monospace">{detail.data?.session.class_group}</Text></Text>
						<StatusBadge value={detail.data?.session.status}/>
						<Text size="sm" c="dimmed">{entries.length} learners · {unmarked} not marked</Text>
					</Group>
					{canEdit&&<Group gap="xs">
						<Button variant="default" size="xs" onClick={()=>setAll("PRESENT")}>Mark all present</Button>
						<Button size="xs" disabled={!changed} loading={save.isPending} onClick={()=>save.mutate()}>Save register</Button>
						<Button size="xs" disabled={submit.isPending} onClick={()=>setConfirmSubmit(true)}>Submit register</Button>
					</Group>}
				</Group>
				{(save.error||submit.error)&&<Alert color="red" variant="light">{errorText(save.error||submit.error)}</Alert>}
				<Alert color="yellow" variant="light">Student display data is not included in the attendance response. UUIDs are shown temporarily to avoid inventing identities.</Alert>
				<Table.ScrollContainer minWidth={640}>
					<Table withTableBorder verticalSpacing="xs">
						<Table.Thead><Table.Tr><Table.Th>Student</Table.Th><Table.Th w={190}>Attendance</Table.Th><Table.Th>Remarks</Table.Th></Table.Tr></Table.Thead>
						<Table.Tbody>
							{entries.map(row=><Table.Tr key={row.id}>
								<Table.Td><Text size="sm" ff="monospace">{row.student}</Text></Table.Td>
								<Table.Td>
									<Select aria-label={`Attendance status for ${row.student}`} size="xs" disabled={!canEdit} allowDeselect={false} data={statusOptions} value={row.status} onChange={value=>value&&update(row.student,{status:value as RecordStatus})}/>
								</Table.Td>
								<Table.Td>
									<TextInput aria-label={`Remarks for ${row.student}`} size="xs" disabled={!canEdit} maxLength={240} placeholder="Optional remark" value={row.remarks} onChange={e=>update(row.student,{remarks:e.currentTarget.value})}/>
								</Table.Td>
							</Table.Tr>)}
						</Table.Tbody>
					</Table>
				</Table.ScrollContainer>
			</Stack>}
		<ActionDialog open={confirmSubmit} title="Submit attendance register" description={unmarked?`${unmarked} learner${unmarked===1?" is":"s are"} still not marked. Submission will be rejected until every learner has a final status.`:"This records the completed register. Later corrections remain audited by the backend."} confirmLabel="Submit register" busy={submit.isPending} onClose={()=>setConfirmSubmit(false)} onSubmit={e=>{e.preventDefault();submit.mutate()}}/>
	</Modal>;
}
