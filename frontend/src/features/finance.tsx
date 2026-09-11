import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Box, Button, Checkbox, Group, NumberInput, Select, Stack, Tabs, Text, Textarea, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { api, Page } from "../api/client";
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
type FeeCategoryRow={id:string;name:string;code:string;is_active:boolean};
type AcademicYear={id:string;name:string;starts_on:string;ends_on:string;is_current:boolean};
type AcademicLevel={id:string;name:string;code:string;sequence:number};
type AcademicTerm={id:string;academic_year:string;name:string;starts_on:string;ends_on:string;sequence:number};
type PaymentMethod={id:string;name:string;code:string;is_active:boolean};
type Structure={id:string;name:string;academic_year:string;academic_level:string;term:string;is_active:boolean;is_approved:boolean;lines:Line[]};
type ClassGroupOption={id:string;name:string;code:string;stream:string;academic_level:string};
type BulkAssignResult={students_matched:number;assignments_created:number;invoices_created:number;failures:{student:string;reason:string}[]};
type BulkTermInvoiceResult={assignments_matched:number;invoices_created:number;failures:{student:string;reason:string}[]};
type Assignment={id:string;student:string;student_name:string;fee_structure:string;fee_structure_name:string;status:string;assigned_at:string};
type Invoice={id:string;invoice_number:string;student:string;assignment:string;status:string;subtotal:string;discount_total:string;total:string;issued_at:string|null;created_at:string};
type Allocation={id:string;payment:string;invoice:string;amount:string;allocated_at:string};
type Payment={id:string;student:string;payment_method:string;amount:string;external_reference:string;status:string;received_at:string;receipt?:{receipt_number:string};allocations:Allocation[];reversal?:{reversal_number:string};allocated_amount:string;unallocated_amount:string};
type Incoming={id:string;payment_method:string;amount:string;external_reference:string;external_transaction_id:string;status:string;matched_payment:string|null;ignored_reason:string;received_at:string};
const kes=new Intl.NumberFormat("en-KE",{style:"currency",currency:"KES",minimumFractionDigits:2});
const cash=(v:string|number)=>kes.format(Number(v));
const when=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v)):"—";
function usePaged<T>(key:string,path:string,params?:Record<string,string|number|undefined>){const [page,setPage]=useState(1);const query=useQuery({queryKey:[key,page,params],queryFn:()=>api<Page<T>>(path,{params:{page,...params}})});return {page,setPage,query,rows:query.data?.results??[]};}

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
	const [term,setTerm]=useState("");
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
	const terms=useQuery({queryKey:["academic-terms"],queryFn:()=>api<Page<AcademicTerm>>("/academics/terms/",{params:{page_size:100}}),enabled:canView});
	const yearName=(id:string)=>years.data?.results.find(y=>y.id===id)?.name??id;
	const levelName=(id:string)=>levels.data?.results.find(l=>l.id===id)?.name??id;
	const termName=(id:string)=>terms.data?.results.find(t=>t.id===id)?.name??id;
	// A term only ever belongs to one academic year -- scoping the picker to
	// the year already chosen avoids offering a combination create_fee_structure
	// would reject server-side ("Term must belong to the selected academic year").
	const termsForYear=(terms.data?.results??[]).filter(t=>t.academic_year===year);
	const create=useMutation({mutationFn:()=>api<Structure>("/finance/fee-structures/",{method:"POST",body:JSON.stringify({name,academic_year:year,academic_level:level,term})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["fee-structures"]});setCreateOpen(false);setName("");setYear("");setLevel("");setTerm("");notify.success("Fee structure created")},onError:error=>notify.error("Fee structure could not be created",error)});

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

	// Bulk rollout: assigns the (approved) structure to every actively
	// enrolled student in its own academic year + level -- or just one class
	// within it -- and generates each of their invoices in the same action.
	// The catalogue endpoint (not attendance's teacher-scoped one) so a bursar
	// sees every class in the level, not just ones they teach.
	const [bulkClassGroup,setBulkClassGroup]=useState("");
	const classGroups=useQuery({queryKey:["class-groups-catalogue",selected?.academic_level],queryFn:()=>api<Page<ClassGroupOption>>("/academics/class-groups/catalogue/",{params:{academic_level:selected!.academic_level,page_size:100}}),enabled:!!selected?.is_approved});
	const bulkAssign=useMutation({
		mutationFn:()=>api<BulkAssignResult>(`/finance/fee-structures/${selected!.id}/bulk-assign/`,{method:"POST",body:JSON.stringify(bulkClassGroup?{class_group:bulkClassGroup}:{})}),
		onSuccess:result=>{
			void qc.invalidateQueries({queryKey:["fee-structures"]});
			void qc.invalidateQueries({queryKey:["assignments"]});
			void qc.invalidateQueries({queryKey:["invoices"]});
			setSelected(null);
			setBulkClassGroup("");
			const summary=`Matched ${result.students_matched} students -- ${result.assignments_created} new assignments, ${result.invoices_created} new invoices`;
			if(result.failures.length)notify.warning(`${summary}. ${result.failures.length} invoice(s) failed: ${result.failures.map(f=>`${f.student} (${f.reason})`).join("; ")}`);
			else notify.success(summary);
		},
		onError:error=>notify.error("Bulk assignment could not be completed",error),
	});

	const columns:Column<Structure>[]=[
		{key:"name",header:"Structure",cell:r=><><Text size="sm" fw={600}>{r.name}</Text><Text size="xs" c="dimmed">{r.lines.length} fee lines</Text></>},
		{key:"year",header:"Academic year",cell:r=>yearName(r.academic_year)},
		{key:"term",header:"Term",cell:r=>termName(r.term)},
		{key:"level",header:"Level",cell:r=>levelName(r.academic_level)},
		{key:"total",header:"Total",cell:r=>cash(r.lines.reduce((n,l)=>n+Number(l.amount),0))},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.is_approved?"Approved":"Draft"}/>},
	];

	return <>
		<WorkspaceHeader title="Fee structures" description="Review charging schedules and approve them before assignment." action={canCreate?<Button onClick={()=>setCreateOpen(true)}>+ New structure</Button>:undefined}/>
		<DataTable title="Fee structures" columns={columns} rows={data.query.data?.results??[]} rowKey={r=>r.id} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} onRefresh={()=>void data.query.refetch()}
			count={data.query.data?.count} page={data.page} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage} onRow={setSelected}
		/>

		<ActionDialog open={createOpen} title="New fee structure" description="Choose the academic year, term and level this structure applies to." confirmLabel="Create structure" busy={create.isPending} onClose={()=>setCreateOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}>
			<Stack gap="sm">
				<TextInput label="Name" required value={name} onChange={e=>setName(e.currentTarget.value)}/>
				<Select label="Academic year" required placeholder="Select academic year" data={years.data?.results.map(y=>({value:y.id,label:`${y.name}${y.is_current?" · Current":""}`}))??[]} value={year||null} onChange={value=>{setYear(value??"");setTerm("")}}/>
				<Select label="Term" required placeholder={year?"Select term":"Select an academic year first"} disabled={!year} data={termsForYear.map(t=>({value:t.id,label:t.name}))} value={term||null} onChange={value=>setTerm(value??"")}/>
				<Select label="Level" required placeholder="Select level" data={levels.data?.results.map(l=>({value:l.id,label:l.name}))??[]} value={level||null} onChange={value=>setLevel(value??"")}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!selected} title={selected?.name??"Fee structure"} description="Approval makes this structure available for student assignment." confirmLabel="Approve structure" busy={approve.isPending} onClose={()=>{setSelected(null);setBulkClassGroup("")}} onSubmit={e=>{e.preventDefault();if(selected&&!selected.is_approved)approve.mutate(selected.id)}}>
			<Stack gap="sm">
				<Stack gap={4}>
					{selected?.lines.map(l=>
						<Group key={l.id} justify="space-between" wrap="nowrap">
							<Text size="sm">{l.fee_item_name}{l.is_required?" · Required":" · Optional"}</Text>
							<Text size="sm" fw={600}>{cash(l.amount)}</Text>
						</Group>
					)}
					{!selected?.lines.length&&<Text size="sm" c="dimmed">No lines yet.</Text>}
				</Stack>
				{selected?.is_approved&&can("finance.invoice.create")&&<Stack gap="sm" mt="sm">
					<Text size="sm" c="dimmed">Assign this structure and generate invoices for every actively enrolled student in this level, or just one class.</Text>
					<Select label="Class" placeholder={classGroups.isLoading?"Loading classes…":"Entire level (every class)"} clearable data={classGroups.data?.results.map(c=>({value:c.id,label:`${c.name}${c.stream?" · "+c.stream:""}`}))??[]} value={bulkClassGroup||null} onChange={value=>setBulkClassGroup(value??"")}/>
					<Button disabled={bulkAssign.isPending} onClick={()=>bulkAssign.mutate()}>{bulkClassGroup?"Assign & invoice this class":"Assign & invoice entire level"}</Button>
				</Stack>}
				{selected&&!selected.is_approved&&canEdit&&<Stack gap="sm" mt="sm">
					<Select label="Fee item" placeholder="Select fee item" data={feeItems.data?.results.map(i=>({value:i.id,label:i.name}))??[]} value={lineItem||null} onChange={value=>setLineItem(value??"")}/>
					<NumberInput label="Amount (KES)" min={0.01} decimalScale={2} value={lineAmount} onChange={value=>setLineAmount(value===""||value===undefined?"":Number(value))}/>
					<Button variant="default" disabled={!lineItem||!lineAmount||addLine.isPending} onClick={()=>addLine.mutate()}>Add line</Button>
				</Stack>}
			</Stack>
		</ActionDialog>
	</>;
}

export function AssignmentsPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const data=usePaged<Assignment>("assignments","/finance/student-fee-assignments/");
	// AssignmentListCreateView.create() (api.py:292-297) and assign_fee_structure()
	// (services.py:94-95) both require finance.invoice.create -- the old gate
	// here was finance.fee_structure.edit, which incorrectly coupled "who can
	// assign fees" to "who can edit fee-structure lines". Fixed to match the
	// permission actually enforced server-side.
	const canAssign=can("finance.invoice.create");
	// FeeStructure catalogue: real max_page_size=100 (FinancePagination, same
	// ceiling as the academic-year/level catalogues), and fee structures are
	// an administratively-created, small catalogue -- realistically never
	// near 100 per tenant. Kept as a dropdown, filtered to is_approved (the
	// one real backend restriction: assign_fee_structure rejects an
	// unapproved structure with "Only approved fee structures can be
	// assigned"; is_active is never checked, so it's not filtered on here).
	const structures=useQuery({queryKey:["assignment-structures"],queryFn:()=>api<Page<Structure>>("/finance/fee-structures/",{params:{page_size:100}}),enabled:canAssign});
	const terms=useQuery({queryKey:["academic-terms"],queryFn:()=>api<Page<AcademicTerm>>("/academics/terms/",{params:{page_size:100}}),enabled:canAssign});
	const termName=(id:string)=>terms.data?.results.find(t=>t.id===id)?.name??id;
	const [open,setOpen]=useState(false);
	// Student: a raw exact-id field, not the old page_size:100 dropdown --
	// students are an unbounded, realistically >100 population per tenant
	// (STUDENT-GAP-02), unlike fee structures above. Flagged and dropped
	// rather than carried forward, matching the same call made for Incoming
	// Payments' Match dialog.
	const [studentId,setStudentId]=useState("");
	const [structure,setStructure]=useState("");
	const create=useMutation({mutationFn:()=>api("/finance/student-fee-assignments/",{method:"POST",body:JSON.stringify({student:studentId,fee_structure:structure})}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["assignments"]});setOpen(false);notify.success("Fee structure assigned to student")},onError:error=>notify.error("Fee assignment could not be created",error)});
	const generate=useMutation({mutationFn:(id:string)=>api(`/finance/student-fee-assignments/${id}/generate-invoice/`,{method:"POST"}),onSuccess:()=>{void qc.invalidateQueries({queryKey:["assignments"]});void qc.invalidateQueries({queryKey:["invoices"]});notify.success("Invoice generated")},onError:error=>notify.error("Invoice could not be generated",error)});

	// Term-wide catch-up: sweeps every active assignment across every
	// class/level whose fee structure belongs to the chosen term and invoices
	// whatever doesn't already have one -- the "close out this term's billing"
	// action, independent of how each assignment was created (individually,
	// or via a fee structure's own Assign & invoice bulk action).
	const [termOpen,setTermOpen]=useState(false);
	const [generateTerm,setGenerateTerm]=useState("");
	const generateForTerm=useMutation({
		mutationFn:()=>api<BulkTermInvoiceResult>(`/finance/terms/${generateTerm}/generate-invoices/`,{method:"POST"}),
		onSuccess:result=>{
			void qc.invalidateQueries({queryKey:["assignments"]});
			void qc.invalidateQueries({queryKey:["invoices"]});
			setTermOpen(false);
			setGenerateTerm("");
			const summary=`Checked ${result.assignments_matched} assignments -- ${result.invoices_created} new invoices`;
			if(result.failures.length)notify.warning(`${summary}. ${result.failures.length} failed: ${result.failures.map(f=>`${f.student} (${f.reason})`).join("; ")}`);
			else notify.success(summary);
		},
		onError:error=>notify.error("Term invoices could not be generated",error),
	});

	const columns:Column<Assignment>[]=[
		{key:"student",header:"Student",cell:r=><Text size="sm" fw={600}>{r.student_name}</Text>},
		{key:"structure",header:"Fee structure",cell:r=>r.fee_structure_name},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.status}/>},
		{key:"assigned",header:"Assigned",cell:r=>when(r.assigned_at)},
		{key:"action",header:"",cell:r=>can("finance.invoice.create")?<Button size="xs" variant="default" disabled={generate.isPending} onClick={e=>{e.stopPropagation();generate.mutate(r.id)}}>Generate invoice</Button>:null},
	];

	return <>
		<WorkspaceHeader title="Fee assignments" description="Assign an approved fee structure, then generate the student's draft invoice." action={canAssign?<Group gap="sm"><Button variant="default" onClick={()=>{setGenerateTerm("");setTermOpen(true)}}>Generate invoices for term</Button><Button onClick={()=>{setStudentId("");setStructure("");setOpen(true)}}>+ Assign fees</Button></Group>:undefined}/>
		<DataTable title="Fee assignments" columns={columns} rows={data.query.data?.results??[]} rowKey={r=>r.id} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} onRefresh={()=>void data.query.refetch()}
			count={data.query.data?.count} page={data.page} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage}
		/>

		<ActionDialog open={termOpen} title="Generate invoices for term" description="Generates an invoice for every active fee assignment in the chosen term that doesn't already have one, across every class and level." confirmLabel="Generate invoices" busy={generateForTerm.isPending} onClose={()=>setTermOpen(false)} onSubmit={e=>{e.preventDefault();if(generateTerm)generateForTerm.mutate()}}>
			<Stack gap="sm">
				<Select label="Term" required placeholder="Select term" data={terms.data?.results.map(t=>({value:t.id,label:t.name}))??[]} value={generateTerm||null} onChange={value=>setGenerateTerm(value??"")}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={open} title="Assign fee structure" description="Only approved structures are offered. Assigning the same student and structure again is safe -- it returns the existing assignment rather than creating a duplicate." confirmLabel="Assign fees" busy={create.isPending} onClose={()=>setOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}>
			<Stack gap="sm">
				<TextInput label="Student id" required placeholder="Exact student id" value={studentId} onChange={e=>setStudentId(e.currentTarget.value)}/>
				<Select label="Approved fee structure" required placeholder="Select structure" data={structures.data?.results.filter(s=>s.is_approved).map(s=>({value:s.id,label:`${s.name} · ${termName(s.term)}`}))??[]} value={structure||null} onChange={value=>setStructure(value??"")}/>
			</Stack>
		</ActionDialog>
	</>;
}

// The "Add line" dialog on FeeStructuresPage (above) can only ever offer fee
// items a tenant already has -- FeeItemListCreateView/FeeCategoryListCreateView
// have always existed on the backend, but nothing in the frontend could create
// a category or item, so a real (non-seeded) tenant hit an empty, permanently
// unselectable dropdown and could never add a fee line. This page is that
// missing catalogue setup screen.
export function FeeItemsPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const canManage=can("finance.setup.manage");
	const categoriesData=usePaged<FeeCategoryRow>("fee-categories","/finance/fee-categories/");
	const itemsData=usePaged<FeeItemOption>("fee-items","/finance/fee-items/");
	// Unpaginated lookup for the item dialog's category picker -- categories are
	// an administratively-created, small catalogue, same reasoning as the
	// academic-year/level pickers on FeeStructuresPage above.
	const allCategories=useQuery({queryKey:["fee-categories-lookup"],queryFn:()=>api<Page<FeeCategoryRow>>("/finance/fee-categories/",{params:{page_size:100}}),enabled:canManage});
	const categoryName=(id:string)=>allCategories.data?.results.find(c=>c.id===id)?.name??id;

	const [categoryOpen,setCategoryOpen]=useState(false);
	const [newCategoryName,setNewCategoryName]=useState("");
	const [newCategoryCode,setNewCategoryCode]=useState("");
	const createCategory=useMutation({
		mutationFn:()=>api<FeeCategoryRow>("/finance/fee-categories/",{method:"POST",body:JSON.stringify({name:newCategoryName,code:newCategoryCode})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["fee-categories"]});void qc.invalidateQueries({queryKey:["fee-categories-lookup"]});setCategoryOpen(false);setNewCategoryName("");setNewCategoryCode("");notify.success("Fee category created")},
		onError:error=>notify.error("Fee category could not be created",error),
	});

	const [itemOpen,setItemOpen]=useState(false);
	const [itemCategory,setItemCategory]=useState("");
	const [itemName,setItemName]=useState("");
	const [itemCode,setItemCode]=useState("");
	const [itemOptional,setItemOptional]=useState(false);
	const createItem=useMutation({
		mutationFn:()=>api<FeeItemOption>("/finance/fee-items/",{method:"POST",body:JSON.stringify({category:itemCategory,name:itemName,code:itemCode,is_optional:itemOptional})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["fee-items"]});setItemOpen(false);setItemCategory("");setItemName("");setItemCode("");setItemOptional(false);notify.success("Fee item created")},
		onError:error=>notify.error("Fee item could not be created",error),
	});

	const categoryColumns:Column<FeeCategoryRow>[]=[
		{key:"name",header:"Category",cell:r=><Text size="sm" fw={600}>{r.name}</Text>},
		{key:"code",header:"Code",cell:r=>r.code},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.is_active?"Active":"Inactive"}/>},
	];
	const itemColumns:Column<FeeItemOption>[]=[
		{key:"name",header:"Fee item",cell:r=><Text size="sm" fw={600}>{r.name}</Text>},
		{key:"category",header:"Category",cell:r=>categoryName(r.category)},
		{key:"code",header:"Code",cell:r=>r.code},
		{key:"optional",header:"Requirement",cell:r=>r.is_optional?"Optional":"Required"},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.is_active?"Active":"Inactive"}/>},
	];

	return <>
		<WorkspaceHeader title="Fee items" description="Define the fee categories and billable items used to build fee structure lines." action={canManage?<Group gap="sm"><Button variant="default" onClick={()=>{setNewCategoryName("");setNewCategoryCode("");setCategoryOpen(true)}}>+ New category</Button><Button onClick={()=>{setItemCategory("");setItemName("");setItemCode("");setItemOptional(false);setItemOpen(true)}}>+ New item</Button></Group>:undefined}/>
		<DataTable title="Fee categories" columns={categoryColumns} rows={categoriesData.query.data?.results??[]} rowKey={r=>r.id} loading={categoriesData.query.isLoading} error={categoriesData.query.error} retry={()=>void categoriesData.query.refetch()} onRefresh={()=>void categoriesData.query.refetch()}
			count={categoriesData.query.data?.count} page={categoriesData.page} previous={!!categoriesData.query.data?.previous} next={!!categoriesData.query.data?.next} onPage={categoriesData.setPage}
		/>
		<DataTable title="Fee items" columns={itemColumns} rows={itemsData.query.data?.results??[]} rowKey={r=>r.id} loading={itemsData.query.isLoading} error={itemsData.query.error} retry={()=>void itemsData.query.refetch()} onRefresh={()=>void itemsData.query.refetch()}
			count={itemsData.query.data?.count} page={itemsData.page} previous={!!itemsData.query.data?.previous} next={!!itemsData.query.data?.next} onPage={itemsData.setPage}
		/>

		<ActionDialog open={categoryOpen} title="New fee category" description="Categories group related fee items, e.g. Tuition or Transport." confirmLabel="Create category" busy={createCategory.isPending} onClose={()=>setCategoryOpen(false)} onSubmit={e=>{e.preventDefault();createCategory.mutate()}}>
			<Stack gap="sm">
				<TextInput label="Name" required value={newCategoryName} onChange={e=>setNewCategoryName(e.currentTarget.value)}/>
				<TextInput label="Code" required value={newCategoryCode} onChange={e=>setNewCategoryCode(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={itemOpen} title="New fee item" description="Fee items are the billable lines a fee structure can include." confirmLabel="Create item" busy={createItem.isPending} onClose={()=>setItemOpen(false)} onSubmit={e=>{e.preventDefault();createItem.mutate()}}>
			<Stack gap="sm">
				<Select label="Category" required placeholder={allCategories.data?.results.length?"Select category":"Create a category first"} disabled={!allCategories.data?.results.length} data={allCategories.data?.results.map(c=>({value:c.id,label:c.name}))??[]} value={itemCategory||null} onChange={value=>setItemCategory(value??"")}/>
				<TextInput label="Name" required value={itemName} onChange={e=>setItemName(e.currentTarget.value)}/>
				<TextInput label="Code" required value={itemCode} onChange={e=>setItemCode(e.currentTarget.value)}/>
				<Checkbox label="Optional fee item" checked={itemOptional} onChange={e=>setItemOptional(e.currentTarget.checked)}/>
			</Stack>
		</ActionDialog>
	</>;
}

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
				{mode==="allocate"?<>
					<Select label="Invoice" required placeholder="Select issued invoice" data={invoices.data?.results.filter(i=>i.status==="ISSUED").map(i=>({value:i.id,label:`${i.invoice_number} · ${cash(i.total)}`}))??[]} value={invoice||null} onChange={value=>setInvoice(value??"")}/>
					<NumberInput label="Amount (KES)" required min={0.01} max={selected?Number(selected.unallocated_amount):undefined} decimalScale={2} value={amount} onChange={value=>setAmount(value===""||value===undefined?"":Number(value))}/>
				</>:
					<Textarea label="Reason" required maxLength={240} value={reason} onChange={e=>setReason(e.currentTarget.value)}/>}
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!reversingAllocation} title="Reverse allocation" description={reversingAllocation?`Reverses money allocated to invoice ${invoiceNumber(reversingAllocation.invoice)} on ${when(reversingAllocation.allocated_at)}. This affects only this one allocation -- the payment itself and its other allocations are not touched.`:undefined} confirmLabel="Reverse allocation" danger busy={reverseAllocation.isPending} onClose={()=>setReversingAllocation(null)} onSubmit={e=>{e.preventDefault();reverseAllocation.mutate()}}>
			<Stack gap="sm">
				{reversingAllocation&&<Text size="sm" c="dimmed">Originally allocated: {cash(reversingAllocation.amount)}</Text>}
				<NumberInput label="Amount to reverse (KES)" required min={0.01} decimalScale={2} value={allocationReversalAmount} onChange={value=>setAllocationReversalAmount(value===""||value===undefined?"":Number(value))}/>
				<Textarea label="Reason" required maxLength={240} value={allocationReversalReason} onChange={e=>setAllocationReversalReason(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>
	</>;
}

// Closes STUDENT-GAP-02 for the one flow it's most dangerous on (matching an
// incoming payment posts a real, unreversable-from-here student payment): a
// live server-side search by admission number or name, resolved to the real
// id under the hood, instead of either a raw id field or a page_size:100
// dropdown that silently implies a complete roster.
function StudentSearchSelect({value,onChange,label="Student",required}:{value:string;onChange:(id:string)=>void;label?:string;required?:boolean}){
	const [query,setQuery]=useState("");
	const [debounced]=useDebouncedValue(query,250);
	// The option that produced the current `value`, kept independent of the
	// live search results -- without this, clearing/changing the search text
	// after picking a student drops that id out of `data`, and Mantine falls
	// back to rendering the raw 36-character id in the input since it can no
	// longer resolve a label for the selected value.
	const [selectedOption,setSelectedOption]=useState<{value:string;label:string}|null>(null);
	const search=useQuery({
		queryKey:["student-search",debounced],
		queryFn:()=>api<Page<Student>>("/students/",{params:{search:debounced,page_size:10}}),
		enabled:debounced.trim().length>=2,
	});
	const options=(search.data?.results??[]).map(s=>({value:s.id,label:`${s.full_name} · ${s.admission_number}`}));
	const data=selectedOption&&!options.some(o=>o.value===selectedOption.value)?[selectedOption,...options]:options;
	return <Select label={label} required={required} searchable clearable
		placeholder="Search admission no. or name"
		searchValue={query} onSearchChange={setQuery}
		data={data} filter={({options})=>options}
		nothingFoundMessage={debounced.trim().length<2?"Type at least 2 characters":search.isFetching?"Searching…":"No matching student"}
		value={value||null}
		onChange={(v,option)=>{onChange(v??"");setSelectedOption(v?option:null)}}/>;
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
	// STUDENT-GAP-02 (no search/name-resolution endpoint existed) is closed
	// for this flow: StudentSearchSelect resolves a live server-side search
	// to the real id, so callers no longer need to know or paste a UUID to
	// match a payment.
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
				{receivedAfter&&<Button variant="subtle" size="xs" onClick={()=>{setReceivedAfter("");setPage(1)}}>Clear</Button>}
			</FilterBar>}
		/>

		<ActionDialog open={!!matchTarget} title="Match incoming payment" description={matchTarget?`Creates a real student payment for ${cash(matchTarget.amount)}. Check the student id carefully -- this cannot be undone from here.`:undefined} confirmLabel="Confirm match" busy={match.isPending} onClose={()=>setMatchTarget(null)} onSubmit={e=>{e.preventDefault();match.mutate()}}>
			<Stack gap="sm">
				<StudentSearchSelect required value={matchStudentId} onChange={setMatchStudentId}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!ignoreTarget} title="Ignore incoming payment" description="The entry remains in the audit trail and will not become a payment." confirmLabel="Ignore entry" danger busy={ignore.isPending} onClose={()=>setIgnoreTarget(null)} onSubmit={e=>{e.preventDefault();ignore.mutate()}}>
			<Stack gap="sm">
				<Textarea label="Reason" required maxLength={240} value={ignoreReason} onChange={e=>setIgnoreReason(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>
	</>;
}
