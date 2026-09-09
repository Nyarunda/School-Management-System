import { useMemo, useState } from "react";
import { IconVolume, IconVolumeOff } from "@tabler/icons-react";
import { useAuth, useAccess } from "../app/auth";
import { navigation, NavItem } from "../app/navigation";
import { isSoundEnabled, setSoundEnabled } from "./notifications/notificationSound";
import { go, Icon, usePath } from "./ui";

function permitted(item:NavItem,canAny:(p?:string[])=>boolean,moduleEnabled:(m?:string)=>boolean){return moduleEnabled(item.module)&&canAny(item.permissions)}

export function AppShell({children}:{children:React.ReactNode}){
 const {session,logout,selectTenant,platformAccess}=useAuth();const {canAccess}=useAccess();const path=usePath();const [mobile,setMobile]=useState(false);const [collapsed,setCollapsed]=useState(false);const [density,setDensity]=useState<"comfortable"|"compact">(()=>localStorage.getItem("school-erp-density")==="compact"?"compact":"comfortable");const [soundOn,setSoundOn]=useState(()=>isSoundEnabled());
 const groups=useMemo(()=>navigation.map(item=>item.children?{...item,children:item.children.filter(child=>canAccess({module:child.module,anyPermissions:child.permissions}))}:item).filter(item=>canAccess({module:item.module,anyPermissions:item.permissions})&&(!item.children||item.children.length)),[session]);
 const current=groups.flatMap(item=>item.children??[item]).find(item=>item.path===path);
 function link(item:NavItem){if(!item.path)return null;const active=path===item.path||item.path!=="/"&&path.startsWith(item.path+"/");return <button key={item.path} className={`nav-link ${active?"active":""}`} onClick={()=>{go(item.path!);setMobile(false)}} title={item.label}><Icon name={item.glyph}/><span>{item.label}</span></button>}
 function toggleDensity(){const next=density==="comfortable"?"compact":"comfortable";localStorage.setItem("school-erp-density",next);setDensity(next)}
 function toggleSound(){const next=!soundOn;setSoundEnabled(next);setSoundOn(next)}
 return <div className={`app-frame ${collapsed?"is-compact":""} density-${density} ${mobile?"mobile-open":""}`}>
  <aside className="sidebar">
   <div className="brand"><div className="brand-mark">S</div><div><strong>Scholaris</strong><small>School ERP</small></div></div>
   <nav className="nav" aria-label="Primary navigation">{groups.map(item=>item.children?<section className="nav-group" key={item.label}><p>{item.label}</p>{item.children.map(link)}</section>:link(item))}</nav>
   <div className="sidebar-foot">{platformAccess&&<button className="nav-link platform-link" onClick={()=>go("/platform")}><Icon name="shield"/><span>Platform console</span></button>}<button className="collapse-button" onClick={toggleDensity}><Icon name="layers"/><span>{density==="compact"?"Comfortable density":"Compact density"}</span></button><button className="collapse-button" onClick={toggleSound}>{soundOn?<IconVolume size={18}/>:<IconVolumeOff size={18}/>}<span>{soundOn?"Sound on":"Sound off"}</span></button><button className="collapse-button" onClick={()=>setCollapsed(!collapsed)}><span>{collapsed?"›":"‹"}</span><span>Collapse menu</span></button></div>
  </aside>
  {mobile&&<button className="scrim" aria-label="Close navigation" onClick={()=>setMobile(false)}/>} 
  <div className="app-main">
   <header className="topbar"><button className="icon-button menu-button" onClick={()=>setMobile(true)} aria-label="Open navigation"><Icon name="menu"/></button><div className="crumb"><span>{current?.label??"Dashboard"}</span></div><button className="icon-button" aria-label="Notifications" onClick={()=>go("/communications/inbox")}><Icon name="bell"/></button><select className="tenant-select" aria-label="Active school" value={session?.active_tenant?.slug??""} onChange={e=>void selectTenant(e.target.value)}>{session?.memberships.map(item=><option value={item.tenant.slug} key={item.tenant.id}>{item.tenant.name}</option>)}</select><div className="account"><span className="avatar">{session?.user.name.slice(0,2).toUpperCase()}</span><div><strong>{session?.user.name}</strong><small>{session?.memberships.find(m=>m.tenant.slug===session.active_tenant?.slug)?.role}</small></div><button className="text-button" onClick={()=>void logout()}>Sign out</button></div></header>
   <main className="content">{children}</main>
  </div>
 </div>
}

export function PlatformShell({children}:{children:React.ReactNode}){const {session,logout}=useAuth();const path=usePath();return <div className="platform-frame"><aside className="platform-sidebar"><div className="brand"><div className="brand-mark platform-mark">P</div><div><strong>Scholaris Platform</strong><small>Super administration</small></div></div><nav className="nav"><button className={`nav-link ${path==="/platform"?"active":""}`} onClick={()=>go("/platform")}><Icon name="grid"/><span>Overview</span></button><button className={`nav-link ${path==="/platform/plans"?"active":""}`} onClick={()=>go("/platform/plans")}><Icon name="layers"/><span>Plans</span></button><button className={`nav-link ${path==="/platform/audit"?"active":""}`} onClick={()=>go("/platform/audit")}><Icon name="report"/><span>Audit trail</span></button></nav><button className="back-school" onClick={()=>go("/")}>← Return to school ERP</button></aside><div className="app-main"><header className="topbar platform-topbar"><div><span className="platform-label">Platform scope</span></div><div className="account"><span className="avatar">{session?.user.name.slice(0,2).toUpperCase()}</span><strong>{session?.user.name}</strong><button className="text-button" onClick={()=>void logout()}>Sign out</button></div></header><main className="content">{children}</main></div></div>}
