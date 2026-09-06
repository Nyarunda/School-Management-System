import { FormEvent, StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Student = {
  admissionNumber: string;
  name: string;
  campus: string;
  status: "ACTIVE";
};

const initialStudents: Student[] = [
  { admissionNumber: "ADM-001", name: "Amina Otieno", campus: "Main Campus", status: "ACTIVE" },
  { admissionNumber: "ADM-002", name: "Daniel Kamau", campus: "North Campus", status: "ACTIVE" },
];

function App() {
  return (
    <main className="shell">
      <StudentsWorkspace />
    </main>
  );
}

function StudentsWorkspace() {
  const [students, setStudents] = useState(initialStudents);
  const [selected, setSelected] = useState<Student | null>(initialStudents[0]);
  const [showAdmission, setShowAdmission] = useState(false);
  const [tab, setTab] = useState("Overview");

  function admitStudent(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const student = {
      admissionNumber: String(form.get("admissionNumber")),
      name: String(form.get("name")),
      campus: String(form.get("campus")),
      status: "ACTIVE" as const,
    };
    setStudents((current) => [...current, student]);
    setSelected(student);
    setShowAdmission(false);
    event.currentTarget.reset();
  }

  return (
    <>
      <header className="workspace-header">
        <div><p className="eyebrow">North Campus / Students</p><h1>Student lifecycle</h1></div>
        <button className="primary-button" onClick={() => setShowAdmission(true)}>+ New admission</button>
      </header>
      <div className="workspace-grid">
        <section className="panel student-list">
          <div className="panel-heading"><h2>Students</h2><span>{students.length} active</span></div>
          {students.map((student) => (
            <button className={`student-row ${selected?.admissionNumber === student.admissionNumber ? "selected" : ""}`} key={student.admissionNumber} onClick={() => setSelected(student)}>
              <span><strong>{student.name}</strong><small>{student.admissionNumber} · {student.campus}</small></span>
              <em>{student.status}</em>
            </button>
          ))}
        </section>
        {selected && <section className="panel student-detail">
          <div className="student-heading"><div><p className="eyebrow">{selected.admissionNumber}</p><h2>{selected.name}</h2><p>{selected.campus} · Active student</p></div><span className="status-pill">{selected.status}</span></div>
          <nav className="tabs" aria-label="Student details">
            {["Overview", "Guardians", "Documents", "Activity"].map((item) => <button className={tab === item ? "active" : ""} key={item} onClick={() => setTab(item)}>{item}</button>)}
          </nav>
          <div className="tab-content">{tab === "Overview" && <Overview />}{tab === "Guardians" && <Info title="Guardians" text="No guardians linked yet." />}{tab === "Documents" && <Info title="Documents" text="Birth certificate and admission documents will appear here." />}{tab === "Activity" && <Info title="Activity" text="Student admitted · Today" />}</div>
        </section>}
      </div>
      {showAdmission && <div className="modal-backdrop"><form className="modal" onSubmit={admitStudent}><div className="panel-heading"><h2>New admission</h2><button type="button" className="close-button" onClick={() => setShowAdmission(false)}>×</button></div><label>Student name<input name="name" required placeholder="Full name" /></label><label>Admission number<input name="admissionNumber" required placeholder="ADM-003" /></label><label>Campus<select name="campus" defaultValue="Main Campus"><option>Main Campus</option><option>North Campus</option></select></label><button className="primary-button" type="submit">Admit student</button></form></div>}
    </>
  );
}

function Overview() { return <div className="overview"><div><small>Status</small><strong>Active</strong></div><div><small>Attendance</small><strong>94%</strong></div><div><small>Average</small><strong>76%</strong></div></div>; }
function Info({ title, text }: { title: string; text: string }) { return <div className="empty-state"><h3>{title}</h3><p>{text}</p></div>; }

createRoot(document.getElementById("root")!).render(
  <StrictMode><App /></StrictMode>,
);