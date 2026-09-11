import { useEffect, useState } from "react";
import { useLocation } from "react-router-dom";
import { Alert, Badge, Box, Button, Group, Loader, Modal as MantineModal, Skeleton, Stack, Text, Title } from "@mantine/core";
import { IconAlertCircle, IconInbox } from "@tabler/icons-react";
import { ApiError } from "../api/client";

// Only glyphs still referenced from page bodies remain here — Shell.tsx and navigation.ts
// moved to Tabler icon components directly during the Phase 2 shell/theme migration.
const glyphs:Record<string,React.ReactNode>={
 grid:<><rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/></>,
 calendar:<><rect x="3" y="5" width="18" height="16" rx="2"/><path d="M16 3v4M8 3v4M3 11h18"/></>,
 check:<path d="M20 6 9 17l-5-5"/>, chart:<><path d="M3 3v18h18"/><path d="m7 16 4-5 4 3 5-8"/></>,
 layers:<><path d="m12 2 9 5-9 5-9-5 9-5z"/><path d="m3 12 9 5 9-5M3 17l9 5 9-5"/></>,
 inbox:<><path d="M4 4h16l2 12H2L4 4z"/><path d="M2 16h5l2 3h6l2-3h5"/></>,
 bell:<><path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9M10 21h4"/></>,
 search:<><circle cx="11" cy="11" r="7"/><path d="m20 20-4-4"/></>,
};
export function Icon({name,size=18}:{name:string;size?:number}){return <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">{glyphs[name]??glyphs.grid}</svg>}
export function PageHeader({eyebrow,title,description,action}:{eyebrow?:string;title:string;description?:string;action?:React.ReactNode}){return <header className="page-header"><div>{eyebrow&&<p className="eyebrow">{eyebrow}</p>}<h1>{title}</h1>{description&&<p className="page-description">{description}</p>}</div>{action&&<div className="page-actions">{action}</div>}</header>}
export function StatusBadge({value}:{value:string|boolean|null|undefined}){const text=typeof value==="boolean"?(value?"Active":"Inactive"):(value||"Unknown");const key=text.toLowerCase();const color=/active|paid|approved|issued|sent|complete|success|present|published/.test(key)?"teal":/pending|draft|open|processing|unallocated|degraded/.test(key)?"yellow":/failed|rejected|reversed|inactive|cancel|absent|error/.test(key)?"red":"gray";return <Badge color={color} variant="light" size="sm">{text.replace(/_/g," ")}</Badge>}

export function Loading({ label }: { label?: string }) {
  if (label) {
    return (
      <Stack align="center" justify="center" py="xl" gap="xs">
        <Loader size="sm" type="dots" color="indigo" />
        <Text size="xs" fw={500} c="dimmed">
          {label}
        </Text>
      </Stack>
    );
  }

  return (
    <Stack gap="xs" p="md">
      <Group justify="space-between" mb={4}>
        <Skeleton height={18} width={140} radius="xs" />
        <Skeleton height={18} width={70} radius="xs" />
      </Group>
      <Skeleton height={34} radius="xs" />
      <Skeleton height={34} radius="xs" />
      <Skeleton height={34} radius="xs" />
      <Skeleton height={34} radius="xs" />
    </Stack>
  );
}

export function Empty({title="No records found",message="Records will appear here when they are available."}:{title?:string;message?:string}){return <Stack className="state" align="center" gap={4}><IconInbox size={28}/><Title order={3} size="sm">{title}</Title><Text size="sm" c="dimmed">{message}</Text></Stack>}
export function ErrorState({error,retry}:{error:unknown;retry?:()=>void}){const message=error instanceof ApiError?error.message:error instanceof Error?error.message:"The request could not be completed.";return <Alert className="state-error" color="red" icon={<IconAlertCircle size={18}/>} title="Unable to load this workspace">{message}{retry&&<Button display="block" mt="sm" variant="light" color="red" onClick={retry}>Try again</Button>}</Alert>}
export function Modal({open,title,children,onClose}:{open:boolean;title:string;children:React.ReactNode;onClose():void}){return <MantineModal opened={open} onClose={onClose} title={title} centered>{children}</MantineModal>}
export function usePath(){return useLocation().pathname}
export function go(path:string){history.pushState({},"",path);window.dispatchEvent(new PopStateEvent("popstate"));window.scrollTo({top:0})}
