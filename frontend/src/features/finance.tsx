import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Box, Button, Group, NumberInput, Select, Stack, Tabs, Text, Textarea, TextInput } from "@mantine/core";
import { api, ApiError, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { FilterBar } from "../components/FilterBar";
import { WorkspaceHeader } from "../components/WorkspaceHeader";
import { notify } from "../components/notifications/notify";
import { Empty, ErrorState, PageHeader, StatusBadge } from "../components/ui";

type Student={id:string;full_name:string;admission_number:string};
type Line={id:string;fee_item:string;fee_item_name:string;amount:string;is_required:boolean};
type FeeItemOption={id:string;category:string;name:string;code:string;is_optional:boolean;is_active:boolean};
type AcademicYear={id:string;name:string;starts_on:string;ends_on:string;is_current:boolean};
type AcademicLevel={id:string;name:string;code:string;sequence:number};
type PaymentMethod={id:string;name:string;code:string;is_active:boolean};
type Structure={id:string;name:string;academic_year:string;academic_level:string;is_active:boolean;is_approved:boolean;lines:Line[]};
type Assignment={id:string;student:string;student_name:string;fee_structure:string;fee_structure_name:string;status:string;assigned_at:string};
type Invoice={id:string;invoice_number:string;student:string;assignment:string;status:string;subtotal:string;discount_total:string;total:string;issued_at:string|null;created_at:string};
type Allocation={id:string;payment:string;invoice:string;amount:string;allocated_at:string};
type Payment={id:string;student:string;payment_method:string;amount:string;external_reference:string;status:string;received_at:string;receipt?:{receipt_number:string};allocations:Allocation[];reversal?:{reversal_number:string};allocated_amount:string;unallocated_amount:string};
type Incoming={id:string;payment_method:string;amount:string;external_reference:string;external_transaction_id:string;status:string;matched_payment:string|null;ignored_reason:string;received_at:string};
const kes=new Intl.NumberFormat("en-KE",{style:"currency",currency:"KES",minimumFractionDigits:2});
const cash=(v:string|number)=>kes.format(Number(v));
const when=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v)):"—";
function usePaged<T>(key:string,path:string,params?:Record<string,string|number|undefined>){const [page,setPage]=useState(1);const query=useQuery({queryKey:[key,page,params],queryFn:()=>api<Page<T>>(path,{params:{page,...params}})});return {page,setPage,query,rows:query.data?.results??[]};}
function Failure({error}:{error:unknown}){return error?<div className="form-error" role="alert">{error instanceof Error?error.message:"The action failed"}</div>:null}
const errorText=(error:unknown)=>error instanceof ApiError?error.message:error instanceof Error?error.message:"The action failed";

export function FinanceOverview(){return <><PageHeader eyebrow="Finance" title="Finance operations" description="Move from approved charges to invoices, collections and reconciled student accounts."/><div className="workflow-strip"><a href="/finance/fees"><b>1</b><span><strong>Set fees</strong><small>Approve charging schedules</small></span></a><a href="/finance/assignments"><b>2</b><span><strong>Assign & invoice</strong><small>Generate student charges</small></span></a><a href="/finance/payments"><b>3</b><span><strong>Collect & allocate</strong><small>Apply money to invoices</small></span></a><a href="/finance/incoming"><b>4</b><span><strong>Reconcile</strong><small>Resolve incoming money</small></span></a></div></>}

export function FeeStructuresPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const data=usePaged<Structure>("fee-structures","/finance/fee-structures/");
	const [selected,setSelected]=useState<Structure|null>(null);
	const approve=useMutation({mutationFn:(id:string)=>api(`/finance/fee-structures/${id}/approve/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["fee-structures"]});setSelected(null);notify.success("Fee structure approved")},onError:error=>notify.error("Fee structure could not be approved",error)});

	const canView=can("finance.fee_structure.view");
	const canCreate=can("finance.fee_structure.create");
	const [createOpen,setCreateOpen]=useState(false);
	const [name,setName]=useState("");
	const [year,setYear]=useState("");
	const [level,setLevel]=useState("");
	// Shared by the create dialog's pickers AND the table's year/level column
	// labels below, so a viewer without create rights still sees names, not
	// raw ids -- gated on the page's own view permission, not the create one.
	// page_size=100 is the real max_page_size AcademicsPagination enforces
	// (apps/academics/api.py:47-49), not an arbitrary frontend guess -- but
	// unlike that hard ceiling, nothing here checks `next`, so a tenant that
	// somehow accumulated more than 100 academic years/levels would still
	// silently see a truncated picker. Realistically far outside any school's
	// actual catalogue size, but noted rather than assumed safe forever.
	const years=useQuery({queryKey:["academic-years"],queryFn:()=>api<Page<AcademicYear>>("/academics/academic-years/",{params:{page_size:100}}),enabled:canView});
	const levels=useQuery({queryKey:["academic-levels"],queryFn:()=>api<Page<AcademicLevel>>("/academics/academic-levels/",{params:{page_size:100}}),enabled:canView});
	const yearName=(id:string)=>years.data?.results.find(y=>y.id===id)?.name??id;
	const levelName=(id:string)=>levels.data?.results.find(l=>l.id===id)?.name??id;
	const create=useMutation({mutationFn:()=>api<Structure>("/finance/fee-structures/",{method:"POST",body:JSON.stringify({name,academic_year:year,academic_level:level})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["fee-structures"]});setCreateOpen(false);setName("");setYear("");setLevel("");notify.success("Fee structure created")},onError:error=>notify.error("Fee structure could not be created",error)});

	// add_fee_structure_line raises "Approved fee structures cannot be edited"
	// once is_approved is true (services.py:63-64) -- this gate reflects that
	// real backend restriction, not an assumption about what approval "should"
	// mean.
	const canEdit=can("finance.fee_structure.edit");
	const [lineItem,setLineItem]=useState("");
	const [lineAmount,setLineAmount]=useState<number|"">("");
	const feeItems=useQuery({queryKey:["fee-items"],queryFn:()=>api<Page<FeeItemOption>>("/finance/fee-items/",{params:{page_size:100}}),enabled:canEdit});
	const addLine=useMutation({
		mutationFn:()=>api<Line>(`/finance/fee-structures/${selected!.id}/lines/`,{method:"POST",body:JSON.stringify({fee_item:lineItem,amount:lineAmount})}),
		onSuccess:newLine=>{setSelected(prev=>prev?{...prev,lines:[...prev.lines,newLine]}:prev);setLineItem("");setLineAmount("");void qc.invalidateQueries({queryKey:["fee-structures"]});notify.success("Fee line added")},
		onError:error=>notify.error("Fee line could not be added",error),
	});

	const columns:Column<Structure>[]=[
		{key:"name",header:"Structure",cell:r=><><Text size="sm" fw={600}>{r.name}</Text><Text size="xs" c="dimmed">{r.lines.length} fee lines</Text></>},
		{key:"year",header:"Academic year",cell:r=>yearName(r.academic_year)},
		{key:"level",header:"Level",cell:r=>levelName(r.academic_level)},
		{key:"total",header:"Total",cell:r=>cash(r.lines.reduce((n,l)=>n+Number(l.amount),0))},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.is_approved?"Approved":"Draft"}/>},
	];

	return <>
		<WorkspaceHeader title="Fee structures" description="Review charging schedules and approve them before assignment." action={canCreate?<Button onClick={()=>setCreateOpen(true)}>+ New structure</Button>:undefined}/>
		<DataTable title="Fee structures" columns={columns} rows={data.query.data?.results??[]} rowKey={r=>r.id} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} onRefresh={()=>void data.query.refetch()}
			count={data.query.data?.count} page={data.page} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage} onRow={setSelected}
		/>

		<ActionDialog open={createOpen} title="New fee structure" description="Choose the academic year and level this structure applies to." confirmLabel="Create structure" busy={create.isPending} onClose={()=>setCreateOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}>
			<Stack gap="sm">
				{create.error&&<Alert color="red" variant="light">{errorText(create.error)}</Alert>}
				<TextInput label="Name" required value={name} onChange={e=>setName(e.currentTarget.value)}/>
				<Select label="Academic year" required placeholder="Select academic year" data={years.data?.results.map(y=>({value:y.id,label:`${y.name}${y.is_current?" · Current":""}`}))??[]} value={year||null} onChange={value=>setYear(value??"")}/>
				<Select label="Level" required placeholder="Select level" data={levels.data?.results.map(l=>({value:l.id,label:l.name}))??[]} value={level||null} onChange={value=>setLevel(value??"")}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!selected} title={selected?.name??"Fee structure"} description="Approval makes this structure available for student assignment." confirmLabel="Approve structure" busy={approve.isPending} onClose={()=>setSelected(null)} onSubmit={e=>{e.preventDefault();if(selected&&!selected.is_approved)approve.mutate(selected.id)}}>
			<Stack gap="sm">
				{approve.error&&<Alert color="red" variant="light">{errorText(approve.error)}</Alert>}
				<Stack gap={4}>
					{selected?.lines.map(l=>
						<Group key={l.id} justify="space-between" wrap="nowrap">
							<Text size="sm">{l.fee_item_name}{l.is_required?" · Required":" · Optional"}</Text>
							<Text size="sm" fw={600}>{cash(l.amount)}</Text>
						</Group>
					)}
					{!selected?.lines.length&&<Text size="sm" c="dimmed">No lines yet.</Text>}
				</Stack>
				{selected?.is_approved&&<Text size="sm" c="dimmed">This structure is already approved.</Text>}
				{selected&&!selected.is_approved&&canEdit&&<Stack gap="sm" mt="sm">
					{addLine.error&&<Alert color="red" variant="light">{errorText(addLine.error)}</Alert>}
					<Select label="Fee item" placeholder="Select fee item" data={feeItems.data?.results.map(i=>({value:i.id,label:i.name}))??[]} value={lineItem||null} onChange={value=>setLineItem(value??"")}/>
					<NumberInput label="Amount (KES)" min={0.01} decimalScale={2} value={lineAmount} onChange={value=>setLineAmount(value===""||value===undefined?"":Number(value))}/>
					<Button variant="default" disabled={!lineItem||!lineAmount||addLine.isPending} onClick={()=>addLine.mutate()}>Add line</Button>
				</Stack>}
			</Stack>
		</ActionDialog>
	</>;
}

export function AssignmentsPage(){const {can}=useAccess();const qc=useQueryClient();const data=usePaged<Assignment>("assignments","/finance/student-fee-assignments/");const students=useQuery({queryKey:["assignment-students"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:can("finance.fee_structure.edit")});const structures=useQuery({queryKey:["assignment-structures"],queryFn:()=>api<Page<Structure>>("/finance/fee-structures/",{params:{page_size:100}}),enabled:can("finance.fee_structure.edit")});const [open,setOpen]=useState(false);const [student,setStudent]=useState("");const [structure,setStructure]=useState("");const create=useMutation({mutationFn:()=>api("/finance/student-fee-assignments/",{method:"POST",body:JSON.stringify({student,fee_structure:structure})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["assignments"]});setOpen(false);notify.success("Fee structure assigned to student")},onError:error=>notify.error("Fee assignment could not be created",error)});const generate=useMutation({mutationFn:(id:string)=>api(`/finance/student-fee-assignments/${id}/generate-invoice/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["assignments"]});void qc.invalidateQueries({queryKey:["invoices"]});notify.success("Invoice generated")},onError:error=>notify.error("Invoice could not be generated",error)});const columns:Column<Assignment>[]=[{key:"student",header:"Student",cell:r=><strong>{r.student_name}</strong>},{key:"structure",header:"Fee structure",cell:r=>r.fee_structure_name},{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},{key:"assigned",header:"Assigned",cell:r=>when(r.assigned_at)},{key:"action",header:"",cell:r=>can("finance.invoice.create")?<button className="button secondary" disabled={generate.isPending} onClick={e=>{e.stopPropagation();generate.mutate(r.id)}}>Generate invoice</button>:null}];return <><PageHeader eyebrow="Finance · Billing" title="Fee assignments" description="Assign an approved fee structure, then generate the student's draft invoice." action={can("finance.fee_structure.edit")?<button className="button primary" onClick={()=>setOpen(true)}>+ Assign fees</button>:undefined}/><Failure error={generate.error}/><DataTable {...data} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} columns={columns} rowKey={r=>r.id} count={data.query.data?.count} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage}/><ActionDialog open={open} title="Assign fee structure" description="Only approved structures are offered." confirmLabel="Assign fees" busy={create.isPending} onClose={()=>setOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}><Failure error={create.error}/><label>Student<select required value={student} onChange={e=>setStudent(e.target.value)}><option value="">Select student</option>{students.data?.results.map(s=><option key={s.id} value={s.id}>{s.full_name} · {s.admission_number}</option>)}</select></label><label>Approved fee structure<select required value={structure} onChange={e=>setStructure(e.target.value)}><option value="">Select structure</option>{structures.data?.results.filter(s=>s.is_approved).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label></ActionDialog></>}

export function InvoicesPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const [page,setPage]=useState(1);
	// Exact match only -- the backend has no search/name-resolution endpoint
	// (STUDENT-GAP-02), so this stays a raw id field rather than a picker that
	// would silently imply a complete, searchable student catalogue.
	const [studentId,setStudentId]=useState("");
	const invoices=useQuery({queryKey:["invoices",page,studentId],queryFn:()=>api<Page<Invoice>>("/finance/invoices/",{params:{page,student:studentId||undefined}})});
	const issue=useMutation({mutationFn:(id:string)=>api(`/finance/invoices/${id}/issue/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["invoices"]});notify.success("Invoice issued")},onError:error=>notify.error("Invoice could not be issued",error)});

	const [creditTarget,setCreditTarget]=useState<Invoice|null>(null);
	const [creditAmount,setCreditAmount]=useState<number|"">("");
	const [creditReason,setCreditReason]=useState("");
	const createCredit=useMutation({
		mutationFn:()=>api("/finance/credit-notes/",{method:"POST",body:JSON.stringify({student:creditTarget!.student,invoice:creditTarget!.id,amount:creditAmount,reason:creditReason})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["invoices"]});setCreditTarget(null);notify.success("Credit note issued")},
		onError:error=>notify.error("Credit note could not be issued",error),
	});

	const columns:Column<Invoice>[]=[
		{key:"number",header:"Invoice",cell:r=><Text size="sm" fw={600}>{r.invoice_number}</Text>},
		{key:"student",header:"Student",cell:r=><Text size="sm" ff="monospace">{r.student}</Text>},
		{key:"total",header:"Total",cell:r=>cash(r.total)},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},
		{key:"date",header:"Issued",cell:r=>when(r.issued_at)},
		{key:"action",header:"",cell:r=>{
			if(r.status==="DRAFT"&&can("finance.invoice.issue"))return <Button size="xs" variant="default" disabled={issue.isPending} onClick={e=>{e.stopPropagation();issue.mutate(r.id)}}>Issue invoice</Button>;
			// Not offered against DRAFT invoices (nothing has been charged yet -- fix
			// the assignment instead) and VOID has no transition anywhere in the API,
			// so this is deliberately ISSUED-only, matching the one real use case.
			if(r.status==="ISSUED"&&can("finance.credit_note.create"))return <Button size="xs" variant="default" onClick={e=>{e.stopPropagation();setCreditTarget(r);setCreditAmount("");setCreditReason("")}}>Create credit note</Button>;
			return null;
		}},
	];

	return <>
		<WorkspaceHeader title="Invoices" description="Review and issue student invoices."/>
		<DataTable title="Invoices" columns={columns} rows={invoices.data?.results??[]} rowKey={r=>r.id} loading={invoices.isLoading} error={invoices.error} retry={()=>void invoices.refetch()} onRefresh={()=>void invoices.refetch()}
			count={invoices.data?.count} page={page} previous={!!invoices.data?.previous} next={!!invoices.data?.next} onPage={setPage}
			toolbar={<FilterBar>
				<TextInput label="Student" placeholder="Exact student id" size="xs" w={300} value={studentId} onChange={e=>{setStudentId(e.currentTarget.value);setPage(1)}}/>
			</FilterBar>}
		/>
		<ActionDialog open={!!creditTarget} title="Create credit note" description={creditTarget?`Issues a credit note against invoice ${creditTarget.invoice_number}. This cannot be undone.`:undefined} confirmLabel="Create credit note" busy={createCredit.isPending} onClose={()=>setCreditTarget(null)} onSubmit={e=>{e.preventDefault();createCredit.mutate()}}>
			<Stack gap="sm">
				{createCredit.error&&<Alert color="red" variant="light">{errorText(createCredit.error)}</Alert>}
				<NumberInput label="Amount (KES)" required min={0.01} decimalScale={2} value={creditAmount} onChange={value=>setCreditAmount(value===""||value===undefined?"":Number(value))}/>
				<Textarea label="Reason" required maxLength={240} value={creditReason} onChange={e=>setCreditReason(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>
	</>;
}

export function PaymentsPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const [page,setPage]=useState(1);
	// Exact match only -- same reasoning as InvoicesPage: no search/name-resolution
	// endpoint exists (STUDENT-GAP-02), so this stays a raw id field.
	const [studentId,setStudentId]=useState("");
	const payments=useQuery({queryKey:["payments",page,studentId],queryFn:()=>api<Page<Payment>>("/finance/payments/",{params:{page,student:studentId||undefined}})});

	const [selected,setSelected]=useState<Payment|null>(null);
	const [mode,setMode]=useState<"allocate"|"reverse"|null>(null);
	const [invoice,setInvoice]=useState("");
	const [amount,setAmount]=useState<number|"">("");
	const [reason,setReason]=useState("");
	// Bounded and real (filtered to the one student this payment belongs to,
	// not a page of "all students") -- loaded as soon as a payment is opened
	// so the allocations list below can also resolve invoice numbers, not
	// just the Allocate dropdown.
	const invoices=useQuery({queryKey:["payment-invoices",selected?.student],queryFn:()=>api<Page<Invoice>>("/finance/invoices/",{params:{student:selected!.student,page_size:100}}),enabled:!!selected});
	const invoiceNumber=(id:string)=>invoices.data?.results.find(i=>i.id===id)?.invoice_number??id;
	const mutate=useMutation({mutationFn:()=>api(mode==="allocate"?`/finance/payments/${selected!.id}/allocate/`:`/finance/payments/${selected!.id}/reverse/`,{method:"POST",body:JSON.stringify(mode==="allocate"?{invoice,amount}:{reason})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["payments"]});const done=mode;setMode(null);setSelected(null);notify.success(done==="allocate"?"Payment allocated":"Payment reversed")},onError:error=>notify.error(mode==="allocate"?"Payment could not be allocated":"Payment could not be reversed",error)});

	// Allocation Reversal (finance.allocation.reverse) is deliberately kept
	// separate from whole-Payment Reversal above: it corrects one misapplied
	// allocation without touching the payment or its other allocations. The
	// embedded PaymentAllocationSerializer (identical shape in the list row
	// and in PaymentDetailView) exposes id/payment/invoice/amount/allocated_at
	// only -- no reversed-to-date figure -- so unlike Allocate (which has a
	// real unallocated_amount to cap against), this amount field has no
	// client-side max. The backend's "Reversal exceeds the allocation's
	// remaining amount" error is the authoritative guard.
	const [reversingAllocation,setReversingAllocation]=useState<Allocation|null>(null);
	const [allocationReversalAmount,setAllocationReversalAmount]=useState<number|"">("");
	const [allocationReversalReason,setAllocationReversalReason]=useState("");
	const reverseAllocation=useMutation({
		mutationFn:()=>api(`/finance/payment-allocations/${reversingAllocation!.id}/reverse/`,{method:"POST",body:JSON.stringify({amount:allocationReversalAmount,reason:allocationReversalReason})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["payments"]});setReversingAllocation(null);notify.success("Allocation reversed")},
		onError:error=>notify.error("Allocation could not be reversed",error),
	});

	const canRecord=can("finance.payment.record");
	const [recordOpen,setRecordOpen]=useState(false);
	const [payStudent,setPayStudent]=useState("");
	const [payMethod,setPayMethod]=useState("");
	const [payAmount,setPayAmount]=useState<number|"">("");
	const [payReference,setPayReference]=useState("");
	// A retry of the SAME attempt (validation error, fix a field, submit again)
	// must reuse this key so the backend's idempotency handling treats it as a
	// replay, not a second charge -- only a fresh "+ Record payment" click
	// starts a new attempt and gets a new key.
	const [idempotencyKey,setIdempotencyKey]=useState("");
	// Picker for the record-payment dialog only -- STUDENT-GAP-02 means there's
	// no bounded way to resolve an arbitrary student id to a name, so the table's
	// "Student" column below shows the raw id rather than guessing from this
	// (necessarily incomplete) page of students. Pre-existing limitation,
	// not solved by this migration -- carried forward, not silently hidden.
	const students=useQuery({queryKey:["payment-students-lookup"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:canRecord});
	const paymentMethods=useQuery({queryKey:["payment-methods"],queryFn:()=>api<Page<PaymentMethod>>("/finance/payment-methods/",{params:{page_size:100}}),enabled:canRecord});
	const openRecord=()=>{setIdempotencyKey(crypto.randomUUID());setPayStudent("");setPayMethod("");setPayAmount("");setPayReference("");setRecordOpen(true)};
	const record=useMutation({
		mutationFn:()=>api("/finance/payments/",{method:"POST",body:JSON.stringify({student:payStudent,payment_method:payMethod,amount:payAmount,idempotency_key:idempotencyKey,external_reference:payReference})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["payments"]});setRecordOpen(false);notify.success("Payment recorded successfully")},
		onError:error=>notify.error("Payment could not be recorded",error),
	});

	const columns:Column<Payment>[]=[
		{key:"receipt",header:"Receipt",cell:r=><Text size="sm" fw={600}>{r.receipt?.receipt_number??"Pending"}</Text>},
		{key:"student",header:"Student",cell:r=><Text size="sm" ff="monospace">{r.student}</Text>},
		{key:"amount",header:"Amount",cell:r=>cash(r.amount)},
		{key:"available",header:"Unallocated",cell:r=>cash(r.unallocated_amount)},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},
		{key:"date",header:"Received",cell:r=>when(r.received_at)},
	];

	return <>
		<WorkspaceHeader title="Payments" description="Open a received payment to allocate it or record a controlled reversal." action={canRecord?<Button onClick={openRecord}>+ Record payment</Button>:undefined}/>
		<DataTable title="Payments" columns={columns} rows={payments.data?.results??[]} rowKey={r=>r.id} loading={payments.isLoading} error={payments.error} retry={()=>void payments.refetch()} onRefresh={()=>void payments.refetch()}
			count={payments.data?.count} page={page} previous={!!payments.data?.previous} next={!!payments.data?.next} onPage={setPage} onRow={setSelected}
			toolbar={<FilterBar>
				<TextInput label="Student" placeholder="Exact student id" size="xs" w={300} value={studentId} onChange={e=>{setStudentId(e.currentTarget.value);setPage(1)}}/>
			</FilterBar>}
		/>

		<ActionDialog open={recordOpen} title="Record payment" description="Creates a received payment for a student, ready to allocate against an invoice." confirmLabel="Record payment" busy={record.isPending} onClose={()=>setRecordOpen(false)} onSubmit={e=>{e.preventDefault();record.mutate()}}>
			<Stack gap="sm">
				{record.error&&<Alert color="red" variant="light">{errorText(record.error)}</Alert>}
				<Select label="Student" required searchable data={students.data?.results.map(s=>({value:s.id,label:`${s.full_name} · ${s.admission_number}`}))??[]} value={payStudent||null} onChange={value=>setPayStudent(value??"")}/>
				<Select label="Payment method" required data={paymentMethods.data?.results.map(m=>({value:m.id,label:m.name}))??[]} value={payMethod||null} onChange={value=>setPayMethod(value??"")}/>
				<NumberInput label="Amount (KES)" required min={0.01} decimalScale={2} value={payAmount} onChange={value=>setPayAmount(value===""||value===undefined?"":Number(value))}/>
				<TextInput label="External reference (optional)" value={payReference} onChange={e=>setPayReference(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!selected&&!mode&&!reversingAllocation} title={selected?.receipt?.receipt_number??"Payment"} description={selected?`${cash(selected.amount)} received · ${cash(selected.unallocated_amount)} available`:undefined} confirmLabel="Close" onClose={()=>setSelected(null)} onSubmit={e=>{e.preventDefault();setSelected(null)}}>
			<Stack gap="sm">
				<Text size="sm" fw={600}>Allocations</Text>
				{!selected?.allocations.length&&<Text size="sm" c="dimmed">No allocations yet.</Text>}
				{selected?.allocations.map(a=>
					<Group key={a.id} justify="space-between" wrap="nowrap" align="flex-start">
						<Box>
							<Text size="sm">Invoice {invoiceNumber(a.invoice)}</Text>
							<Text size="xs" c="dimmed">{when(a.allocated_at)}</Text>
						</Box>
						<Group gap="xs" wrap="nowrap">
							<Text size="sm" fw={600}>{cash(a.amount)}</Text>
							{can("finance.allocation.reverse")&&selected.status==="RECEIVED"&&
								<Button size="xs" variant="subtle" color="red" onClick={()=>{setReversingAllocation(a);setAllocationReversalAmount("");setAllocationReversalReason("")}}>Reverse</Button>}
						</Group>
					</Group>
				)}
				{selected?.status==="RECEIVED"&&<Group gap="xs" mt="sm">
					{can("finance.payment.allocate")&&Number(selected.unallocated_amount)>0&&<Button onClick={()=>setMode("allocate")}>Allocate payment</Button>}
					{can("finance.payment.reverse")&&<Button color="red" variant="light" onClick={()=>setMode("reverse")}>Reverse payment</Button>}
				</Group>}
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!selected&&!!mode} title={mode==="allocate"?"Allocate payment":"Reverse payment"} description={mode==="reverse"?"Invalidates the payment and reverses every currently active allocation on it. A reason is required.":"Apply available money to one issued invoice."} confirmLabel={mode==="allocate"?"Allocate":"Reverse payment"} danger={mode==="reverse"} busy={mutate.isPending} onClose={()=>setMode(null)} onSubmit={e=>{e.preventDefault();mutate.mutate()}}>
			<Stack gap="sm">
				{mutate.error&&<Alert color="red" variant="light">{errorText(mutate.error)}</Alert>}
				{mode==="allocate"?<>
					<Select label="Invoice" required placeholder="Select issued invoice" data={invoices.data?.results.filter(i=>i.status==="ISSUED").map(i=>({value:i.id,label:`${i.invoice_number} · ${cash(i.total)}`}))??[]} value={invoice||null} onChange={value=>setInvoice(value??"")}/>
					<NumberInput label="Amount (KES)" required min={0.01} max={selected?Number(selected.unallocated_amount):undefined} decimalScale={2} value={amount} onChange={value=>setAmount(value===""||value===undefined?"":Number(value))}/>
				</>:
					<Textarea label="Reason" required maxLength={240} value={reason} onChange={e=>setReason(e.currentTarget.value)}/>}
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!reversingAllocation} title="Reverse allocation" description={reversingAllocation?`Reverses money allocated to invoice ${invoiceNumber(reversingAllocation.invoice)} on ${when(reversingAllocation.allocated_at)}. This affects only this one allocation -- the payment itself and its other allocations are not touched.`:undefined} confirmLabel="Reverse allocation" danger busy={reverseAllocation.isPending} onClose={()=>setReversingAllocation(null)} onSubmit={e=>{e.preventDefault();reverseAllocation.mutate()}}>
			<Stack gap="sm">
				{reverseAllocation.error&&<Alert color="red" variant="light">{errorText(reverseAllocation.error)}</Alert>}
				{reversingAllocation&&<Text size="sm" c="dimmed">Originally allocated: {cash(reversingAllocation.amount)}</Text>}
				<NumberInput label="Amount to reverse (KES)" required min={0.01} decimalScale={2} value={allocationReversalAmount} onChange={value=>setAllocationReversalAmount(value===""||value===undefined?"":Number(value))}/>
				<Textarea label="Reason" required maxLength={240} value={allocationReversalReason} onChange={e=>setAllocationReversalReason(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>
	</>;
}

const INCOMING_TABS=[{value:"UNMATCHED",label:"Unmatched"},{value:"MATCHED",label:"Matched"},{value:"IGNORED",label:"Ignored"}];

export function IncomingPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const [status,setStatus]=useState("UNMATCHED");
	const [page,setPage]=useState(1);
	// Plain date input -- converted to an ISO datetime at midnight UTC before
	// it reaches the API, matching the exact format the backend's own test
	// exercises (api_tests.py: "received_after=2999-01-01T00:00:00Z"), rather
	// than relying on Django's undocumented bare-date fallback parsing.
	const [receivedAfter,setReceivedAfter]=useState("");
	const incoming=useQuery({queryKey:["incoming",status,page,receivedAfter],queryFn:()=>api<Page<Incoming>>("/finance/incoming-payments/",{params:{status,page,received_after:receivedAfter?`${receivedAfter}T00:00:00Z`:undefined}})});

	const [matchTarget,setMatchTarget]=useState<Incoming|null>(null);
	// The match serializer takes only a student id -- no bounded/searchable
	// student catalogue exists (STUDENT-GAP-02), so unlike the old dropdown
	// (a page_size:100 fetch presented as if it were the complete roster),
	// this stays a raw exact-id field. Flagged rather than carried forward.
	const [matchStudentId,setMatchStudentId]=useState("");
	const match=useMutation({
		mutationFn:()=>api(`/finance/incoming-payments/${matchTarget!.id}/match/`,{method:"POST",body:JSON.stringify({student:matchStudentId})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["incoming"]});setMatchTarget(null);notify.success("Incoming payment matched")},
		onError:error=>notify.error("Incoming payment could not be matched",error),
	});

	const [ignoreTarget,setIgnoreTarget]=useState<Incoming|null>(null);
	const [ignoreReason,setIgnoreReason]=useState("");
	const ignore=useMutation({
		mutationFn:()=>api(`/finance/incoming-payments/${ignoreTarget!.id}/ignore/`,{method:"POST",body:JSON.stringify({reason:ignoreReason})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["incoming"]});setIgnoreTarget(null);notify.success("Incoming payment ignored")},
		onError:error=>notify.error("Incoming payment could not be ignored",error),
	});

	const paymentMethods=useQuery({queryKey:["incoming-payment-methods-lookup"],queryFn:()=>api<Page<PaymentMethod>>("/finance/payment-methods/",{params:{page_size:100}}),enabled:can("finance.reconciliation.view")});
	const methodName=(id:string)=>paymentMethods.data?.results.find(m=>m.id===id)?.name??id;

	const columns:Column<Incoming>[]=[
		{key:"reference",header:"Reference",cell:r=><><Text size="sm" fw={600}>{r.external_transaction_id}</Text><Text size="xs" c="dimmed">{r.external_reference||"No account reference"}</Text></>},
		{key:"amount",header:"Amount",cell:r=>cash(r.amount)},
		{key:"method",header:"Payment method",cell:r=>methodName(r.payment_method)},
		{key:"status",header:"Status",cell:r=><><StatusBadge value={r.status}/>{r.status==="IGNORED"&&r.ignored_reason&&<Text size="xs" c="dimmed" mt={2}>{r.ignored_reason}</Text>}{r.status==="MATCHED"&&r.matched_payment&&<Text size="xs" c="dimmed" ff="monospace" mt={2}>Payment {r.matched_payment}</Text>}</>},
		{key:"date",header:"Received",cell:r=>when(r.received_at)},
		{key:"action",header:"",cell:r=>r.status==="UNMATCHED"?<Group gap="xs" wrap="nowrap">
			{can("finance.reconciliation.match")&&<Button size="xs" variant="default" onClick={e=>{e.stopPropagation();setMatchTarget(r);setMatchStudentId("")}}>Match</Button>}
			{can("finance.reconciliation.ignore")&&<Button size="xs" variant="default" color="red" onClick={e=>{e.stopPropagation();setIgnoreTarget(r);setIgnoreReason("")}}>Ignore</Button>}
		</Group>:null},
	];

	return <>
		<WorkspaceHeader title="Incoming payments" description="Review and reconcile incoming payment records."/>
		<Tabs value={status} onChange={v=>{if(v){setStatus(v);setPage(1)}}} mb="md">
			<Tabs.List>
				{INCOMING_TABS.map(t=><Tabs.Tab key={t.value} value={t.value}>{t.label}</Tabs.Tab>)}
			</Tabs.List>
		</Tabs>
		<DataTable title="Incoming payments" columns={columns} rows={incoming.data?.results??[]} rowKey={r=>r.id} loading={incoming.isLoading} error={incoming.error} retry={()=>void incoming.refetch()} onRefresh={()=>void incoming.refetch()}
			count={incoming.data?.count} page={page} previous={!!incoming.data?.previous} next={!!incoming.data?.next} onPage={setPage}
			toolbar={<FilterBar>
				<TextInput type="date" label="Received after" size="xs" w={170} value={receivedAfter} onChange={e=>{setReceivedAfter(e.currentTarget.value);setPage(1)}}/>
				{receivedAfter&&<Button variant="subtle" size="xs" mt={22} onClick={()=>{setReceivedAfter("");setPage(1)}}>Clear</Button>}
			</FilterBar>}
		/>

		<ActionDialog open={!!matchTarget} title="Match incoming payment" description={matchTarget?`Creates a real student payment for ${cash(matchTarget.amount)}. Check the student id carefully -- this cannot be undone from here.`:undefined} confirmLabel="Confirm match" busy={match.isPending} onClose={()=>setMatchTarget(null)} onSubmit={e=>{e.preventDefault();match.mutate()}}>
			<Stack gap="sm">
				{match.error&&<Alert color="red" variant="light">{errorText(match.error)}</Alert>}
				<TextInput label="Student id" required placeholder="Exact student id" value={matchStudentId} onChange={e=>setMatchStudentId(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!ignoreTarget} title="Ignore incoming payment" description="The entry remains in the audit trail and will not become a payment." confirmLabel="Ignore entry" danger busy={ignore.isPending} onClose={()=>setIgnoreTarget(null)} onSubmit={e=>{e.preventDefault();ignore.mutate()}}>
			<Stack gap="sm">
				{ignore.error&&<Alert color="red" variant="light">{errorText(ignore.error)}</Alert>}
				<Textarea label="Reason" required maxLength={240} value={ignoreReason} onChange={e=>setIgnoreReason(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>
	</>;
}
