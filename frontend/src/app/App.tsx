import { lazy, Suspense } from "react";
import { AppShell, PlatformShell } from "../components/Shell";
import { Loading, usePath } from "../components/ui";
import { DashboardPage, EmployeePage, InvitePage, LoginPage, MissingPage, PlatformHome, ResourcePage, resources, SetupPage, StaffPage, StudentPage, StudentsPage } from "../pages/pages";
import { canAccess, useAuth } from "./auth";
import { navRequirementsFor } from "./navigation";
const FinanceOverview=lazy(()=>import("../features/finance").then(m=>({default:m.FinanceOverview})));
const FeeStructuresPage=lazy(()=>import("../features/finance").then(m=>({default:m.FeeStructuresPage})));
const AssignmentsPage=lazy(()=>import("../features/finance").then(m=>({default:m.AssignmentsPage})));
const InvoicesPage=lazy(()=>import("../features/finance").then(m=>({default:m.InvoicesPage})));
const PaymentsPage=lazy(()=>import("../features/finance").then(m=>({default:m.PaymentsPage})));
const IncomingPage=lazy(()=>import("../features/finance").then(m=>({default:m.IncomingPage})));
const MpesaPage=lazy(()=>import("../features/mpesa").then(m=>({default:m.MpesaPage})));
const LeaveWorkflowPage=lazy(()=>import("../features/leave").then(m=>({default:m.LeaveWorkflowPage})));
const AttendancePage=lazy(()=>import("../features/attendance").then(m=>({default:m.AttendancePage})));
const AssessmentsPage=lazy(()=>import("../features/assessments").then(m=>({default:m.AssessmentsPage})));
const ReportsPage=lazy(()=>import("../features/reporting").then(m=>({default:m.ReportsPage})));
const NotificationInboxPage=lazy(()=>import("../features/notifications").then(m=>({default:m.NotificationInboxPage})));
const NotificationTemplatesPage=lazy(()=>import("../features/notifications").then(m=>({default:m.NotificationTemplatesPage})));
const NotificationProvidersPage=lazy(()=>import("../features/notifications").then(m=>({default:m.NotificationProvidersPage})));
const NotificationRulesPage=lazy(()=>import("../features/notifications").then(m=>({default:m.NotificationRulesPage})));
const NotificationOutboxPage=lazy(()=>import("../features/notifications").then(m=>({default:m.NotificationOutboxPage})));
const RolesPage=lazy(()=>import("../features/tenancy").then(m=>({default:m.RolesPage})));
const UsersPage=lazy(()=>import("../features/tenancy").then(m=>({default:m.UsersPage})));

export function App(){
 const path=usePath();const {session,loading,platformAccess}=useAuth();
 if(path==="/accept-invite")return <InvitePage/>;
 if(loading)return <main className="boot-screen"><div className="brand standalone"><div className="brand-mark">S</div><strong>Scholaris</strong></div><Loading label="Opening your workspace"/></main>;
 if(!session)return <LoginPage/>;
 if(path.startsWith("/platform")){
  if(!platformAccess)return <AppShell><MissingPage/></AppShell>;
  const platformPage=path==="/platform"?<PlatformHome/>:resources[path]?<ResourcePage config={resources[path]}/>:<MissingPage/>;
  return <PlatformShell>{platformPage}</PlatformShell>;
 }
 let page:React.ReactNode;
 if(path==="/")page=<DashboardPage/>;
 else if(path==="/students")page=<StudentsPage/>;
 else if(/^\/students\/[0-9a-f-]+$/i.test(path))page=<StudentPage id={path.split("/").pop()!}/>;
 else if(path==="/staff")page=<StaffPage/>;
 else if(/^\/staff\/[0-9a-f-]+$/i.test(path))page=<EmployeePage id={path.split("/").pop()!}/>;
 else if(path==="/administration/setup")page=<SetupPage/>;
 else if(path==="/administration/roles")page=<RolesPage/>;
 else if(path==="/administration/users")page=<UsersPage/>;
 else if(path==="/admissions")page=<MissingPage/>;
 else if(path==="/finance")page=<FinanceOverview/>;
 else if(path==="/finance/fees")page=<FeeStructuresPage/>;
 else if(path==="/finance/assignments")page=<AssignmentsPage/>;
 else if(path==="/finance/invoices")page=<InvoicesPage/>;
 else if(path==="/finance/payments")page=<PaymentsPage/>;
 else if(path==="/finance/incoming")page=<IncomingPage/>;
 else if(path==="/finance/mpesa")page=<MpesaPage/>;
 else if(path==="/leave/workflows")page=<LeaveWorkflowPage/>;
 else if(path==="/attendance")page=<AttendancePage/>;
 else if(path==="/assessments")page=<AssessmentsPage/>;
 else if(path==="/reports")page=<ReportsPage/>;
 else if(path==="/communications/inbox")page=<NotificationInboxPage/>;
 else if(path==="/communications/templates")page=<NotificationTemplatesPage/>;
 else if(path==="/communications/providers")page=<NotificationProvidersPage/>;
 else if(path==="/communications/rules")page=<NotificationRulesPage/>;
 else if(path==="/communications/outbox")page=<NotificationOutboxPage/>;
 else page=resources[path]?<ResourcePage config={resources[path]}/>:<MissingPage/>;
 // Route-level enforcement mirrors navigation.ts exactly (parent + own requirement),
 // so a page is never reachable by URL when its own sidebar entry would be hidden.
 const canonicalPath=/^\/students\/[0-9a-f-]+$/i.test(path)?"/students":/^\/staff\/[0-9a-f-]+$/i.test(path)?"/staff":path;
 const requirements=navRequirementsFor(canonicalPath);
 if(requirements.length&&!requirements.every(requirement=>canAccess(session,requirement)))page=<MissingPage/>;
 return <AppShell><Suspense fallback={<Loading label="Opening workspace"/>}>{page}</Suspense></AppShell>;
}
