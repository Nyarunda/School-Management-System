import { FormEvent, ReactNode } from "react";
import { Button, Group, Modal, Stack, Text } from "@mantine/core";

export function ActionDialog({open,title,description,children,confirmLabel="Confirm",busy=false,danger=false,onClose,onSubmit}:{open:boolean;title:string;description?:string;children?:ReactNode;confirmLabel?:string;busy?:boolean;danger?:boolean;onClose:()=>void;onSubmit:(event:FormEvent)=>void}){
 if(!open)return null;
 return <Modal opened={open} onClose={onClose} title={title} centered><form onSubmit={onSubmit}><Stack gap="md">{description&&<Text size="sm" c="dimmed">{description}</Text>}<div className="dialog-body">{children}</div><Group justify="flex-end"><Button type="button" variant="default" onClick={onClose}>Cancel</Button><Button type="submit" color={danger?"red":"indigo"} loading={busy}>{confirmLabel}</Button></Group></Stack></form></Modal>
}
