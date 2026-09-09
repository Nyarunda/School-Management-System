import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
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

export function FinanceOverview(){return <><PageHeader eyebrow="Finance" title="Finance operations" description="Move from approved charges to invoices, collections and reconciled student accounts."/><div className="workflow-strip"><a href="/finance/fees"><b>1</b><span><strong>Set fees</strong><small>Approve charging schedules</small></span></a><a href="/finance/assignments"><b>2</b><span><strong>Assign & invoice</strong><small>Generate student charges</small></span></a><a href="/finance/payments"><b>3</b><span><strong>Collect & allocate</strong><small>Apply money to invoices</small></span></a><a href="/finance/incoming"><b>4</b><span><strong>Reconcile</strong><small>Resolve incoming money</small></span></a></div></>}

export function FeeStructuresPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const q=useQuery({queryKey:["fee-structures"],queryFn:()=>api<Page<Structure>>("/finance/fee-structures/")});
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
	const years=useQuery({queryKey:["academic-years"],queryFn:()=>api<Page<AcademicYear>>("/academics/academic-years/",{params:{page_size:100}}),enabled:canView});
	const levels=useQuery({queryKey:["academic-levels"],queryFn:()=>api<Page<AcademicLevel>>("/academics/academic-levels/",{params:{page_size:100}}),enabled:canView});
	const yearName=(id:string)=>years.data?.results.find(y=>y.id===id)?.name??id;
	const levelName=(id:string)=>levels.data?.results.find(l=>l.id===id)?.name??id;
	const create=useMutation({mutationFn:()=>api<Structure>("/finance/fee-structures/",{method:"POST",body:JSON.stringify({name,academic_year:year,academic_level:level})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["fee-structures"]});setCreateOpen(false);setName("");setYear("");setLevel("");notify.success("Fee structure created")},onError:error=>notify.error("Fee structure could not be created",error)});

	const canEdit=can("finance.fee_structure.edit");
	const [lineItem,setLineItem]=useState("");
	const [lineAmount,setLineAmount]=useState("");
	const feeItems=useQuery({queryKey:["fee-items"],queryFn:()=>api<Page<FeeItemOption>>("/finance/fee-items/",{params:{page_size:100}}),enabled:canEdit});
	const addLine=useMutation({
		mutationFn:()=>api<Line>(`/finance/fee-structures/${selected!.id}/lines/`,{method:"POST",body:JSON.stringify({fee_item:lineItem,amount:lineAmount})}),
		onSuccess:newLine=>{setSelected(prev=>prev?{...prev,lines:[...prev.lines,newLine]}:prev);setLineItem("");setLineAmount("");void qc.invalidateQueries({queryKey:["fee-structures"]});notify.success("Fee line added")},
		onError:error=>notify.error("Fee line could not be added",error),
	});

	const columns:Column<Structure>[]=[{key:"name",header:"Structure",cell:r=><><strong>{r.name}</strong><small className="cell-sub">{r.lines.length} fee lines</small></>},{key:"year",header:"Academic year",cell:r=>yearName(r.academic_year)},{key:"level",header:"Level",cell:r=>levelName(r.academic_level)},{key:"total",header:"Total",cell:r=>cash(r.lines.reduce((n,l)=>n+Number(l.amount),0))},{key:"status",header:"Status",cell:r=><StatusBadge value={r.is_approved?"Approved":"Draft"}/>}];

	return <>
		<PageHeader eyebrow="Finance · Setup" title="Fee structures" description="Review charging schedules and approve them before assignment." action={canCreate?<button className="button primary" onClick={()=>setCreateOpen(true)}>+ New structure</button>:undefined}/>
		<DataTable columns={columns} rows={q.data?.results??[]} rowKey={r=>r.id} loading={q.isLoading} error={q.error} retry={()=>void q.refetch()} count={q.data?.count} onRow={setSelected}/>

		<ActionDialog open={createOpen} title="New fee structure" description="Choose the academic year and level this structure applies to." confirmLabel="Create structure" busy={create.isPending} onClose={()=>setCreateOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}>
			<Failure error={create.error}/>
			<label>Name<input required value={name} onChange={e=>setName(e.target.value)}/></label>
			<label>Academic year<select required value={year} onChange={e=>setYear(e.target.value)}><option value="">Select academic year</option>{years.data?.results.map(y=><option key={y.id} value={y.id}>{y.name}{y.is_current?" · Current":""}</option>)}</select></label>
			<label>Level<select required value={level} onChange={e=>setLevel(e.target.value)}><option value="">Select level</option>{levels.data?.results.map(l=><option key={l.id} value={l.id}>{l.name}</option>)}</select></label>
		</ActionDialog>

		<ActionDialog open={!!selected} title={selected?.name??"Fee structure"} description="Approval makes this structure available for student assignment." confirmLabel="Approve structure" busy={approve.isPending} onClose={()=>setSelected(null)} onSubmit={e=>{e.preventDefault();if(selected&&!selected.is_approved)approve.mutate(selected.id)}}>
			<Failure error={approve.error}/>
			{selected&&<div className="detail-list">{selected.lines.map(l=><div key={l.id}><span>{l.fee_item_name}{l.is_required?" · Required":" · Optional"}</span><strong>{cash(l.amount)}</strong></div>)}</div>}
			{selected?.is_approved&&<p className="notice">This structure is already approved.</p>}
			{selected&&!selected.is_approved&&canEdit&&<div className="detail-list">
				<Failure error={addLine.error}/>
				<label>Fee item<select value={lineItem} onChange={e=>setLineItem(e.target.value)}><option value="">Select fee item</option>{feeItems.data?.results.map(i=><option key={i.id} value={i.id}>{i.name}</option>)}</select></label>
				<label>Amount<input type="number" min="0.01" step="0.01" value={lineAmount} onChange={e=>setLineAmount(e.target.value)}/></label>
				<button type="button" className="button secondary" disabled={!lineItem||!lineAmount||addLine.isPending} onClick={()=>addLine.mutate()}>Add line</button>
			</div>}
		</ActionDialog>
	</>;
}

export function AssignmentsPage(){const {can}=useAccess();const qc=useQueryClient();const data=usePaged<Assignment>("assignments","/finance/student-fee-assignments/");const students=useQuery({queryKey:["assignment-students"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:can("finance.fee_structure.edit")});const structures=useQuery({queryKey:["assignment-structures"],queryFn:()=>api<Page<Structure>>("/finance/fee-structures/",{params:{page_size:100}}),enabled:can("finance.fee_structure.edit")});const [open,setOpen]=useState(false);const [student,setStudent]=useState("");const [structure,setStructure]=useState("");const create=useMutation({mutationFn:()=>api("/finance/student-fee-assignments/",{method:"POST",body:JSON.stringify({student,fee_structure:structure})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["assignments"]});setOpen(false);notify.success("Fee structure assigned to student")},onError:error=>notify.error("Fee assignment could not be created",error)});const generate=useMutation({mutationFn:(id:string)=>api(`/finance/student-fee-assignments/${id}/generate-invoice/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["assignments"]});void qc.invalidateQueries({queryKey:["invoices"]});notify.success("Invoice generated")},onError:error=>notify.error("Invoice could not be generated",error)});const columns:Column<Assignment>[]=[{key:"student",header:"Student",cell:r=><strong>{r.student_name}</strong>},{key:"structure",header:"Fee structure",cell:r=>r.fee_structure_name},{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},{key:"assigned",header:"Assigned",cell:r=>when(r.assigned_at)},{key:"action",header:"",cell:r=>can("finance.invoice.create")?<button className="button secondary" disabled={generate.isPending} onClick={e=>{e.stopPropagation();generate.mutate(r.id)}}>Generate invoice</button>:null}];return <><PageHeader eyebrow="Finance · Billing" title="Fee assignments" description="Assign an approved fee structure, then generate the student's draft invoice." action={can("finance.fee_structure.edit")?<button className="button primary" onClick={()=>setOpen(true)}>+ Assign fees</button>:undefined}/><Failure error={generate.error}/><DataTable {...data} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} columns={columns} rowKey={r=>r.id} count={data.query.data?.count} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage}/><ActionDialog open={open} title="Assign fee structure" description="Only approved structures are offered." confirmLabel="Assign fees" busy={create.isPending} onClose={()=>setOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}><Failure error={create.error}/><label>Student<select required value={student} onChange={e=>setStudent(e.target.value)}><option value="">Select student</option>{students.data?.results.map(s=><option key={s.id} value={s.id}>{s.full_name} · {s.admission_number}</option>)}</select></label><label>Approved fee structure<select required value={structure} onChange={e=>setStructure(e.target.value)}><option value="">Select structure</option>{structures.data?.results.filter(s=>s.is_approved).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label></ActionDialog></>}

export function InvoicesPage(){const {can}=useAccess();const qc=useQueryClient();const data=usePaged<Invoice>("invoices","/finance/invoices/");const students=useQuery({queryKey:["invoice-students-lookup"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:can("finance.invoice.view")});const studentLabel=(id:string)=>{const s=students.data?.results.find(x=>x.id===id);return s?`${s.full_name} · ${s.admission_number}`:id};const issue=useMutation({mutationFn:(id:string)=>api(`/finance/invoices/${id}/issue/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["invoices"]});notify.success("Invoice issued")},onError:error=>notify.error("Invoice could not be issued",error)});const columns:Column<Invoice>[]=[{key:"number",header:"Invoice",cell:r=><strong>{r.invoice_number}</strong>},{key:"student",header:"Student",cell:r=>studentLabel(r.student)},{key:"total",header:"Total",cell:r=>cash(r.total)},{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},{key:"date",header:"Issued",cell:r=>when(r.issued_at)},{key:"action",header:"",cell:r=>r.status==="DRAFT"&&can("finance.invoice.issue")?<button className="button secondary" disabled={issue.isPending} onClick={()=>issue.mutate(r.id)}>Issue invoice</button>:null}];return <><PageHeader eyebrow="Finance · Billing" title="Invoices" description="Issue draft invoices only after checking the student and total."/><Failure error={issue.error}/><DataTable {...data} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} columns={columns} rowKey={r=>r.id} count={data.query.data?.count} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage}/></>}

export function PaymentsPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const data=usePaged<Payment>("payments","/finance/payments/");
	const [selected,setSelected]=useState<Payment|null>(null);
	const [mode,setMode]=useState<"allocate"|"reverse"|null>(null);
	const [invoice,setInvoice]=useState("");
	const [amount,setAmount]=useState("");
	const [reason,setReason]=useState("");
	const invoices=useQuery({queryKey:["payment-invoices",selected?.student],queryFn:()=>api<Page<Invoice>>("/finance/invoices/",{params:{student:selected!.student,page_size:100}}),enabled:mode==="allocate"&&!!selected});
	const mutate=useMutation({mutationFn:()=>api(mode==="allocate"?`/finance/payments/${selected!.id}/allocate/`:`/finance/payments/${selected!.id}/reverse/`,{method:"POST",body:JSON.stringify(mode==="allocate"?{invoice,amount}:{reason})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["payments"]});const done=mode;setMode(null);setSelected(null);notify.success(done==="allocate"?"Payment allocated":"Payment reversed")},onError:error=>notify.error(mode==="allocate"?"Payment could not be allocated":"Payment could not be reversed",error)});

	const canRecord=can("finance.payment.record");
	const [recordOpen,setRecordOpen]=useState(false);
	const [payStudent,setPayStudent]=useState("");
	const [payMethod,setPayMethod]=useState("");
	const [payAmount,setPayAmount]=useState("");
	const [payReference,setPayReference]=useState("");
	// A retry of the SAME attempt (validation error, fix a field, submit again)
	// must reuse this key so the backend's idempotency handling treats it as a
	// replay, not a second charge -- only a fresh "+ Record payment" click
	// starts a new attempt and gets a new key.
	const [idempotencyKey,setIdempotencyKey]=useState("");
	// Shared by the record-payment dialog's picker AND the table's "Student"
	// column label below.
	const students=useQuery({queryKey:["payment-students-lookup"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:can("finance.payment.view")});
	const studentLabel=(id:string)=>{const s=students.data?.results.find(x=>x.id===id);return s?`${s.full_name} · ${s.admission_number}`:id};
	const paymentMethods=useQuery({queryKey:["payment-methods"],queryFn:()=>api<Page<PaymentMethod>>("/finance/payment-methods/",{params:{page_size:100}}),enabled:canRecord});
	const openRecord=()=>{setIdempotencyKey(crypto.randomUUID());setPayStudent("");setPayMethod("");setPayAmount("");setPayReference("");setRecordOpen(true)};
	const record=useMutation({
		mutationFn:()=>api("/finance/payments/",{method:"POST",body:JSON.stringify({student:payStudent,payment_method:payMethod,amount:payAmount,idempotency_key:idempotencyKey,external_reference:payReference})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["payments"]});setRecordOpen(false);notify.success("Payment recorded successfully")},
		onError:error=>notify.error("Payment could not be recorded",error),
	});

	const columns:Column<Payment>[]=[{key:"receipt",header:"Receipt",cell:r=><strong>{r.receipt?.receipt_number??"Pending"}</strong>},{key:"student",header:"Student",cell:r=>studentLabel(r.student)},{key:"amount",header:"Amount",cell:r=>cash(r.amount)},{key:"available",header:"Unallocated",cell:r=>cash(r.unallocated_amount)},{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},{key:"date",header:"Received",cell:r=>when(r.received_at)}];

	return <>
		<PageHeader eyebrow="Finance · Collections" title="Payments" description="Open a received payment to allocate it or record a controlled reversal." action={canRecord?<button className="button primary" onClick={openRecord}>+ Record payment</button>:undefined}/>
		<DataTable {...data} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} columns={columns} rowKey={r=>r.id} count={data.query.data?.count} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage} onRow={setSelected}/>

		<ActionDialog open={recordOpen} title="Record payment" description="Creates a received payment for a student, ready to allocate against an invoice." confirmLabel="Record payment" busy={record.isPending} onClose={()=>setRecordOpen(false)} onSubmit={e=>{e.preventDefault();record.mutate()}}>
			<Failure error={record.error}/>
			<label>Student<select required value={payStudent} onChange={e=>setPayStudent(e.target.value)}><option value="">Select student</option>{students.data?.results.map(s=><option key={s.id} value={s.id}>{s.full_name} · {s.admission_number}</option>)}</select></label>
			<label>Payment method<select required value={payMethod} onChange={e=>setPayMethod(e.target.value)}><option value="">Select payment method</option>{paymentMethods.data?.results.map(m=><option key={m.id} value={m.id}>{m.name}</option>)}</select></label>
			<label>Amount<input required type="number" min="0.01" step="0.01" value={payAmount} onChange={e=>setPayAmount(e.target.value)}/></label>
			<label>External reference (optional)<input value={payReference} onChange={e=>setPayReference(e.target.value)}/></label>
		</ActionDialog>

		<ActionDialog open={!!selected&&!mode} title={selected?.receipt?.receipt_number??"Payment"} description={selected?`${cash(selected.amount)} received · ${cash(selected.unallocated_amount)} available`:undefined} confirmLabel="Close" onClose={()=>setSelected(null)} onSubmit={e=>{e.preventDefault();setSelected(null)}}><div className="detail-list">{selected?.allocations.map(a=><div key={a.id}><span>Invoice {a.invoice}</span><strong>{cash(a.amount)}</strong></div>)}</div>{selected?.status==="RECEIVED"&&<div className="choice-actions">{can("finance.payment.allocate")&&Number(selected.unallocated_amount)>0&&<button type="button" className="button primary" onClick={()=>setMode("allocate")}>Allocate payment</button>}{can("finance.payment.reverse")&&<button type="button" className="button danger" onClick={()=>setMode("reverse")}>Reverse payment</button>}</div>}</ActionDialog><ActionDialog open={!!selected&&!!mode} title={mode==="allocate"?"Allocate payment":"Reverse payment"} description={mode==="reverse"?"This invalidates the payment and reverses its active allocations. A reason is required.":"Apply available money to one issued invoice."} confirmLabel={mode==="allocate"?"Allocate":"Reverse payment"} danger={mode==="reverse"} busy={mutate.isPending} onClose={()=>setMode(null)} onSubmit={e=>{e.preventDefault();mutate.mutate()}}><Failure error={mutate.error}/>{mode==="allocate"?<><label>Invoice<select required value={invoice} onChange={e=>setInvoice(e.target.value)}><option value="">Select issued invoice</option>{invoices.data?.results.filter(i=>i.status==="ISSUED").map(i=><option key={i.id} value={i.id}>{i.invoice_number} · {cash(i.total)}</option>)}</select></label><label>Amount<input required type="number" min="0.01" step="0.01" max={selected?.unallocated_amount} value={amount} onChange={e=>setAmount(e.target.value)}/></label></>:<label>Reason<textarea required maxLength={240} value={reason} onChange={e=>setReason(e.target.value)}/></label>}</ActionDialog>
	</>;
}

export function IncomingPage(){const {can}=useAccess();const qc=useQueryClient();const [status,setStatus]=useState("UNMATCHED");const data=usePaged<Incoming>("incoming","/finance/incoming-payments/",{status});const [selected,setSelected]=useState<Incoming|null>(null);const [mode,setMode]=useState<"match"|"ignore"|null>(null);const [student,setStudent]=useState("");const [reason,setReason]=useState("");const students=useQuery({queryKey:["reconciliation-students"],queryFn:()=>api<Page<Student>>("/students/",{params:{page_size:100}}),enabled:mode==="match"});const paymentMethods=useQuery({queryKey:["incoming-payment-methods-lookup"],queryFn:()=>api<Page<PaymentMethod>>("/finance/payment-methods/",{params:{page_size:100}}),enabled:can("finance.reconciliation.view")});const methodName=(id:string)=>paymentMethods.data?.results.find(m=>m.id===id)?.name??id;const action=useMutation({mutationFn:()=>api(`/finance/incoming-payments/${selected!.id}/${mode}/`,{method:"POST",body:JSON.stringify(mode==="match"?{student}:{reason})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["incoming"]});const done=mode;setSelected(null);setMode(null);notify.success(done==="match"?"Incoming payment matched":"Incoming payment ignored")},onError:error=>notify.error(mode==="match"?"Incoming payment could not be matched":"Incoming payment could not be ignored",error)});const columns:Column<Incoming>[]=[{key:"reference",header:"Reference",cell:r=><><strong>{r.external_transaction_id}</strong><small className="cell-sub">{r.external_reference||"No account reference"}</small></>},{key:"amount",header:"Amount",cell:r=>cash(r.amount)},{key:"method",header:"Payment method",cell:r=>methodName(r.payment_method)},{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},{key:"date",header:"Received",cell:r=>when(r.received_at)}];return <><PageHeader eyebrow="Finance · Reconciliation" title="Incoming payments" description="Unmatched entries are a holding queue. They become student payments only after matching."/><DataTable {...data} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} columns={columns} rowKey={r=>r.id} count={data.query.data?.count} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage} onRow={r=>r.status==="UNMATCHED"&&setSelected(r)} toolbar={<label>Status <select value={status} onChange={e=>{setStatus(e.target.value);data.setPage(1)}}><option value="UNMATCHED">Unmatched</option><option value="MATCHED">Matched</option><option value="IGNORED">Ignored</option></select></label>}/><ActionDialog open={!!selected&&!mode} title={selected?.external_transaction_id??"Incoming payment"} description={selected?`${cash(selected.amount)} is not yet confirmed against a student account.`:undefined} onClose={()=>setSelected(null)} onSubmit={e=>{e.preventDefault();setSelected(null)}}><div className="choice-actions">{can("finance.reconciliation.match")&&<button type="button" className="button primary" onClick={()=>setMode("match")}>Match to student</button>}{can("finance.reconciliation.ignore")&&<button type="button" className="button danger" onClick={()=>setMode("ignore")}>Ignore entry</button>}</div></ActionDialog><ActionDialog open={!!mode} title={mode==="match"?"Match incoming payment":"Ignore incoming payment"} description={mode==="match"?"This creates a real student payment. Check the student carefully.":"The entry remains in the audit trail and will not become a payment."} confirmLabel={mode==="match"?"Confirm match":"Ignore entry"} danger={mode==="ignore"} busy={action.isPending} onClose={()=>setMode(null)} onSubmit={e=>{e.preventDefault();action.mutate()}}><Failure error={action.error}/>{mode==="match"?<label>Student<select required value={student} onChange={e=>setStudent(e.target.value)}><option value="">Select student</option>{students.data?.results.map(s=><option key={s.id} value={s.id}>{s.full_name} · {s.admission_number}</option>)}</select></label>:<label>Reason<textarea required maxLength={240} value={reason} onChange={e=>setReason(e.target.value)}/></label>}</ActionDialog></>}
