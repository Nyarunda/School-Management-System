import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ActionIcon, Box, Button, Divider, Grid, Group, Menu, NumberInput, Paper, Select, Stack, Text, TextInput, UnstyledButton } from "@mantine/core";
import { IconDots } from "@tabler/icons-react";
import { api, Page } from "../api/client";
import { useAccess } from "../app/auth";
import { ActionDialog } from "../components/ActionDialog";
import { KeyValueGrid, KeyValueItem, KeyValueSection } from "../components/KeyValueGrid";
import { WorkspaceHeader } from "../components/WorkspaceHeader";
import { notify } from "../components/notifications/notify";
import { ErrorState, Loading } from "../components/ui";

type Workflow={id:string;name:string};
type Stage={id:string;sequence:number;name:string;approver_role:number};
type Role={id:number;name:string};
type LeaveType={id:string;approval_workflow:string|null};
type StageDialogState={mode:"add"}|{mode:"edit";stage:Stage};

export function LeaveWorkflowPage(){
	const {can}=useAccess();
	const qc=useQueryClient();
	const manage=can("leave.setup.manage");
	const workflows=useQuery({queryKey:["leave-workflows"],queryFn:()=>api<Page<Workflow>>("/leave/workflows/")});
	const [selected,setSelected]=useState<Workflow|null>(null);
	const stages=useQuery({queryKey:["leave-workflow-stages",selected?.id],queryFn:()=>api<Page<Stage>>(`/leave/workflows/${selected!.id}/stages/`),enabled:!!selected});
	const roles=useQuery({queryKey:["roles-for-leave"],queryFn:()=>api<Page<Role>>("/tenancy/roles/",{params:{page_size:100}}),enabled:manage});
	// Only fetched once a workflow has ever been selected, then cached across
	// selections -- /leave/types/ has no approval_workflow filter, so "used by
	// N leave types" is computed client-side against this one shared list
	// rather than re-fetched per workflow.
	const leaveTypes=useQuery({queryKey:["leave-types-for-workflow-usage"],queryFn:()=>api<Page<LeaveType>>("/leave/types/",{params:{page_size:100}}),enabled:!!selected});
	const roleName=(id:number)=>roles.data?.results.find(r=>r.id===id)?.name??`Role ${id}`;

	const [workflowDialog,setWorkflowDialog]=useState(false);
	const [workflowName,setWorkflowName]=useState("");
	const [stageDialog,setStageDialog]=useState<StageDialogState|null>(null);
	const [stageName,setStageName]=useState("");
	const [stageSequence,setStageSequence]=useState(1);
	const [stageRole,setStageRole]=useState("");
	const [deleteTarget,setDeleteTarget]=useState<Stage|null>(null);

	const createWorkflow=useMutation({
		mutationFn:()=>api<Workflow>("/leave/workflows/",{method:"POST",body:JSON.stringify({name:workflowName})}),
		onSuccess:w=>{void qc.invalidateQueries({queryKey:["leave-workflows"]});setSelected(w);setWorkflowDialog(false);notify.success("Leave workflow created")},
		onError:error=>notify.error("Leave workflow could not be created",error),
	});
	const createStage=useMutation({
		mutationFn:()=>api<Stage>(`/leave/workflows/${selected!.id}/stages/`,{method:"POST",body:JSON.stringify({name:stageName,sequence:stageSequence,approver_role:Number(stageRole)})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["leave-workflow-stages",selected?.id]});setStageDialog(null);notify.success("Approval stage added")},
		onError:error=>notify.error("Approval stage could not be added",error),
	});
	const updateStage=useMutation({
		mutationFn:(stage:Stage)=>api<Stage>(`/leave/workflows/${selected!.id}/stages/${stage.id}/`,{method:"PATCH",body:JSON.stringify({name:stageName,approver_role:Number(stageRole)})}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["leave-workflow-stages",selected?.id]});setStageDialog(null);notify.success("Approval stage updated")},
		onError:error=>notify.error("Approval stage could not be updated",error),
	});
	const deleteStage=useMutation({
		mutationFn:(id:string)=>api(`/leave/workflows/${selected!.id}/stages/${id}/`,{method:"DELETE"}),
		onSuccess:()=>{void qc.invalidateQueries({queryKey:["leave-workflow-stages",selected?.id]});setDeleteTarget(null);notify.success("Approval stage removed")},
		onError:error=>notify.error("Approval stage could not be removed",error),
	});

	const stageRows=stages.data?.results??[];
	const usageCount=leaveTypes.data?.results.filter(t=>t.approval_workflow===selected?.id).length??0;

	const openAddStage=()=>{setStageName("");setStageSequence(stageRows.length?Math.max(...stageRows.map(s=>s.sequence))+1:1);setStageRole("");setStageDialog({mode:"add"})};
	const openEditStage=(stage:Stage)=>{setStageName(stage.name);setStageRole(String(stage.approver_role));setStageDialog({mode:"edit",stage})};

	return <Stack gap="lg">
		<WorkspaceHeader title="Approval workflows" description="Configure how leave requests move through approval."
			action={manage?<Button onClick={()=>{setWorkflowName("");setWorkflowDialog(true)}}>+ New workflow</Button>:undefined}/>

		{workflows.isLoading?<Loading label="Loading workflows"/>:workflows.isError?<ErrorState error={workflows.error} retry={()=>void workflows.refetch()}/>:
			!workflows.data?.results.length?
				<Paper withBorder p="xl">
					<Stack align="center" gap={4}>
						<Text fw={600}>No approval workflows</Text>
						<Text size="sm" c="dimmed" ta="center" maw={360}>Create an approval workflow to define how new leave requests are reviewed and approved.</Text>
						{manage&&<Button mt="sm" onClick={()=>{setWorkflowName("");setWorkflowDialog(true)}}>+ New workflow</Button>}
					</Stack>
				</Paper>
			:
			<Grid gap="lg">
				<Grid.Col span={{base:12,md:3}}>
					<Paper withBorder p="sm">
						<Text fz={11} fw={600} tt="uppercase" c="dimmed" mb={2}>Workflows</Text>
						<Text size="xs" c="dimmed" mb="sm">{workflows.data.results.length} workflow{workflows.data.results.length===1?"":"s"}</Text>
						<Stack gap={2}>
							{workflows.data.results.map(w=>{
								const active=selected?.id===w.id;
								return <UnstyledButton key={w.id} onClick={()=>setSelected(w)} px="sm" py={6}
									style={{borderRadius:6,borderLeft:`3px solid ${active?"var(--mantine-color-indigo-6)":"transparent"}`,background:active?"var(--mantine-color-indigo-0)":"transparent"}}>
									<Text size="sm" fw={active?600:500}>{w.name}</Text>
								</UnstyledButton>;
							})}
						</Stack>
					</Paper>
				</Grid.Col>
				<Grid.Col span={{base:12,md:9}}>
					{!selected?
						<Stack gap={4}>
							<Text fz={11} fw={600} tt="uppercase" c="dimmed">Workflow configuration</Text>
							<Text size="sm" c="dimmed">Select a workflow to configure its approval stages.</Text>
						</Stack>
					:
						<Stack gap="lg">
							<Stack gap={2}>
								<Text size="lg" fw={600}>{selected.name}</Text>
								<Text size="xs" c="dimmed">Leave approval workflow</Text>
							</Stack>

							<KeyValueSection title="General">
								<KeyValueGrid>
									<KeyValueItem label="Name" value={selected.name}/>
									<KeyValueItem label="Approval stages" value={stageRows.length}/>
									<KeyValueItem label="Used by" value={usageCount?`${usageCount} leave type${usageCount===1?"":"s"}`:"No leave types yet"}/>
								</KeyValueGrid>
							</KeyValueSection>

							<Stack gap="sm">
								<Box>
									<Text fz={11} fw={600} tt="uppercase" c="dimmed">Approval chain</Text>
									<Text size="xs" c="dimmed">Stages execute in the following order.</Text>
								</Box>

								{stages.isLoading?<Loading label="Loading stages"/>:stages.isError?<ErrorState error={stages.error} retry={()=>void stages.refetch()}/>:
									!stageRows.length?
										<Text size="sm" c="dimmed">This workflow has no approval stages yet.</Text>
									:
									<Paper withBorder p="md">
										<Stack gap={0}>
											{stageRows.map((stage,index)=><Box key={stage.id}>
												{index>0&&<Stack align="center" gap={0} py={2}><Text c="dimmed" size="sm">↓</Text></Stack>}
												<Group justify="space-between" wrap="nowrap" py={8}>
													<Group gap="md" wrap="nowrap">
														<Text fw={700} c="dimmed" size="lg" w={22}>{index+1}</Text>
														<Stack gap={0}>
															<Text size="sm" fw={600}>{stage.name}</Text>
															<Text size="xs" c="dimmed">Approver: {roleName(stage.approver_role)}</Text>
														</Stack>
													</Group>
													{manage&&<Menu position="bottom-end" withinPortal>
														<Menu.Target><ActionIcon variant="subtle" color="gray" aria-label="Stage actions"><IconDots size={16}/></ActionIcon></Menu.Target>
														<Menu.Dropdown>
															<Menu.Item onClick={()=>openEditStage(stage)}>Edit stage</Menu.Item>
															<Menu.Item color="red" onClick={()=>setDeleteTarget(stage)}>Delete stage</Menu.Item>
														</Menu.Dropdown>
													</Menu>}
												</Group>
												{index<stageRows.length-1&&<Divider/>}
											</Box>)}
										</Stack>
									</Paper>
								}

								{manage&&<Button variant="default" onClick={openAddStage} style={{alignSelf:"flex-start"}}>+ Add approval stage</Button>}
							</Stack>
						</Stack>
					}
				</Grid.Col>
			</Grid>
		}

		<ActionDialog open={workflowDialog} title="Create leave workflow" confirmLabel="Create workflow" busy={createWorkflow.isPending} onClose={()=>setWorkflowDialog(false)} onSubmit={e=>{e.preventDefault();if(!workflowName)return;createWorkflow.mutate()}}>
			<Stack gap="sm">
				<TextInput label="Workflow name" required maxLength={120} value={workflowName} onChange={e=>setWorkflowName(e.currentTarget.value)}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!stageDialog} title={stageDialog?.mode==="edit"?"Edit approval stage":"Add approval stage"} description={stageDialog?.mode==="add"?"The sequence must be unique within this workflow.":undefined}
			confirmLabel={stageDialog?.mode==="edit"?"Save changes":"Add stage"} busy={createStage.isPending||updateStage.isPending} onClose={()=>setStageDialog(null)}
			onSubmit={e=>{e.preventDefault();if(!stageName||!stageRole)return;if(stageDialog?.mode==="edit")updateStage.mutate(stageDialog.stage);else createStage.mutate()}}>
			<Stack gap="sm">
				{stageDialog?.mode==="add"&&<NumberInput label="Sequence" required min={1} value={stageSequence} onChange={value=>setStageSequence(Number(value)||1)}/>}
				<TextInput label="Stage name" required maxLength={80} value={stageName} onChange={e=>setStageName(e.currentTarget.value)}/>
				<Select label="Approver role" required placeholder={roles.isLoading?"Loading…":"Select role"} disabled={roles.isLoading} data={(roles.data?.results??[]).map(r=>({value:String(r.id),label:r.name}))} value={stageRole} onChange={value=>setStageRole(value??"")}/>
			</Stack>
		</ActionDialog>

		<ActionDialog open={!!deleteTarget} title="Delete approval stage" danger description={`Delete "${deleteTarget?.name}"? Requests already using this workflow keep their own snapshotted stages -- only future submissions are affected. This cannot be undone.`}
			confirmLabel="Delete stage" busy={deleteStage.isPending} onClose={()=>setDeleteTarget(null)} onSubmit={e=>{e.preventDefault();deleteStage.mutate(deleteTarget!.id)}}/>
	</Stack>;
}
