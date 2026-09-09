import type { ComponentType } from "react";
import {
 IconLayoutDashboard, IconUsers, IconInbox, IconBook2, IconCalendar, IconChecklist, IconChartBar,
 IconBriefcase, IconWallet, IconStack2, IconFileText, IconDeviceMobile, IconBell, IconSend,
 IconReportAnalytics, IconSettings, IconShieldCheck,
} from "@tabler/icons-react";
import type { AccessRequirement } from "./auth";

export type NavIcon = ComponentType<{ size?: number | string; stroke?: number }>;
// `section` is presentation-only grouping metadata for a leaf inside an expanded module
// (e.g. "Billing"/"Payments" under Finance) — it never participates in routing or
// permission resolution, so navRequirementsFor and route matching stay two levels deep.
export type NavItem={label:string;path?:string;icon:NavIcon;module?:string;permissions?:string[];section?:string;children?:NavItem[]};

export const navigation:NavItem[]=[
 {label:"Dashboard",path:"/",icon:IconLayoutDashboard},
 {label:"Students",icon:IconUsers,module:"student_records",permissions:["students.view"],children:[
  {label:"Student directory",path:"/students",icon:IconUsers,permissions:["students.view"]},
  {label:"Admissions",path:"/admissions",icon:IconInbox,module:"admissions",permissions:["admissions.enroll"]},
 ]},
 {label:"Academics",icon:IconBook2,children:[
  {label:"Timetable",path:"/timetable",icon:IconCalendar,module:"academics",permissions:["timetable.record.view","timetable.manage"]},
  {label:"Attendance",path:"/attendance",icon:IconChecklist,module:"attendance",permissions:["attendance.record.view","attendance.session.manage"]},
  {label:"Assessments",path:"/assessments",icon:IconChartBar,module:"assessments",permissions:["assessment.record.view","assessment.manage"]},
 ]},
 {label:"Staff",icon:IconBriefcase,module:"staff_hr",children:[
  {label:"Employees",path:"/staff",icon:IconUsers,permissions:["staff.view","staff.manage"]},
  {label:"Leave requests",path:"/leave",icon:IconCalendar,permissions:["leave.request.view","leave.request.manage","leave.approve"]},
  {label:"Leave workflow setup",path:"/leave/workflows",icon:IconStack2,permissions:["leave.setup.view"]},
 ]},
 {label:"Finance",icon:IconWallet,module:"finance",children:[
  {label:"Finance overview",path:"/finance",icon:IconChartBar,permissions:["finance.invoice.view","finance.payment.view"]},
  {label:"Fee structures",path:"/finance/fees",icon:IconStack2,permissions:["finance.fee_structure.view"],section:"Billing"},
  {label:"Assignments",path:"/finance/assignments",icon:IconUsers,permissions:["finance.fee_structure.view"],section:"Billing"},
  {label:"Invoices",path:"/finance/invoices",icon:IconFileText,permissions:["finance.invoice.view"],section:"Billing"},
  {label:"Payments",path:"/finance/payments",icon:IconWallet,permissions:["finance.payment.view"],section:"Payments"},
  {label:"Incoming payments",path:"/finance/incoming",icon:IconInbox,permissions:["finance.reconciliation.view"],section:"Payments"},
  {label:"M-Pesa operations",path:"/finance/mpesa",icon:IconDeviceMobile,permissions:["finance.mpesa.callback.view","finance.mpesa.stk_push.view"],section:"M-Pesa"},
 ]},
 {label:"Communications",icon:IconBell,module:"communications",children:[
  {label:"My inbox",path:"/communications/inbox",icon:IconInbox},
  {label:"Outbox",path:"/communications/outbox",icon:IconSend,permissions:["notifications.record.view"]},
  {label:"Templates",path:"/communications/templates",icon:IconFileText,permissions:["notifications.templates.view"]},
  {label:"Notification rules",path:"/communications/rules",icon:IconStack2,permissions:["notifications.rules.view"]},
  {label:"Providers & channels",path:"/communications/providers",icon:IconSettings,permissions:["notifications.setup.view"]},
 ]},
 {label:"Reports",path:"/reports",icon:IconReportAnalytics,permissions:["reports.students.view","reports.attendance.view","reports.assessments.view","reports.finance.view","reports.staff.view"]},
 {label:"Administration",icon:IconSettings,children:[
  {label:"Users",path:"/administration/users",icon:IconUsers,permissions:["tenancy.membership.view"],section:"Users & access"},
  {label:"Roles & permissions",path:"/administration/roles",icon:IconShieldCheck,permissions:["tenancy.role.view"],section:"Users & access"},
  {label:"Module setup",path:"/administration/setup",icon:IconSettings,permissions:["finance.setup.view","leave.setup.view","documents.setup.view","notifications.setup.view"]},
 ]},
];

// Walks navigation to the item matching `path`, returning its own requirement plus
// every ancestor's — the same chain Shell.tsx's sidebar filtering applies, so a route
// can never be reachable by URL when its own nav entry would be hidden.
export function navRequirementsFor(path:string):AccessRequirement[]{
 const found:AccessRequirement[]=[];
 const visit=(items:NavItem[]):boolean=>{
  for(const item of items){
   if(item.path===path){found.push({module:item.module,anyPermissions:item.permissions});return true}
   if(item.children&&visit(item.children)){found.push({module:item.module,anyPermissions:item.permissions});return true}
  }
  return false;
 };
 visit(navigation);
 return found;
}
