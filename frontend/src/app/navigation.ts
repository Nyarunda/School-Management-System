import type { AccessRequirement } from "./auth";
export type NavItem={label:string;path?:string;glyph:string;module?:string;permissions?:string[];children?:NavItem[]};
export const navigation:NavItem[]=[
 {label:"Dashboard",path:"/",glyph:"grid"},
 {label:"Students",glyph:"users",module:"student_records",permissions:["students.view"],children:[{label:"Student directory",path:"/students",glyph:"users",permissions:["students.view"]},{label:"Admissions",path:"/admissions",glyph:"inbox",module:"admissions",permissions:["admissions.enroll"]}]},
 {label:"Academics",glyph:"book",children:[{label:"Timetable",path:"/timetable",glyph:"calendar",module:"academics",permissions:["timetable.record.view","timetable.manage"]},{label:"Attendance",path:"/attendance",glyph:"check",module:"attendance",permissions:["attendance.record.view","attendance.session.manage"]},{label:"Assessments",path:"/assessments",glyph:"chart",module:"assessments",permissions:["assessment.record.view","assessment.manage"]}]},
 {label:"Staff",glyph:"briefcase",module:"staff_hr",children:[{label:"Employees",path:"/staff",glyph:"users",permissions:["staff.view","staff.manage"]},{label:"Leave requests",path:"/leave",glyph:"calendar",permissions:["leave.request.view","leave.request.manage","leave.approve"]},{label:"Leave workflow setup",path:"/leave/workflows",glyph:"layers",permissions:["leave.setup.view"]}]},
 {label:"Finance",glyph:"wallet",module:"finance",children:[{label:"Finance overview",path:"/finance",glyph:"chart",permissions:["finance.invoice.view","finance.payment.view"]},{label:"Fee structures",path:"/finance/fees",glyph:"layers",permissions:["finance.fee_structure.view"]},{label:"Assignments",path:"/finance/assignments",glyph:"users",permissions:["finance.fee_structure.view"]},{label:"Invoices",path:"/finance/invoices",glyph:"file",permissions:["finance.invoice.view"]},{label:"Payments",path:"/finance/payments",glyph:"wallet",permissions:["finance.payment.view"]},{label:"Incoming payments",path:"/finance/incoming",glyph:"inbox",permissions:["finance.reconciliation.view"]},{label:"M-Pesa operations",path:"/finance/mpesa",glyph:"phone",permissions:["finance.mpesa.callback.view","finance.mpesa.stk_push.view"]}]},
 {label:"Communications",glyph:"bell",module:"communications",children:[{label:"My inbox",path:"/communications/inbox",glyph:"inbox"},{label:"Outbox",path:"/communications/outbox",glyph:"send",permissions:["notifications.record.view"]},{label:"Templates",path:"/communications/templates",glyph:"file",permissions:["notifications.templates.view"]},{label:"Notification rules",path:"/communications/rules",glyph:"layers",permissions:["notifications.rules.view"]},{label:"Providers & channels",path:"/communications/providers",glyph:"settings",permissions:["notifications.setup.view"]}]},
 {label:"Reports",path:"/reports",glyph:"report",permissions:["reports.students.view","reports.attendance.view","reports.assessments.view","reports.finance.view","reports.staff.view"]},
 {label:"Administration",glyph:"settings",children:[{label:"Users",path:"/administration/users",glyph:"users",permissions:["tenancy.membership.view"]},{label:"Roles & permissions",path:"/administration/roles",glyph:"shield",permissions:["tenancy.role.view"]},{label:"Module setup",path:"/administration/setup",glyph:"settings",permissions:["finance.setup.view","leave.setup.view","documents.setup.view","notifications.setup.view"]}]},
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
