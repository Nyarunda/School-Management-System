import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Accordion, Alert, Badge, Button, Group, Modal, NumberInput, Select, Stack, Table, Text, Textarea, TextInput } from "@mantine/core";
import { api, ApiError, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { WorkspaceHeader } from "../components/WorkspaceHeader";
import { notify } from "../components/notifications/notify";
import { Empty, ErrorState, Loading, StatusBadge } from "../components/ui";
type AssessmentStatus="DRAFT"|"SUBMITTED"|"APPROVED"|"PUBLISHED";type MarkStatus="NOT_MARKED"|"SCORED"|"ABSENT"|"EXEMPT";
type Assessment={id:string;term:string;assessment_type:string;class_group:string;subject:string;name:string;max_marks:string;scheduled_date:string;grading_scheme:string|null;created_by:string;status:AssessmentStatus;submitted_at:string|null;approved_at:string|null;published_at:string|null;last_amended_at:string|null};
type Result={id:string;student:string;mark_status:MarkStatus;score:string|null;grade:string;remarks:string;recorded_by:string|null;updated_at:string};type Band={id:string;grade_label:string;min_percentage:string;max_percentage:string;remark:string};type Detail={assessment:Assessment;grading_bands:Band[];results:Result[]};
const markStatuses:MarkStatus[]=["NOT_MARKED","SCORED","ABSENT","EXEMPT"];const label=(v:string)=>v.toLowerCase().replace(/_/g," ").replace(/^./,c=>c.toUpperCase());const when=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v)):"—";const errorText=(e:unknown)=>e instanceof ApiError?e.message:e instanceof Error?e.message:"The action failed";

export function AssessmentsPage(){
	const {can}=useAccess();
	const [page,setPage]=useState(1);
	const [selected,setSelected]=useState<string|null>(null);
	const q=useQuery({queryKey:["assessments",page],queryFn:()=>api<Page<Assessment>>("/assessments/assessments/",{params:{page}})});
	const columns:Column<Assessment>[]=[
		{key:"name",header:"Assessment",cell:r=><><Text size="sm" fw={600}>{r.name}</Text><Text size="xs" c="dimmed">{r.scheduled_date}</Text></>},
		{key:"class",header:"Class group",cell:r=><Text size="sm" ff="monospace">{r.class_group}</Text>},
		{key:"subject",header:"Subject",cell:r=><Text size="sm" ff="monospace">{r.subject}</Text>},
		{key:"marks",header:"Maximum marks",cell:r=>r.max_marks},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},
	];
	return <>
		<WorkspaceHeader
			eyebrow="Academics · Assessments"
			title="Assessment register"
			description="Enter marks against the snapshotted roster and move assessments through controlled review and publication."
			action={can("assessment.manage")?<Button variant="default" disabled title="Term, class-group and subject catalogue endpoints are required">Open assessment unavailable</Button>:undefined}
		/>
		<Alert color="yellow" variant="light" mb="md" title="Assessment creation needs backend catalogue support">Term, class-group and subject selectors cannot be populated from the current API.</Alert>
		<DataTable title="Assessments" columns={columns} rows={q.data?.results??[]} rowKey={r=>r.id} loading={q.isLoading} error={q.error} retry={()=>void q.refetch()} onRefresh={()=>void q.refetch()}
			count={q.data?.count} page={page} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage} onRow={r=>setSelected(r.id)}/>
		{selected&&<AssessmentWorkspace id={selected} onClose={()=>setSelected(null)}/>}
	</>;
}

function AssessmentWorkspace({id,onClose}:{id:string;onClose:()=>void}){
	const {can}=useAccess();
	const qc=useQueryClient();
	const detail=useQuery({queryKey:["assessment",id],queryFn:()=>api<Detail>(`/assessments/assessments/${id}/`)});
	const [rows,setRows]=useState<Result[]>([]);
	const [transition,setTransition]=useState<"submit"|"approve"|"reject"|"reopen"|"publish"|null>(null);
	const [reason,setReason]=useState("");
	useEffect(()=>{if(detail.data)setRows(detail.data.results)},[detail.data]);
	const original=detail.data?.results??[];
	const changed=useMemo(()=>JSON.stringify(rows.map(r=>[r.student,r.mark_status,r.score,r.remarks]))!==JSON.stringify(original.map(r=>[r.student,r.mark_status,r.score,r.remarks])),[rows,original]);
	const assessment=detail.data?.assessment;
	const canEdit=!!assessment&&((assessment.status==="DRAFT"&&can("assessment.marks.manage"))||(assessment.status==="PUBLISHED"&&can("assessment.result.amend")));
	const payload=()=>({entries:rows.map(({student,mark_status,score,remarks})=>({student,mark_status,score:mark_status==="SCORED"&&score!==""?score:null,remarks}))});
	const save=useMutation({
		mutationFn:()=>api<Result[]>(`/assessments/assessments/${id}/marks/`,{method:"POST",body:JSON.stringify(payload())}),
		onSuccess:result=>{setRows(result);void qc.invalidateQueries({queryKey:["assessment",id]});notify.success("Marks saved")},
		onError:error=>notify.error("Marks could not be saved",error),
	});
	const transitionVerb:Record<string,string>={submit:"submitted for approval",approve:"approved",reject:"rejected",reopen:"reopened",publish:"published"};
	const workflow=useMutation({
		mutationFn:async()=>{if(transition==="submit"&&changed)await api(`/assessments/assessments/${id}/marks/`,{method:"POST",body:JSON.stringify(payload())});return api<Assessment>(`/assessments/assessments/${id}/${transition}/`,{method:"POST",body:JSON.stringify(transition==="reject"||transition==="reopen"?{reason}:{})})},
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["assessment",id]});void qc.invalidateQueries({queryKey:["assessments"]});const done=transition;setTransition(null);notify.success(`Assessment ${transitionVerb[done!]}`)},
		onError:error=>notify.error(`Assessment could not be ${transitionVerb[transition!]}`,error),
	});
	const update=(student:string,patch:Partial<Result>)=>setRows(current=>current.map(r=>r.student===student?{...r,...patch}:r));
	const missing=rows.filter(r=>r.mark_status==="NOT_MARKED").length;
	const action=(name:typeof transition,text:string,danger=false)=><Button size="xs" color={danger?"red":"indigo"} onClick={()=>{setReason("");setTransition(name)}}>{text}</Button>;

	return <Modal opened fullScreen closeOnClickOutside={false} closeOnEscape={false} title={assessment?.name??"Assessment"} onClose={onClose}>
		{detail.isLoading?<Loading label="Opening assessment"/>:detail.isError?<ErrorState error={detail.error}/>:!assessment?<Empty/>:
			<Stack gap="md">
				<Group justify="space-between" wrap="wrap" gap="sm">
					<Group gap="sm" wrap="wrap">
						<Text size="sm" c="dimmed">Class <Text span ff="monospace">{assessment.class_group}</Text></Text>
						<Text size="sm" c="dimmed">Subject <Text span ff="monospace">{assessment.subject}</Text></Text>
						<Text size="sm" c="dimmed">Term <Text span ff="monospace">{assessment.term}</Text></Text>
						<StatusBadge value={assessment.status}/>
						<Text size="sm" c="dimmed">{rows.length} candidates · {missing} missing</Text>
						{changed&&<Badge color="yellow" variant="light">Unsaved changes</Badge>}
					</Group>
					<Group gap="xs">
						{canEdit&&<Button variant="default" size="xs" disabled={!changed} loading={save.isPending} onClick={()=>save.mutate()}>Save draft</Button>}
						{assessment.status==="DRAFT"&&can("assessment.marks.manage")&&action("submit","Submit")}
						{assessment.status==="SUBMITTED"&&can("assessment.approve")&&<>{action("reject","Reject",true)}{action("approve","Approve")}</>}
						{assessment.status==="APPROVED"&&can("assessment.approve")&&action("reopen","Reopen",true)}
						{assessment.status==="APPROVED"&&can("assessment.publish")&&action("publish","Publish")}
					</Group>
				</Group>
				{(save.error||workflow.error)&&<Alert color="red" variant="light">{errorText(save.error||workflow.error)}</Alert>}
				<Alert color="yellow" variant="light">Candidate names and admission numbers are absent from the assessment response. UUIDs are shown temporarily; no per-row student requests are made.</Alert>
				<Table.ScrollContainer minWidth={720}>
					<Table withTableBorder verticalSpacing="xs">
						<Table.Thead><Table.Tr><Table.Th>Candidate</Table.Th><Table.Th w={160}>Status</Table.Th><Table.Th w={120}>Mark</Table.Th><Table.Th w={70}>Grade</Table.Th><Table.Th>Remarks</Table.Th></Table.Tr></Table.Thead>
						<Table.Tbody>
							{rows.map(row=><Table.Tr key={row.id}>
								<Table.Td><Text size="sm" ff="monospace">{row.student}</Text></Table.Td>
								<Table.Td>
									<Select aria-label={`Mark status for ${row.student}`} size="xs" disabled={!canEdit} allowDeselect={false} data={markStatuses.map(s=>({value:s,label:label(s)}))} value={row.mark_status} onChange={value=>{if(!value)return;const mark_status=value as MarkStatus;update(row.student,{mark_status,score:mark_status==="SCORED"?row.score:null})}}/>
								</Table.Td>
								<Table.Td>
									<NumberInput aria-label={`Score for ${row.student}`} size="xs" disabled={!canEdit||row.mark_status!=="SCORED"} min={0} max={Number(assessment.max_marks)} decimalScale={2} value={row.score===null||row.score===""?"":Number(row.score)} onChange={value=>update(row.student,{score:value===""||value===undefined?"":String(value)})}/>
								</Table.Td>
								<Table.Td><Text size="sm" fw={600}>{row.grade||"—"}</Text></Table.Td>
								<Table.Td>
									<TextInput aria-label={`Remarks for ${row.student}`} size="xs" disabled={!canEdit} maxLength={240} value={row.remarks} onChange={e=>update(row.student,{remarks:e.currentTarget.value})}/>
								</Table.Td>
							</Table.Tr>)}
						</Table.Tbody>
					</Table>
				</Table.ScrollContainer>
				{!!detail.data?.grading_bands.length&&<Accordion variant="separated">
					<Accordion.Item value="bands">
						<Accordion.Control>Frozen grading scale ({detail.data.grading_bands.length} bands)</Accordion.Control>
						<Accordion.Panel>
							<Stack gap={4}>
								{detail.data.grading_bands.map(b=><Group key={b.id} justify="space-between"><Text size="sm" fw={600}>{b.grade_label}</Text><Text size="sm" c="dimmed">{b.min_percentage}%–{b.max_percentage}%</Text><Text size="sm">{b.remark}</Text></Group>)}
							</Stack>
						</Accordion.Panel>
					</Accordion.Item>
				</Accordion>}
				<Group gap="lg" wrap="wrap">
					<Text size="xs" c="dimmed">Submitted {when(assessment.submitted_at)}</Text>
					<Text size="xs" c="dimmed">Approved {when(assessment.approved_at)}</Text>
					<Text size="xs" c="dimmed">Published {when(assessment.published_at)}</Text>
					<Text size="xs" c="dimmed">Last amended {when(assessment.last_amended_at)}</Text>
				</Group>
			</Stack>}
		<ActionDialog open={!!transition} title={`${transition?label(transition):"Assessment"} assessment`} description={transition==="submit"?(missing?`${missing} candidate${missing===1?" is":"s are"} not marked. The backend will reject submission until each has a final mark status.`:"Unsaved marks will be saved first. Submission runs only after that save succeeds."):transition==="publish"?"Publishing exposes the approved results to downstream readers.":transition==="approve"?"Approve the submitted marks for publication.":"This transition is recorded in the assessment activity trail."} confirmLabel={transition?label(transition):"Confirm"} danger={transition==="reject"||transition==="reopen"} busy={workflow.isPending} onClose={()=>setTransition(null)} onSubmit={e=>{e.preventDefault();workflow.mutate()}}>
			{(transition==="reject"||transition==="reopen")&&<Textarea label="Reason" maxLength={240} value={reason} onChange={e=>setReason(e.currentTarget.value)}/>}
		</ActionDialog>
	</Modal>;
}
