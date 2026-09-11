import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Badge, Box, Button, Checkbox, Group, Modal, MultiSelect, Select, SimpleGrid, Stack, Switch, Table, Text, TextInput } from "@mantine/core";
import { useDebouncedValue } from "@mantine/hooks";
import { api, Page } from "../api/client";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { FilterBar } from "../components/FilterBar";
import { WorkspaceHeader } from "../components/WorkspaceHeader";
import { notify } from "../components/notifications/notify";
import { Empty, ErrorState, Loading, PageHeader, StatusBadge } from "../components/ui";

type ModuleDef={code:string;label:string;apps:string[]};
type Plan={id:string;name:string;module_codes:string[];is_default:boolean;is_active:boolean;created_at:string};
type TenantRow={id:string;name:string;slug:string;is_active:boolean;created_at:string;plan_name:string|null;enabled_module_count:number};
type TenantDetail={id:string;name:string;slug:string;is_active:boolean;created_at:string;plan:Plan|null;enabled_modules:string[]};
type Override={module_code:string;is_enabled:boolean};
type TenantAuditRow={id:number;actor:string|null;action:string;resource_type:string;resource_id:string;metadata:Record<string,unknown>;created_at:string};
type TenantIntegrationChannel={channel:string;label:string;enabled:boolean;provider_configured:boolean;provider:string;sender_id:string;provider_active:boolean};
type TenantIntegrations={notifications_enabled:boolean;channels:TenantIntegrationChannel[];mpesa:{configured:boolean;environment:string;shortcode:string;is_active:boolean}};
const when=(v:string)=>new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v));

function usePaged<T>(key:string,path:string,params?:Record<string,string|number|undefined>){
	const [page,setPage]=useState(1);
	const query=useQuery({queryKey:[key,page,params],queryFn:()=>api<Page<T>>(path,{params:{page,...params},tenant:false})});
	return {page,setPage,query};
}

export function TenantsPage(){
	const qc=useQueryClient();
	const [search,setSearch]=useState("");
	const [debounced]=useDebouncedValue(search,300);
	const data=usePaged<TenantRow>("platform-tenants","/platform/tenants/",{search:debounced||undefined});
	const [selectedId,setSelectedId]=useState<string|null>(null);
	const [createOpen,setCreateOpen]=useState(false);
	const [form,setForm]=useState({name:"",slug:"",admin_email:""});
	const create=useMutation({
		mutationFn:()=>api<{tenant_id:string;slug:string;name:string}>("/platform/tenants/",{method:"POST",tenant:false,body:JSON.stringify(form)}),
		onSuccess:result=>{void qc.invalidateQueries({queryKey:["platform-tenants"]});setCreateOpen(false);setForm({name:"",slug:"",admin_email:""});notify.success(`${result.name} provisioned`)},
		onError:error=>notify.error("School could not be provisioned",error),
	});

	const columns:Column<TenantRow>[]=[
		{key:"name",header:"School",cell:r=><><Text size="sm" fw={600}>{r.name}</Text><Text size="xs" c="dimmed" ff="monospace">{r.slug}</Text></>},
		{key:"plan",header:"Plan",cell:r=>r.plan_name??<Text size="sm" c="dimmed">No plan</Text>},
		{key:"modules",header:"Modules",cell:r=>`${r.enabled_module_count} enabled`},
		{key:"status",header:"Status",cell:r=><StatusBadge value={r.is_active?"Active":"Suspended"}/>},
		{key:"created",header:"Provisioned",cell:r=>when(r.created_at)},
		{key:"action",header:"",cell:r=><Button size="xs" variant="default" onClick={e=>{e.stopPropagation();setSelectedId(r.id)}}>Manage</Button>},
	];

	return <>
		<WorkspaceHeader title="Tenants" description="Every school on the platform -- provision new schools and manage status, plan and modules." action={<Button onClick={()=>setCreateOpen(true)}>+ New school</Button>}/>
		<DataTable title="Tenants" columns={columns} rows={data.query.data?.results??[]} rowKey={r=>r.id} loading={data.query.isLoading} error={data.query.error} retry={()=>void data.query.refetch()} onRefresh={()=>void data.query.refetch()}
			count={data.query.data?.count} page={data.page} previous={!!data.query.data?.previous} next={!!data.query.data?.next} onPage={data.setPage} onRow={r=>setSelectedId(r.id)}
			toolbar={<FilterBar><TextInput placeholder="Search name or slug" size="xs" w={260} value={search} onChange={e=>{setSearch(e.currentTarget.value);data.setPage(1)}}/></FilterBar>}
		/>

		<ActionDialog open={createOpen} title="Provision a new school" description="Creates the tenant and sends the first administrator an invite to accept." confirmLabel="Provision school" busy={create.isPending} onClose={()=>setCreateOpen(false)} onSubmit={e=>{e.preventDefault();create.mutate()}}>
			<Stack gap="sm">
				<TextInput label="School name" required value={form.name} onChange={e=>setForm({...form,name:e.currentTarget.value})}/>
				<TextInput label="Tenant slug" required description="Lowercase letters, numbers and hyphens only." value={form.slug} onChange={e=>setForm({...form,slug:e.currentTarget.value.toLowerCase().replace(/[^a-z0-9-]/g,"-")})}/>
				<TextInput label="First administrator email" type="email" required value={form.admin_email} onChange={e=>setForm({...form,admin_email:e.currentTarget.value})}/>
			</Stack>
		</ActionDialog>

		{selectedId&&<TenantDetailModal tenantId={selectedId} onClose={()=>setSelectedId(null)}/>}
	</>;
}

function TenantDetailModal({tenantId,onClose}:{tenantId:string;onClose:()=>void}){
	const qc=useQueryClient();
	const detail=useQuery({queryKey:["platform-tenant-detail",tenantId],queryFn:()=>api<TenantDetail>(`/platform/tenants/${tenantId}/`,{tenant:false})});
	const plans=useQuery({queryKey:["platform-plans-lookup"],queryFn:()=>api<Plan[]>("/platform/plans/",{tenant:false})});
	const modules=useQuery({queryKey:["platform-modules"],queryFn:()=>api<ModuleDef[]>("/platform/modules/",{tenant:false})});
	const overrides=useQuery({queryKey:["platform-tenant-overrides",tenantId],queryFn:()=>api<Override[]>(`/platform/tenants/${tenantId}/overrides/`,{tenant:false})});
	const audit=useQuery({queryKey:["platform-tenant-audit",tenantId],queryFn:()=>api<Page<TenantAuditRow>>(`/platform/tenants/${tenantId}/audit-events/`,{params:{page_size:10},tenant:false})});
	const integrations=useQuery({queryKey:["platform-tenant-integrations",tenantId],queryFn:()=>api<TenantIntegrations>(`/platform/tenants/${tenantId}/integrations/`,{tenant:false})});

	const invalidateAll=()=>{void qc.invalidateQueries({queryKey:["platform-tenant-detail",tenantId]});void qc.invalidateQueries({queryKey:["platform-tenants"]});void qc.invalidateQueries({queryKey:["platform-tenant-overrides",tenantId]});void qc.invalidateQueries({queryKey:["platform-tenant-audit",tenantId]});void qc.invalidateQueries({queryKey:["platform-tenant-integrations",tenantId]})};

	const toggleNotifications=useMutation({
		mutationFn:(enabled:boolean)=>api<TenantIntegrations>(`/platform/tenants/${tenantId}/integrations/`,{method:"PATCH",tenant:false,body:JSON.stringify({notifications_enabled:enabled})}),
		onSuccess:()=>{invalidateAll();notify.success("Notifications setting updated")},
		onError:error=>notify.error("Could not update notifications setting",error),
	});
	const toggleChannel=useMutation({
		mutationFn:(args:{channel:string;enabled:boolean})=>api(`/platform/tenants/${tenantId}/integrations/channels/${args.channel}/`,{method:"PUT",tenant:false,body:JSON.stringify({enabled:args.enabled})}),
		onSuccess:()=>{invalidateAll();notify.success("Channel updated")},
		onError:error=>notify.error("Channel could not be updated",error),
	});
	const toggleProvider=useMutation({
		mutationFn:(args:{channel:string;provider_active:boolean})=>api(`/platform/tenants/${tenantId}/integrations/channels/${args.channel}/`,{method:"PUT",tenant:false,body:JSON.stringify({provider_active:args.provider_active})}),
		onSuccess:()=>{invalidateAll();notify.success("Provider updated")},
		onError:error=>notify.error("Provider could not be updated",error),
	});
	const toggleMpesa=useMutation({
		mutationFn:(is_active:boolean)=>api(`/platform/tenants/${tenantId}/integrations/mpesa/`,{method:"PUT",tenant:false,body:JSON.stringify({is_active})}),
		onSuccess:()=>{invalidateAll();notify.success("M-Pesa setting updated")},
		onError:error=>notify.error("M-Pesa setting could not be updated",error),
	});

	const toggleActive=useMutation({
		mutationFn:()=>api<TenantDetail>(`/platform/tenants/${tenantId}/`,{method:"PATCH",tenant:false,body:JSON.stringify({is_active:!detail.data!.is_active})}),
		onSuccess:updated=>{invalidateAll();notify.success(updated.is_active?"School activated":"School suspended -- every user there is now locked out")},
		onError:error=>notify.error("School status could not be changed",error),
	});

	const [planId,setPlanId]=useState("");
	const changePlan=useMutation({
		mutationFn:()=>api(`/platform/tenants/${tenantId}/subscription/`,{method:"PUT",tenant:false,body:JSON.stringify({plan_id:planId})}),
		onSuccess:()=>{invalidateAll();setPlanId("");notify.success("Plan changed")},
		onError:error=>notify.error("Plan could not be changed",error),
	});

	const [overrideModule,setOverrideModule]=useState("");
	const [overrideEnabled,setOverrideEnabled]=useState(true);
	const setOverride=useMutation({
		mutationFn:()=>api(`/platform/tenants/${tenantId}/overrides/${overrideModule}/`,{method:"PUT",tenant:false,body:JSON.stringify({is_enabled:overrideEnabled})}),
		onSuccess:()=>{invalidateAll();setOverrideModule("");notify.success("Module override set")},
		onError:error=>notify.error("Override could not be set",error),
	});
	const clearOverride=useMutation({
		mutationFn:(moduleCode:string)=>api(`/platform/tenants/${tenantId}/overrides/${moduleCode}/`,{method:"DELETE",tenant:false}),
		onSuccess:()=>{invalidateAll();notify.success("Override cleared")},
		onError:error=>notify.error("Override could not be cleared",error),
	});

	const moduleLabel=(code:string)=>modules.data?.find(m=>m.code===code)?.label??code;

	return <Modal opened onClose={onClose} title={detail.data?.name??"School"} size="lg">
		{detail.isLoading?<Loading label="Loading school"/>:detail.isError?<ErrorState error={detail.error}/>:!detail.data?<Empty/>:
			<Stack gap="lg">
				<Group justify="space-between" align="center">
					<Box>
						<Text size="xs" c="dimmed" ff="monospace">{detail.data.slug}</Text>
						<Text size="xs" c="dimmed">Provisioned {when(detail.data.created_at)}</Text>
					</Box>
					<Group gap="sm">
						<StatusBadge value={detail.data.is_active?"Active":"Suspended"}/>
						<Button size="xs" color={detail.data.is_active?"red":"teal"} variant="light" loading={toggleActive.isPending} onClick={()=>toggleActive.mutate()}>
							{detail.data.is_active?"Suspend school":"Activate school"}
						</Button>
					</Group>
				</Group>

				<Box>
					<Text fz={12} fw={600} tt="uppercase" c="dimmed" mb={6}>Subscription plan</Text>
					<Group align="end" gap="sm">
						<Text size="sm" fw={600}>{detail.data.plan?.name??"No plan assigned"}</Text>
					</Group>
					<Group align="end" gap="sm" mt="xs">
						<Select placeholder="Change plan" data={plans.data?.filter(p=>p.is_active).map(p=>({value:p.id,label:p.name}))??[]} value={planId||null} onChange={value=>setPlanId(value??"")} w={240}/>
						<Button size="xs" variant="default" disabled={!planId||changePlan.isPending} onClick={()=>changePlan.mutate()}>Assign plan</Button>
					</Group>
				</Box>

				<Box>
					<Text fz={12} fw={600} tt="uppercase" c="dimmed" mb={6}>Enabled modules ({detail.data.enabled_modules.length})</Text>
					<Group gap={6}>{detail.data.enabled_modules.map(code=><Badge key={code} variant="light">{moduleLabel(code)}</Badge>)}</Group>
				</Box>

				<Box>
					<Text fz={12} fw={600} tt="uppercase" c="dimmed" mb={6}>Module overrides</Text>
					<Text size="xs" c="dimmed" mb={6}>An exception to the plan for one module -- force-add one the plan lacks, or force-remove one it includes.</Text>
					{!overrides.data?.length?<Text size="sm" c="dimmed">No overrides.</Text>:
						<Stack gap={4} mb="sm">
							{overrides.data.map(o=>
								<Group key={o.module_code} justify="space-between" wrap="nowrap">
									<Text size="sm">{moduleLabel(o.module_code)} -- {o.is_enabled?"force enabled":"force disabled"}</Text>
									<Button size="xs" variant="subtle" color="red" onClick={()=>clearOverride.mutate(o.module_code)}>Clear</Button>
								</Group>
							)}
						</Stack>}
					<Group align="end" gap="sm">
						<Select label="Module" placeholder="Select module" data={modules.data?.map(m=>({value:m.code,label:m.label}))??[]} value={overrideModule||null} onChange={value=>setOverrideModule(value??"")} w={200}/>
						<Select label="Force" data={[{value:"true",label:"Enabled"},{value:"false",label:"Disabled"}]} value={String(overrideEnabled)} onChange={value=>setOverrideEnabled(value==="true")} w={130}/>
						<Button size="xs" variant="default" disabled={!overrideModule||setOverride.isPending} onClick={()=>setOverride.mutate()}>Set override</Button>
					</Group>
				</Box>

				<Box>
					<Text fz={12} fw={600} tt="uppercase" c="dimmed" mb={6}>Integrations</Text>
					<Text size="xs" c="dimmed" mb={6}>Status and on/off switches only -- credentials (SMTP password, SMS/M-Pesa keys) stay self-service in the school's own settings.</Text>
					{integrations.isLoading?<Loading/>:!integrations.data?<Text size="sm" c="dimmed">Unavailable.</Text>:
						<Stack gap="xs">
							<Group justify="space-between" wrap="nowrap">
								<Text size="sm">Notifications (master switch)</Text>
								<Switch checked={integrations.data.notifications_enabled} onChange={e=>toggleNotifications.mutate(e.currentTarget.checked)} disabled={toggleNotifications.isPending}/>
							</Group>
							{integrations.data.channels.map(ch=>
								<Group key={ch.channel} justify="space-between" wrap="nowrap">
									<Box>
										<Text size="sm">{ch.label}</Text>
										<Text size="xs" c="dimmed">{ch.provider_configured?`${ch.provider||"Provider"}${ch.sender_id?` · ${ch.sender_id}`:""}`:"No provider configured yet"}</Text>
									</Box>
									<Group gap="md">
										{ch.provider_configured&&<Group gap={4}><Text size="xs" c="dimmed">Provider active</Text><Switch size="sm" checked={ch.provider_active} onChange={e=>toggleProvider.mutate({channel:ch.channel,provider_active:e.currentTarget.checked})} disabled={toggleProvider.isPending}/></Group>}
										<Group gap={4}><Text size="xs" c="dimmed">Channel on</Text><Switch size="sm" checked={ch.enabled} onChange={e=>toggleChannel.mutate({channel:ch.channel,enabled:e.currentTarget.checked})} disabled={toggleChannel.isPending}/></Group>
									</Group>
								</Group>
							)}
							<Group justify="space-between" wrap="nowrap">
								<Box>
									<Text size="sm">M-Pesa</Text>
									<Text size="xs" c="dimmed">{integrations.data.mpesa.configured?`${integrations.data.mpesa.environment} · ${integrations.data.mpesa.shortcode}`:"Not configured"}</Text>
								</Box>
								{integrations.data.mpesa.configured
									?<Switch checked={integrations.data.mpesa.is_active} onChange={e=>toggleMpesa.mutate(e.currentTarget.checked)} disabled={toggleMpesa.isPending}/>
									:<Text size="xs" c="dimmed">—</Text>}
							</Group>
						</Stack>}
				</Box>

				<Box>
					<Text fz={12} fw={600} tt="uppercase" c="dimmed" mb={6}>Recent activity</Text>
					{audit.isLoading?<Loading/>:!audit.data?.results.length?<Text size="sm" c="dimmed">No recorded activity.</Text>:
						<Table withTableBorder verticalSpacing="xs">
							<Table.Thead><Table.Tr><Table.Th>Action</Table.Th><Table.Th>Actor</Table.Th><Table.Th>When</Table.Th></Table.Tr></Table.Thead>
							<Table.Tbody>{audit.data.results.map(row=><Table.Tr key={row.id}><Table.Td>{row.action}</Table.Td><Table.Td>{row.actor??"System"}</Table.Td><Table.Td>{when(row.created_at)}</Table.Td></Table.Tr>)}</Table.Tbody>
						</Table>}
				</Box>
			</Stack>}
	</Modal>;
}

export function PlansPage(){
	const qc=useQueryClient();
	const plans=useQuery({queryKey:["platform-plans"],queryFn:()=>api<Plan[]>("/platform/plans/",{tenant:false})});
	const modules=useQuery({queryKey:["platform-modules"],queryFn:()=>api<ModuleDef[]>("/platform/modules/",{tenant:false})});
	const moduleLabel=(code:string)=>modules.data?.find(m=>m.code===code)?.label??code;

	const [editing,setEditing]=useState<Plan|null>(null);
	const [dialogOpen,setDialogOpen]=useState(false);
	const [name,setName]=useState("");
	const [moduleCodes,setModuleCodes]=useState<string[]>([]);
	const [isDefault,setIsDefault]=useState(false);
	const [isActive,setIsActive]=useState(true);
	const openCreate=()=>{setEditing(null);setName("");setModuleCodes([]);setIsDefault(false);setIsActive(true);setDialogOpen(true)};
	const openEdit=(plan:Plan)=>{setEditing(plan);setName(plan.name);setModuleCodes(plan.module_codes);setIsDefault(plan.is_default);setIsActive(plan.is_active);setDialogOpen(true)};

	const save=useMutation({
		mutationFn:()=>editing
			?api<Plan>(`/platform/plans/${editing.id}/`,{method:"PATCH",tenant:false,body:JSON.stringify({name,module_codes:moduleCodes,is_default:isDefault,is_active:isActive})})
			:api<Plan>("/platform/plans/",{method:"POST",tenant:false,body:JSON.stringify({name,module_codes:moduleCodes,is_default:isDefault,is_active:isActive})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["platform-plans"]});setDialogOpen(false);notify.success(editing?"Plan updated":"Plan created")},
		onError:error=>notify.error(editing?"Plan could not be updated":"Plan could not be created",error),
	});
	const remove=useMutation({
		mutationFn:(id:string)=>api(`/platform/plans/${id}/`,{method:"DELETE",tenant:false}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["platform-plans"]});notify.success("Plan deleted")},
		onError:error=>notify.error("Plan could not be deleted",error),
	});

	return <>
		<WorkspaceHeader title="Subscription plans" description="Module bundles assignable to a school. Deactivating a plan blocks new assignment -- it never strips modules from a tenant already on it." action={<Button onClick={openCreate}>+ New plan</Button>}/>
		<Box className="card data-card" p="md">
			{plans.isLoading?<Loading/>:plans.isError?<ErrorState error={plans.error} retry={()=>void plans.refetch()}/>:!plans.data?.length?<Empty/>:
				<Table withTableBorder verticalSpacing="sm">
					<Table.Thead><Table.Tr><Table.Th>Plan</Table.Th><Table.Th>Modules</Table.Th><Table.Th>Default</Table.Th><Table.Th>Status</Table.Th><Table.Th/></Table.Tr></Table.Thead>
					<Table.Tbody>{plans.data.map(plan=>
						<Table.Tr key={plan.id}>
							<Table.Td><Text fw={600} size="sm">{plan.name}</Text></Table.Td>
							<Table.Td><Group gap={4}>{plan.module_codes.map(code=><Badge key={code} size="sm" variant="light">{moduleLabel(code)}</Badge>)}</Group></Table.Td>
							<Table.Td>{plan.is_default?<Badge color="indigo" variant="light">Default</Badge>:null}</Table.Td>
							<Table.Td><StatusBadge value={plan.is_active?"Active":"Inactive"}/></Table.Td>
							<Table.Td><Group gap="xs" justify="flex-end">
								<Button size="xs" variant="default" onClick={()=>openEdit(plan)}>Edit</Button>
								{!plan.is_default&&<Button size="xs" variant="subtle" color="red" disabled={remove.isPending} onClick={()=>remove.mutate(plan.id)}>Delete</Button>}
							</Group></Table.Td>
						</Table.Tr>
					)}</Table.Tbody>
				</Table>}
		</Box>

		<ActionDialog open={dialogOpen} title={editing?"Edit plan":"New plan"} confirmLabel={editing?"Save":"Create plan"} busy={save.isPending} onClose={()=>setDialogOpen(false)} onSubmit={e=>{e.preventDefault();save.mutate()}}>
			<Stack gap="sm">
				<TextInput label="Name" required value={name} onChange={e=>setName(e.currentTarget.value)}/>
				<MultiSelect label="Modules" data={modules.data?.map(m=>({value:m.code,label:m.label}))??[]} value={moduleCodes} onChange={setModuleCodes} searchable/>
				<Checkbox label="Active (available for new assignment)" checked={isActive} onChange={e=>{const checked=e.currentTarget.checked;setIsActive(checked);if(!checked)setIsDefault(false)}}/>
				<Checkbox label="Default plan for new schools" checked={isDefault} disabled={!isActive} onChange={e=>setIsDefault(e.currentTarget.checked)}/>
			</Stack>
		</ActionDialog>
	</>;
}

export function PlatformOverview(){
	const tenants=useQuery({queryKey:["platform-tenants-summary"],queryFn:()=>api<Page<TenantRow>>("/platform/tenants/",{params:{page_size:1},tenant:false})});
	const plans=useQuery({queryKey:["platform-plans"],queryFn:()=>api<Plan[]>("/platform/plans/",{tenant:false})});
	const modules=useQuery({queryKey:["platform-modules"],queryFn:()=>api<ModuleDef[]>("/platform/modules/",{tenant:false})});
	return <>
		<PageHeader eyebrow="Platform administration" title="Overview" description="Cross-tenant status at a glance. Use Tenants to provision schools and manage status/plan/modules."/>
		<SimpleGrid cols={{base:1,sm:3}} spacing="md">
			<Box className="card panel-card" p="md"><Text size="xs" c="dimmed" tt="uppercase">Schools</Text><Text size="xl" fw={700}>{tenants.data?.count??"—"}</Text></Box>
			<Box className="card panel-card" p="md"><Text size="xs" c="dimmed" tt="uppercase">Subscription plans</Text><Text size="xl" fw={700}>{plans.data?.length??"—"}</Text></Box>
			<Box className="card panel-card" p="md"><Text size="xs" c="dimmed" tt="uppercase">Modules in catalogue</Text><Text size="xl" fw={700}>{modules.data?.length??"—"}</Text></Box>
		</SimpleGrid>
	</>;
}
