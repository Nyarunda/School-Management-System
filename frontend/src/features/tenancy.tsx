import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Checkbox, Grid, Group, Paper, Select, SimpleGrid, Stack, Text, TextInput, Tooltip, UnstyledButton } from "@mantine/core";
import { api, ApiError, Page } from "../api/client";
import { useAccess, useAuth } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { Column, DataTable } from "../components/DataTable";
import { WorkspaceHeader } from "../components/WorkspaceHeader";
import { notify } from "../components/notifications/notify";
import { ErrorState, Loading, StatusBadge } from "../components/ui";

type Role={id:number;name:string;permissions:string[]};
type PermissionEntry={code:string;label:string;domain:string};
const errorText=(error:unknown)=>error instanceof ApiError?error.message:error instanceof Error?error.message:"The action failed";

export function RolesPage(){
	const {can}=useAccess();
	const {session}=useAuth();
	const qc=useQueryClient();
	const manage=can("tenancy.role.manage");

	// tenancy.role.manage does NOT imply tenancy.role.view (verified against
	// the actual permission contract, not assumed) -- but the route itself
	// already requires .view to be reachable at all (navigation.ts), so a
	// manage-only actor without .view never lands on this page in the first
	// place. No extra defensive fetch-gating is needed inside the page for
	// that combination; it only matters for pickers embedded in OTHER pages
	// (see the Users slice, which needs its own explicit .view checks).
	const roles=useQuery({queryKey:["tenancy-roles"],queryFn:()=>api<Page<Role>>("/tenancy/roles/",{params:{page_size:100}})});
	const catalogue=useQuery({queryKey:["tenancy-permission-catalogue"],queryFn:()=>api<PermissionEntry[]>("/tenancy/permissions/")});

	// The actor's own effective permission set -- exactly what
	// _require_grantable_permissions checks server-side (a non-superuser can
	// only grant/assign permissions they themselves currently hold). A
	// platform admin (is_superuser) bypasses that check entirely, so is
	// treated as able to grant anything here too, mirroring the backend
	// bypass exactly rather than approximating it.
	const isPlatformAdmin=session?.user.is_platform_admin??false;
	const actorPermissions=new Set(session?.permissions??[]);
	const canGrant=(code:string)=>isPlatformAdmin||actorPermissions.has(code);

	const [selectedId,setSelectedId]=useState<number|null>(null);
	const selectedRole=roles.data?.results.find(r=>r.id===selectedId)??null;

	const [name,setName]=useState("");
	// Two disjoint sets partition the selected role's current permissions:
	// - preserved: currently held but the acting admin cannot grant/revoke.
	//   Never touched by a checkbox; always resubmitted verbatim.
	// - editableSelected: currently held AND grantable, plus anything newly
	//   checked. The only set checkboxes ever mutate.
	// The submitted payload is always [...preserved, ...editableSelected] --
	// never "whatever is currently checked" -- so a role can never lose a
	// permission the current admin was never allowed to touch in the first
	// place, regardless of what their own checkboxes show.
	const [preserved,setPreserved]=useState<Set<string>>(new Set());
	const [editableSelected,setEditableSelected]=useState<Set<string>>(new Set());

	function selectRole(role:Role){
		setSelectedId(role.id);
		setName(role.name);
		const held=role.permissions;
		setPreserved(new Set(held.filter(code=>!canGrant(code))));
		setEditableSelected(new Set(held.filter(code=>canGrant(code))));
	}

	function toggle(code:string){
		setEditableSelected(current=>{
			const next=new Set(current);
			if(next.has(code))next.delete(code);else next.add(code);
			return next;
		});
	}

	const save=useMutation({
		mutationFn:()=>api<Role>(`/tenancy/roles/${selectedId}/`,{method:"PATCH",body:JSON.stringify({name,permissions:[...preserved,...editableSelected]})}),
		onSuccess:updated=>{void qc.invalidateQueries({queryKey:["tenancy-roles"]});selectRole(updated);notify.success("Role updated")},
		onError:error=>notify.error("Role could not be updated",error),
	});

	const [createOpen,setCreateOpen]=useState(false);
	const [createName,setCreateName]=useState("");
	const create=useMutation({
		mutationFn:()=>api<Role>("/tenancy/roles/",{method:"POST",body:JSON.stringify({name:createName,permissions:[]})}),
		onSuccess:created=>{void qc.invalidateQueries({queryKey:["tenancy-roles"]});setCreateOpen(false);selectRole(created)},
		onError:error=>notify.error("Role could not be created",error),
	});

	const [deleteTarget,setDeleteTarget]=useState<Role|null>(null);
	const remove=useMutation({
		mutationFn:()=>api(`/tenancy/roles/${deleteTarget!.id}/`,{method:"DELETE"}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["tenancy-roles"]});setDeleteTarget(null);if(selectedId===deleteTarget?.id)setSelectedId(null);notify.success("Role deleted")},
		onError:error=>notify.error("Role could not be deleted",error),
	});

	const grouped=Object.entries(
		(catalogue.data??[]).reduce<Record<string,PermissionEntry[]>>((groups,entry)=>{
			(groups[entry.domain]??=[]).push(entry);
			return groups;
		},{}),
	).sort(([a],[b])=>a.localeCompare(b));

	return <Stack gap="lg">
		<WorkspaceHeader title="Roles & permissions" description="Define the actions each school role can perform."
			action={manage?<Button onClick={()=>{setCreateName("");setCreateOpen(true)}}>+ New role</Button>:undefined}/>

		{roles.isLoading?<Loading label="Loading roles"/>:roles.isError?<ErrorState error={roles.error} retry={()=>void roles.refetch()}/>:
			!roles.data?.results.length?
				<Paper withBorder p="xl">
					<Stack align="center" gap={4}>
						<Text fw={600}>No roles yet</Text>
						<Text size="sm" c="dimmed" ta="center" maw={360}>Create a role to define what its members can see and do.</Text>
						{manage&&<Button mt="sm" onClick={()=>{setCreateName("");setCreateOpen(true)}}>+ New role</Button>}
					</Stack>
				</Paper>
			:
			<Grid gap="lg">
				<Grid.Col span={{base:12,md:3}}>
					<Paper withBorder p="sm">
						<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={2}>Roles</Text>
						<Text size="xs" c="dimmed" mb="sm">{roles.data.results.length} role{roles.data.results.length===1?"":"s"}</Text>
						<Stack gap={2}>
							{roles.data.results.map(role=>{
								const active=selectedId===role.id;
								return <UnstyledButton key={role.id} onClick={()=>selectRole(role)} px="sm" py={6}
									style={{borderRadius:6,borderLeft:`3px solid ${active?"var(--mantine-color-indigo-6)":"transparent"}`,background:active?"var(--mantine-color-indigo-0)":"transparent"}}>
									<Text size="sm" fw={active?600:500}>{role.name}</Text>
									<Text size="xs" c="dimmed">{role.permissions.length} permission{role.permissions.length===1?"":"s"}</Text>
								</UnstyledButton>;
							})}
						</Stack>
					</Paper>
				</Grid.Col>
				<Grid.Col span={{base:12,md:9}}>
					{!selectedRole?
						<Stack gap={4}>
							<Text fz={11} fw={600} tt="uppercase" c="dimmed">Role configuration</Text>
							<Text size="sm" c="dimmed">Select a role to review or change its permissions.</Text>
						</Stack>
					:
						<Stack gap="lg">
							<TextInput label="Role name" required maxLength={100} value={name} onChange={e=>setName(e.currentTarget.value)} disabled={!manage} w={{base:"100%",sm:320}}/>

							{save.error&&<Alert color="red" variant="light">{errorText(save.error)}</Alert>}

							{catalogue.isLoading?<Loading label="Loading permission catalogue"/>:catalogue.isError?<ErrorState error={catalogue.error} retry={()=>void catalogue.refetch()}/>:
								<Stack gap="md">
									{grouped.map(([domain,entries])=>
										<Paper key={domain} withBorder p="md">
											<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb="xs">{domain}</Text>
											<SimpleGrid cols={{base:1,sm:2}} spacing="xs">
												{entries.map(entry=>{
													const isPreserved=preserved.has(entry.code);
													const grantable=canGrant(entry.code);
													const checked=isPreserved||editableSelected.has(entry.code);
													const disabled=!manage||isPreserved||!grantable;
													const checkbox=<Checkbox key={entry.code} label={entry.label} checked={checked} disabled={disabled}
														onChange={()=>{if(!disabled)toggle(entry.code)}}/>;
													return disabled&&manage?
														<Tooltip key={entry.code} label={isPreserved?"You cannot revoke a permission you don't hold yourself":"You cannot grant a permission you don't hold yourself"} withArrow>
															<div>{checkbox}</div>
														</Tooltip>
													:checkbox;
												})}
											</SimpleGrid>
										</Paper>,
									)}
								</Stack>
							}

							{manage&&<Group justify="space-between">
								<Button color="red" variant="subtle" onClick={()=>setDeleteTarget(selectedRole)}>Delete role</Button>
								<Button onClick={()=>save.mutate()} loading={save.isPending} disabled={!name}>Save changes</Button>
							</Group>}
						</Stack>
					}
				</Grid.Col>
			</Grid>
		}

		<ActionDialog open={createOpen} title="Create role" description="Configure its permissions after creating it." confirmLabel="Create role" busy={create.isPending} onClose={()=>setCreateOpen(false)} onSubmit={e=>{e.preventDefault();if(!createName)return;create.mutate()}}>
			<Stack gap="sm">
				{create.error&&<Alert color="red" variant="light">{errorText(create.error)}</Alert>}
				<TextInput label="Role name" required maxLength={100} value={createName} onChange={e=>setCreateName(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!deleteTarget} title="Delete role" danger description={`Delete "${deleteTarget?.name}" permanently? This cannot be undone.`} confirmLabel="Delete role" busy={remove.isPending} onClose={()=>setDeleteTarget(null)} onSubmit={e=>{e.preventDefault();remove.mutate()}}>
			{remove.error&&<Alert color="red" variant="light">{errorText(remove.error)}</Alert>}
		</ActionDialog>
	</Stack>;
}

type MembershipUser={id:string;username:string;email:string;first_name:string;last_name:string};
type MembershipRole={id:number;name:string};
type MembershipCampus={id:number;name:string}|null;
type UserMembership={id:number;user:MembershipUser;role:MembershipRole;campus:MembershipCampus;is_active:boolean;invite_accepted_at:string|null;joined_at:string};
type CampusOption={id:number;name:string};
const when=(v:string|null)=>v?new Intl.DateTimeFormat("en-KE",{dateStyle:"medium",timeStyle:"short"}).format(new Date(v)):"—";
// invite_accepted_at is the single-use invite-acceptance flag (independent
// of is_active, see accept_invite's docstring) -- "Pending" means the invite
// was sent but never accepted, distinct from "Inactive" (accepted, then
// later deactivated by an admin).
function membershipStatus(m:UserMembership):"Pending"|"Active"|"Inactive"{
	if(!m.invite_accepted_at)return "Pending";
	return m.is_active?"Active":"Inactive";
}

export function UsersPage(){
	const {can}=useAccess();
	const {session}=useAuth();
	const qc=useQueryClient();
	const manage=can("tenancy.membership.manage");
	// tenancy.role.view is a different domain permission from
	// tenancy.membership.view (which the route itself already requires) --
	// not implied by it, proven twice now on RolesPage. Gated independently
	// here too, never assumed from this page's own reachability.
	const canViewRoles=can("tenancy.role.view");

	// Mirrors _require_grantable_permissions server-side: a non-superuser can
	// only invite/reassign someone into a role whose permissions they
	// themselves already hold. Used to grey out ungrantable roles in the
	// pickers below rather than let the request round-trip into a 400.
	const isPlatformAdmin=session?.user.is_platform_admin??false;
	const actorPermissions=new Set(session?.permissions??[]);
	const canGrantRole=(role:Role)=>isPlatformAdmin||role.permissions.every(p=>actorPermissions.has(p));

	const [page,setPage]=useState(1);
	const memberships=useQuery({queryKey:["tenancy-memberships",page],queryFn:()=>api<Page<UserMembership>>("/tenancy/memberships/",{params:{page}})});
	const roles=useQuery({queryKey:["tenancy-roles-for-users"],queryFn:()=>api<Page<Role>>("/tenancy/roles/",{params:{page_size:100}}),enabled:canViewRoles});
	// CampusListView requires tenancy.membership.view, exactly what this
	// page's own route already requires -- always safe to fetch here.
	const campuses=useQuery({queryKey:["tenancy-campuses-for-users"],queryFn:()=>api<Page<CampusOption>>("/tenancy/campuses/",{params:{page_size:100}})});
	const roleOptions=(roles.data?.results??[]).map(r=>({value:String(r.id),label:r.name,disabled:!canGrantRole(r)}));
	const campusOptions=(campuses.data?.results??[]).map(c=>({value:String(c.id),label:c.name}));

	const [inviteOpen,setInviteOpen]=useState(false);
	const [inviteForm,setInviteForm]=useState({email:"",role:"",campus:""});
	const invite=useMutation({
		mutationFn:()=>api<UserMembership>("/tenancy/users/invite/",{method:"POST",body:JSON.stringify({email:inviteForm.email,role:Number(inviteForm.role),campus:inviteForm.campus?Number(inviteForm.campus):null})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["tenancy-memberships"]});setInviteOpen(false);notify.success("Invitation sent")},
		onError:error=>notify.error("Invitation could not be sent",error),
	});

	const [reassignTarget,setReassignTarget]=useState<UserMembership|null>(null);
	const [reassignForm,setReassignForm]=useState({role:"",campus:""});
	const reassign=useMutation({
		mutationFn:()=>api<UserMembership>(`/tenancy/memberships/${reassignTarget!.id}/`,{method:"PATCH",body:JSON.stringify({
			...(canViewRoles&&reassignForm.role?{role:Number(reassignForm.role)}:{}),
			campus:reassignForm.campus?Number(reassignForm.campus):null,
		})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["tenancy-memberships"]});setReassignTarget(null);notify.success("Member updated")},
		onError:error=>notify.error("Member could not be updated",error),
	});

	// Activation is only ever offered for a membership that already accepted
	// its invite (invite_accepted_at set) and was later deactivated --
	// never for a Pending row. activate_membership has no server-side guard
	// against activating an unaccepted invite, so the UI is what keeps an
	// admin from bypassing the accept-invite flow (setting a password /
	// explicit consent) entirely.
	const [statusTarget,setStatusTarget]=useState<{membership:UserMembership;action:"activate"|"deactivate"}|null>(null);
	const setStatus=useMutation({
		mutationFn:()=>api<UserMembership>(`/tenancy/memberships/${statusTarget!.membership.id}/${statusTarget!.action}/`,{method:"POST"}),
		onSuccess:()=>{const action=statusTarget?.action;void qc.invalidateQueries({queryKey:["tenancy-memberships"]});setStatusTarget(null);notify.success(action==="activate"?"Member activated":"Member deactivated")},
		onError:error=>notify.error(statusTarget?.action==="activate"?"Member could not be activated":"Member could not be deactivated",error),
	});

	const columns:Column<UserMembership>[]=[
		{key:"user",header:"Member",cell:r=><><Text size="sm" fw={500}>{(r.user.first_name||r.user.last_name)?`${r.user.first_name} ${r.user.last_name}`.trim():r.user.username}</Text><Text size="xs" c="dimmed">{r.user.email||r.user.username}</Text></>},
		{key:"role",header:"Role",cell:r=>r.role.name},
		{key:"campus",header:"Campus",cell:r=>r.campus?.name??"All campuses"},
		{key:"status",header:"Status",cell:r=><StatusBadge value={membershipStatus(r)}/>},
		{key:"joined",header:"Joined",cell:r=>when(r.joined_at)},
	];

	return <Stack gap="lg">
		<WorkspaceHeader title="Users" description="Invite people to this workspace and manage their role, campus and access."
			action={manage?<Button disabled={!canViewRoles} onClick={()=>{setInviteForm({email:"",role:"",campus:""});setInviteOpen(true)}}>+ Invite user</Button>:undefined}/>

		<DataTable title="Members" columns={columns} rows={memberships.data?.results??[]} rowKey={r=>r.id}
			loading={memberships.isLoading} error={memberships.error} retry={()=>void memberships.refetch()} onRefresh={()=>void memberships.refetch()}
			page={page} count={memberships.data?.count} previous={!!memberships.data?.previous} next={!!memberships.data?.next} onPage={setPage}
			rowActions={!manage?undefined:r=>{
				const status=membershipStatus(r);
				return <Group gap={6} wrap="nowrap">
					<Button size="xs" variant="default" onClick={e=>{e.stopPropagation();setReassignForm({role:String(r.role.id),campus:r.campus?String(r.campus.id):""});setReassignTarget(r)}}>Reassign</Button>
					{status==="Active"&&<Button size="xs" variant="default" color="red" onClick={e=>{e.stopPropagation();setStatusTarget({membership:r,action:"deactivate"})}}>Deactivate</Button>}
					{status==="Inactive"&&<Button size="xs" variant="default" onClick={e=>{e.stopPropagation();setStatusTarget({membership:r,action:"activate"})}}>Activate</Button>}
				</Group>;
			}}/>

		<ActionDialog open={inviteOpen} title="Invite user" description="They'll receive an email with a secure link to accept and set up access." confirmLabel="Send invite" busy={invite.isPending} onClose={()=>setInviteOpen(false)} onSubmit={e=>{e.preventDefault();if(!inviteForm.email||!inviteForm.role)return;invite.mutate()}}>
			<Stack gap="sm">
				{invite.error&&<Alert color="red" variant="light">{errorText(invite.error)}</Alert>}
				<TextInput label="Email" type="email" required value={inviteForm.email} onChange={e=>setInviteForm({...inviteForm,email:e.currentTarget.value})}/>
				<Select label="Role" required placeholder={roles.isLoading?"Loading…":"Select role"} disabled={roles.isLoading} data={roleOptions} value={inviteForm.role||null} onChange={value=>setInviteForm({...inviteForm,role:value??""})}/>
				<Select label="Campus" placeholder={campuses.isLoading?"Loading…":"All campuses"} disabled={campuses.isLoading} data={campusOptions} value={inviteForm.campus||null} onChange={value=>setInviteForm({...inviteForm,campus:value??""})} clearable/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!reassignTarget} title="Reassign member" description={`Change ${reassignTarget?.user.username}'s role or campus.`} confirmLabel="Save changes" busy={reassign.isPending} onClose={()=>setReassignTarget(null)} onSubmit={e=>{e.preventDefault();reassign.mutate()}}>
			<Stack gap="sm">
				{reassign.error&&<Alert color="red" variant="light">{errorText(reassign.error)}</Alert>}
				{canViewRoles?
					<Select label="Role" required placeholder={roles.isLoading?"Loading…":"Select role"} disabled={roles.isLoading} data={roleOptions} value={reassignForm.role||null} onChange={value=>setReassignForm({...reassignForm,role:value??""})}/>
				:<Text size="sm" c="dimmed">You don't have permission to view roles, so this member's role cannot be changed here.</Text>}
				<Select label="Campus" placeholder={campuses.isLoading?"Loading…":"All campuses"} disabled={campuses.isLoading} data={campusOptions} value={reassignForm.campus||null} onChange={value=>setReassignForm({...reassignForm,campus:value??""})} clearable/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!statusTarget} title={statusTarget?.action==="activate"?"Activate member":"Deactivate member"} danger={statusTarget?.action==="deactivate"}
			description={statusTarget?.action==="activate"?"Restore this member's access to the workspace.":"This member will immediately lose access to the workspace."}
			confirmLabel={statusTarget?.action==="activate"?"Activate":"Deactivate"} busy={setStatus.isPending} onClose={()=>setStatusTarget(null)} onSubmit={e=>{e.preventDefault();setStatus.mutate()}}>
			{setStatus.error&&<Alert color="red" variant="light">{errorText(setStatus.error)}</Alert>}
		</ActionDialog>
	</Stack>;
}
