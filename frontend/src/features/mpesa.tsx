import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Code, Group, NumberInput, ScrollArea, Select, Stack, Text, Textarea, TextInput } from "@mantine/core";
import { IconArrowRight } from "@tabler/icons-react";
import { api, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { DetailDrawer } from "../components/DetailDrawer";
import { FilterBar } from "../components/FilterBar";
import { KeyValueGrid, KeyValueItem, KeyValueSection } from "../components/KeyValueGrid";
import { notify } from "../components/notifications/notify";
import { RecordTabs } from "../components/RecordTabs";
import { Loading, StatusBadge } from "../components/ui";
import { WorkspaceHeader } from "../components/WorkspaceHeader";

type Student={id:string;full_name:string;admission_number:string};
type Stk={id:string;student:string;invoice:string|null;phone_number:string;amount:string;idempotency_key:string;status:string;checkout_request_id:string|null;merchant_request_id:string;confirmed_receipt:string|null;incoming_payment:string|null;result_code:string;result_description:string;queried_at:string|null;created_at:string};
type Callback={id:string;callback_type:string;provider_transaction_id:string;request_id:string|null;status:string;created_at:string;verified_by:string|null;verified_at:string|null;verification_reference:string;processed_at:string|null;attempts:number;last_error:string;raw_payload?:unknown};
const cash=(v:string)=>new Intl.NumberFormat("en-KE",{style:"currency",currency:"KES"}).format(Number(v));
const when=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v)):"—";
function Failure({error}:{error:unknown}){if(!error)return null;return <Alert color="red" variant="light" mb="sm">{error instanceof Error?error.message:"The action failed"}</Alert>}

const STK_TAB="STK requests", CALLBACK_TAB="Callback security queue";
const CALLBACK_STATUSES=[{value:"RECEIVED",label:"Received"},{value:"PROCESSED",label:"Processed"},{value:"FAILED",label:"Failed"},{value:"REJECTED",label:"Rejected"}];
const TRUST_STEPS=[{label:"Received",sub:"Untrusted payload"},{label:"Verify",sub:"Operator evidence"},{label:"Process",sub:"Create incoming payment"},{label:"Reconcile",sub:"Match student"}];

export function MpesaPage(){
 const {can}=useAccess();
 const [tab,setTab]=useState(can("finance.mpesa.stk_push.view")?STK_TAB:CALLBACK_TAB);
 return <>
  <WorkspaceHeader title="M-Pesa operations" description="Track customer prompts separately from the secure callback verification queue."/>
  <RecordTabs tabs={[STK_TAB,CALLBACK_TAB]} value={tab} onChange={setTab}/>
  {tab===STK_TAB?<StkWorkspace/>:<CallbackWorkspace/>}
 </>;
}

function StkWorkspace(){
 const {can}=useAccess();
 const qc=useQueryClient();
 const [page,setPage]=useState(1);
 const q=useQuery({queryKey:["stk",page],queryFn:()=>api<Page<Stk>>("/finance/mpesa/stk-requests/",{params:{page}})});
 const [open,setOpen]=useState(false);
 const [selected,setSelected]=useState<Stk|null>(null);
 const [identifyTarget,setIdentifyTarget]=useState<Stk|null>(null);
 const [form,setForm]=useState({student:"",phone_number:"",amount:"",idempotency_key:""});
 const [identifyForm,setIdentifyForm]=useState({checkout_request_id:"",merchant_request_id:"",evidence:""});
 const students=useQuery({queryKey:["stk-students"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:open});
 const initiate=useMutation({mutationFn:()=>api("/finance/mpesa/stk-push/",{method:"POST",body:JSON.stringify(form)}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["stk"]});setOpen(false);notify.success("M-Pesa STK push sent")},onError:error=>notify.error("M-Pesa STK push could not be sent",error)});
 const query=useMutation({mutationFn:(id:string)=>api(`/finance/mpesa/stk-requests/${id}/query/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["stk"]});notify.success("Provider status refreshed")},onError:error=>notify.error("Provider status could not be refreshed",error)});
 const identify=useMutation({mutationFn:()=>api(`/finance/mpesa/stk-requests/${identifyTarget!.id}/identify/`,{method:"POST",body:JSON.stringify(identifyForm)}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["stk"]});setIdentifyTarget(null);notify.success("STK request identified")},onError:error=>notify.error("STK request could not be identified",error)});
 const columns:Column<Stk>[]=[
  {key:"phone",header:"Phone",cell:r=><><Text size="sm" fw={500}>{r.phone_number}</Text><Text size="xs" c="dimmed">{r.checkout_request_id??"Awaiting provider ID"}</Text></>},
  {key:"amount",header:"Amount",cell:r=>cash(r.amount)},
  {key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},
  {key:"receipt",header:"Confirmed receipt",cell:r=>r.confirmed_receipt??"—"},
  {key:"created",header:"Created",cell:r=>when(r.created_at)},
 ];
 return <>
  <Group justify="space-between" align="flex-start" mb="sm" wrap="wrap">
   <Text size="sm" c="dimmed" maw={520}>A completed STK request points to an incoming payment; reconciliation remains a separate step.</Text>
   {can("finance.mpesa.stk_push.initiate")&&<Button onClick={()=>{setForm({student:"",phone_number:"",amount:"",idempotency_key:crypto.randomUUID()});setOpen(true)}}>+ Request payment</Button>}
  </Group>
  <Failure error={query.error}/>
  <DataTable title="STK requests" columns={columns} rows={q.data?.results??[]} rowKey={r=>r.id} loading={q.isLoading} error={q.error} retry={()=>void q.refetch()} onRefresh={()=>void q.refetch()} onRow={setSelected}
   page={page} count={q.data?.count} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage}
   rowActions={r=><Group gap={6} wrap="nowrap">
    {can("finance.mpesa.stk_push.query")&&["PENDING","UNKNOWN"].includes(r.status)&&<Button size="xs" variant="default" loading={query.isPending} onClick={e=>{e.stopPropagation();query.mutate(r.id)}}>Query</Button>}
    {can("finance.mpesa.stk_push.reconcile")&&["INITIATING","UNKNOWN"].includes(r.status)&&<Button size="xs" variant="default" onClick={e=>{e.stopPropagation();setIdentifyForm({checkout_request_id:"",merchant_request_id:"",evidence:""});setIdentifyTarget(r)}}>Identify</Button>}
   </Group>}/>
  <ActionDialog open={open} title="Send STK prompt" description="The request starts a provider workflow. It does not confirm receipt of money." confirmLabel="Send prompt" busy={initiate.isPending} onClose={()=>setOpen(false)} onSubmit={(e:FormEvent)=>{e.preventDefault();initiate.mutate()}}>
   <Failure error={initiate.error}/>
   <Stack gap="sm">
    <Select label="Student" required placeholder="Select student" data={students.data?.results.map(s=>({value:s.id,label:`${s.full_name} · ${s.admission_number}`}))??[]} value={form.student||null} onChange={value=>setForm({...form,student:value??""})}/>
    <TextInput label="Phone number" required placeholder="2547XXXXXXXX" value={form.phone_number} onChange={e=>setForm({...form,phone_number:e.target.value})}/>
    <NumberInput label="Amount" required min={1} step={1} value={form.amount===""?"":Number(form.amount)} onChange={value=>setForm({...form,amount:value===""||value===undefined?"":String(value)})}/>
   </Stack>
  </ActionDialog>
  <ActionDialog open={!!selected} title="STK request" description="Provider and reconciliation identifiers are shown for investigation." onClose={()=>setSelected(null)} onSubmit={e=>{e.preventDefault();setSelected(null)}}>
   {selected&&<KeyValueGrid>
    <KeyValueItem label="Status" value={<StatusBadge value={selected.status}/>}/>
    <KeyValueItem label="Checkout request" value={<Code>{selected.checkout_request_id??"—"}</Code>}/>
    <KeyValueItem label="Merchant request" value={<Code>{selected.merchant_request_id||"—"}</Code>}/>
    <KeyValueItem label="Incoming payment" value={<Code>{selected.incoming_payment??"Not created"}</Code>}/>
    <KeyValueItem label="Result" value={selected.result_description||"—"}/>
   </KeyValueGrid>}
  </ActionDialog>
  <ActionDialog open={!!identifyTarget} title="Identify STK request" description="Record the provider identifiers found through an independent check (e.g. the M-Pesa dashboard). This does not send a second prompt or post a payment." confirmLabel="Record identification" busy={identify.isPending} onClose={()=>setIdentifyTarget(null)} onSubmit={(e:FormEvent)=>{e.preventDefault();identify.mutate()}}>
   <Failure error={identify.error}/>
   <Stack gap="sm">
    <TextInput label="Checkout request ID" required value={identifyForm.checkout_request_id} onChange={e=>setIdentifyForm({...identifyForm,checkout_request_id:e.target.value})}/>
    <TextInput label="Merchant request ID" required value={identifyForm.merchant_request_id} onChange={e=>setIdentifyForm({...identifyForm,merchant_request_id:e.target.value})}/>
    <Textarea label="Evidence" description="What you independently checked to confirm these identifiers." required maxLength={240} value={identifyForm.evidence} onChange={e=>setIdentifyForm({...identifyForm,evidence:e.target.value})}/>
   </Stack>
  </ActionDialog>
 </>;
}

function CallbackWorkspace(){
 const {can}=useAccess();
 const qc=useQueryClient();
 const [page,setPage]=useState(1);
 const [status,setStatus]=useState("RECEIVED");
 const q=useQuery({queryKey:["callbacks",page,status],queryFn:()=>api<Page<Callback>>("/finance/mpesa/callbacks/",{params:{page,status}})});
 const [selected,setSelected]=useState<Callback|null>(null);
 const detail=useQuery({queryKey:["callback",selected?.id],queryFn:()=>api<Callback>(`/finance/mpesa/callbacks/${selected!.id}/`),enabled:!!selected});
 const [mode,setMode]=useState<"verify"|"reject"|null>(null);
 const [evidence,setEvidence]=useState("");
 const mutate=useMutation({mutationFn:()=>api(`/finance/mpesa/callbacks/${selected!.id}/${mode}/`,{method:"POST",body:JSON.stringify(mode==="verify"?{evidence}:{reason:evidence})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["callbacks"]});void qc.invalidateQueries({queryKey:["callback"]});const done=mode;setMode(null);setSelected(null);notify.success(done==="verify"?"M-Pesa callback verified":"M-Pesa callback rejected")},onError:error=>notify.error(mode==="verify"?"M-Pesa callback verification failed":"M-Pesa callback could not be rejected",error)});
 const process=useMutation({mutationFn:(id:string)=>api(`/finance/mpesa/callbacks/${id}/process/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["callbacks"]});setSelected(null);notify.success("M-Pesa callback processed")},onError:error=>notify.error("M-Pesa callback could not be processed",error)});
 const columns:Column<Callback>[]=[
  {key:"type",header:"Callback",cell:r=><><Text size="sm" fw={500}>{r.callback_type.replaceAll("_"," ")}</Text><Text size="xs" c="dimmed">{r.provider_transaction_id||"No transaction ID"}</Text></>},
  {key:"state",header:"Trust state",cell:r=><StatusBadge value={r.status}/>},
  {key:"attempts",header:"Attempts",cell:r=>r.attempts},
  {key:"created",header:"Received",cell:r=>when(r.created_at)},
  {key:"error",header:"Last error",cell:r=>r.last_error||"—"},
 ];
 return <>
  <Group gap={10} mb="md" wrap="wrap">
   {TRUST_STEPS.map((step,i)=><Group key={step.label} gap={10} wrap="nowrap">
    {i>0&&<IconArrowRight size={14} style={{color:"var(--mantine-color-gray-5)"}}/>}
    <Stack gap={0}><Text size="xs" fw={600}>{step.label}</Text><Text fz={10} c="dimmed">{step.sub}</Text></Stack>
   </Group>)}
  </Group>
  <DataTable title="Callback security queue" columns={columns} rows={q.data?.results??[]} rowKey={r=>r.id} loading={q.isLoading} error={q.error} retry={()=>void q.refetch()} onRefresh={()=>void q.refetch()} onRow={setSelected}
   page={page} count={q.data?.count} previous={!!q.data?.previous} next={!!q.data?.next} onPage={setPage}
   toolbar={<FilterBar><Select aria-label="Filter by status" data={CALLBACK_STATUSES} value={status} onChange={value=>{setStatus(value??"RECEIVED");setPage(1)}} w={160} allowDeselect={false}/></FilterBar>}/>
  <DetailDrawer open={!!selected} title="Inspect callback" description="RECEIVED means the payload was stored. It is not evidence that money was received." onClose={()=>setSelected(null)}>
   {detail.isLoading?<Loading label="Loading callback"/>:detail.data&&<Stack gap="md">
    <Failure error={process.error}/>
    <KeyValueSection title="Transaction">
     <KeyValueGrid>
      <KeyValueItem label="Type" value={detail.data.callback_type}/>
      <KeyValueItem label="Provider transaction" value={<Code>{detail.data.provider_transaction_id||"—"}</Code>}/>
      <KeyValueItem label="Request" value={<Code>{detail.data.request_id??"—"}</Code>}/>
      <KeyValueItem label="Status" value={<StatusBadge value={detail.data.status}/>}/>
     </KeyValueGrid>
    </KeyValueSection>
    <KeyValueSection title="Verification">
     <KeyValueGrid>
      <KeyValueItem label="Verified by" value={detail.data.verified_by??"—"}/>
      <KeyValueItem label="Verified at" value={when(detail.data.verified_at)}/>
      <KeyValueItem label="Reference" value={detail.data.verification_reference||"—"}/>
     </KeyValueGrid>
    </KeyValueSection>
    <Stack gap={4}>
     <Text size="xs" fw={600} tt="uppercase" c="dimmed">Raw payload</Text>
     <ScrollArea.Autosize mah={220}><Code block>{JSON.stringify(detail.data.raw_payload,null,2)}</Code></ScrollArea.Autosize>
    </Stack>
    <Group gap="xs">
     {detail.data.status==="RECEIVED"&&can("finance.mpesa.callback.verify")&&<Button onClick={()=>{setEvidence("");setMode("verify")}}>Verify</Button>}
     {detail.data.status==="RECEIVED"&&can("finance.mpesa.callback.verify")&&<Button color="red" variant="light" onClick={()=>{setEvidence("");setMode("reject")}}>Reject</Button>}
     {detail.data.verified_at&&detail.data.status==="RECEIVED"&&can("finance.mpesa.callback.process")&&<Button loading={process.isPending} onClick={()=>process.mutate(detail.data!.id)}>Process verified callback</Button>}
    </Group>
   </Stack>}
  </DetailDrawer>
  <ActionDialog open={!!mode} title={mode==="verify"?"Verify callback":"Reject callback"} description={mode==="verify"?"Record the independent evidence used to trust this payload. Verification still does not create a payment.":"Record why this callback must never be processed."} confirmLabel={mode==="verify"?"Record verification":"Reject callback"} danger={mode==="reject"} busy={mutate.isPending} onClose={()=>setMode(null)} onSubmit={e=>{e.preventDefault();mutate.mutate()}}>
   <Failure error={mutate.error}/>
   <Textarea label={mode==="verify"?"Verification evidence":"Rejection reason"} required maxLength={240} value={evidence} onChange={e=>setEvidence(e.target.value)}/>
  </ActionDialog>
 </>;
}
