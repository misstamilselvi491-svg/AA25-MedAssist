from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import Optional
import sqlite3, uuid, re, datetime, json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_NAME = BASE_DIR / "medassist_audit.db"
MAX_SCAN_BYTES = 8 * 1024 * 1024

app = FastAPI(title="AA-25 MedAssist", description="Multi-agent evidence decision-support prototype", version="1.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

def db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_database():
    with db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS audit_logs (
            id TEXT PRIMARY KEY, patient_id TEXT NOT NULL, created_at TEXT NOT NULL, result TEXT NOT NULL
        )""")
        conn.commit()

init_database()

def normalize(text):
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()

def contains_any(text, words):
    t = normalize(text)
    return any(w.lower() in t for w in words)

def extract_numbers(text):
    values = {}
    pattern = r"([A-Za-z][A-Za-z0-9 _-]{1,25})\s*[:=]\s*(-?\d+(?:\.\d+)?)"
    for name, value in re.findall(pattern, text or ""):
        try: values[normalize(name)] = float(value)
        except ValueError: pass
    return values

def history_agent(symptoms, history, medications, allergies, age, sex):
    combined = " ".join([symptoms, history, medications, allergies])
    findings, differential, concerns, evidence = [], [], [], []
    if symptoms.strip(): findings.append("Reported symptoms: " + symptoms.strip())
    if history.strip(): findings.append("Relevant history supplied.")
    if medications.strip(): findings.append("Medication information supplied.")
    if allergies.strip(): findings.append("Allergy information supplied.")
    if contains_any(combined, ["fever", "cough", "sputum", "cold"]):
        differential.append("Respiratory infectious/inflammatory process")
        evidence.append({"source":"Patient history","finding":"Respiratory symptom pattern","strength":"moderate"})
    if contains_any(combined, ["chest pain", "shortness of breath", "breathlessness"]):
        differential.append("Cardiorespiratory process requiring clinical assessment")
        concerns.append("Chest/breathing symptoms require prompt clinical review.")
        evidence.append({"source":"Patient history","finding":"Potential cardiorespiratory red flag","strength":"strong"})
    if contains_any(combined, ["abdominal pain", "vomiting", "diarrhea"]):
        differential.append("Gastrointestinal process")
        evidence.append({"source":"Patient history","finding":"Gastrointestinal symptom pattern","strength":"moderate"})
    if contains_any(combined, ["headache", "dizziness"]):
        differential.append("Neurological/systemic symptom process")
        evidence.append({"source":"Patient history","finding":"Neurological/systemic symptoms","strength":"moderate"})
    if not differential: differential.append("Insufficient history evidence")
    return {"agent":"Patient History Agent","role":"Analyzes supplied history and context.","status":"Completed","confidence":0.65 if evidence else 0.25,"findings":findings,"differential":list(dict.fromkeys(differential)),"concerns":concerns,"evidence":evidence,"trace":["Reviewed patient history.","Extracted reported symptoms.","Checked medication and allergy context.","Mapped explicit symptom patterns.","Flagged potential red-red-flag symptoms."]}

def pathology_agent(labs):
    values = extract_numbers(labs)
    findings, differential, concerns, evidence = [], [], [], []
    for name, value in values.items():
        if "wbc" in name or "white blood" in name:
            if value > 11:
                findings.append(f"{name}: {value} — elevated in demo range."); differential.append("Inflammatory/infectious laboratory pattern"); evidence.append({"source":"Laboratory report","finding":f"{name} = {value}","strength":"strong"})
        elif "hemoglobin" in name and value < 12:
            findings.append(f"{name}: {value} — low in demo range."); differential.append("Possible anemia pattern"); evidence.append({"source":"Laboratory report","finding":f"{name} = {value}","strength":"moderate"})
        elif "creatinine" in name and value > 1.3:
            findings.append(f"{name}: {value} — elevated in demo range."); differential.append("Possible renal-function abnormality"); evidence.append({"source":"Laboratory report","finding":f"{name} = {value}","strength":"moderate"})
        elif "crp" in name and value > 10:
            findings.append(f"{name}: {value} — elevated in demo range."); differential.append("Inflammatory laboratory pattern"); evidence.append({"source":"Laboratory report","finding":f"{name} = {value}","strength":"moderate"})
    if not values and labs.strip(): findings.append("Laboratory text was supplied, but no supported key:value values were extracted.")
    if not differential: differential.append("No specific laboratory-supported pattern")
    return {"agent":"Pathology Agent","role":"Analyzes laboratory values and identifies patterns.","status":"Completed","confidence":0.70 if evidence else 0.20,"findings":findings,"differential":list(dict.fromkeys(differential)),"concerns":concerns,"evidence":evidence,"trace":["Parsed explicit laboratory values.","Compared supported values with demo reference ranges.","Extracted laboratory evidence.","Generated pattern-level observations."]}

def radiology_agent(imaging_report, scan_present):
    text = normalize(imaging_report); findings=[]; differential=[]; concerns=[]; evidence=[]
    if contains_any(text,["consolidation","infiltrate","airspace opacity"]):
        findings.append("Airspace/inflammatory imaging language detected."); differential.append("Respiratory infectious/inflammatory process"); evidence.append({"source":"Imaging report","finding":"Airspace/consolidation language","strength":"strong"})
    if contains_any(text,["pleural effusion","effusion"]):
        findings.append("Pleural fluid language detected."); differential.append("Pleural effusion requiring clinical interpretation"); evidence.append({"source":"Imaging report","finding":"Effusion language","strength":"moderate"})
    if contains_any(text,["pneumothorax"]):
        findings.append("Pneumothorax is explicitly mentioned."); differential.append("Pneumothorax"); concerns.append("Potential urgent imaging finding requires clinician review.")
    if contains_any(text,["mass","nodule"]):
        findings.append("Focal lesion terminology detected."); differential.append("Focal lesion requiring further clinical interpretation")
    if contains_any(text,["normal","no acute abnormality"]):
        findings.append("Report contains a normal/no-acute statement."); differential.append("No acute imaging abnormality reported")
    if not imaging_report.strip() and not scan_present: findings.append("No imaging report or scan was supplied.")
    if scan_present: findings.append("An imaging file was uploaded for review.")
    if not differential: differential.append("No specific imaging-supported pattern")
    return {"agent":"Radiology Agent","role":"Analyzes supplied imaging reports and scan metadata.","status":"Completed","confidence":0.70 if evidence else 0.20,"findings":findings,"differential":list(dict.fromkeys(differential)),"concerns":concerns,"evidence":evidence,"trace":["Reviewed supplied imaging information.","Matched explicit radiology terminology.","Did not invent findings.","Flagged possible conflicts for reconciliation."]}

def reconcile(agents):
    scores={}; supporters={}
    for agent in agents:
        weight=max(0.10,min(1.0,float(agent["confidence"])))
        for candidate in agent["differential"]:
            scores[candidate]=scores.get(candidate,0)+weight; supporters.setdefault(candidate,[]).append(agent["agent"])
    ordered=sorted(scores.items(),key=lambda x:x[1],reverse=True)
    candidates=[{"candidate":c,"support":round(s,2),"agents":supporters[c]} for c,s in ordered[:6]]
    conflicts=[]
    for i in range(len(agents)):
        for j in range(i+1,len(agents)):
            a=set(agents[i]["differential"]); b=set(agents[j]["differential"])
            if a and b and not a.intersection(b): conflicts.append({"agent_a":agents[i]["agent"],"agent_b":agents[j]["agent"],"message":"The two specialist agents returned different candidate sets."})
    agreement=round(max(0.0,1-len(conflicts)/3),2)
    return {"candidates":candidates,"agreement":agreement,"conflicts":conflicts,"decision":"HUMAN REVIEW REQUIRED","reason":"The system presents evidence and cross-agent support but does not autonomously diagnose."}

def run_pipeline(patient_id, age, sex, symptoms, history, medications, allergies, labs, imaging_report, scan_present):
    agents=[history_agent(symptoms,history,medications,allergies,age,sex),pathology_agent(labs),radiology_agent(imaging_report,scan_present)]
    result={"audit_id":str(uuid.uuid4()),"patient_id":patient_id,"timestamp":datetime.datetime.now(datetime.timezone.utc).isoformat(),"agents":agents,"consensus":reconcile(agents),"safety":"Prototype decision-support system only. All results require qualified clinician review."}
    with db() as conn:
        conn.execute("INSERT INTO audit_logs VALUES (?,?,?,?)",(result["audit_id"],patient_id,result["timestamp"],json.dumps(result)))
        conn.commit()
    return result

@app.get("/api/health")
def health(): return {"status":"online","system":"AA-25 MedAssist","agents":3,"database":"connected"}

@app.get("/api/demo")
def demo_case():
    return {"patient_id":"DEMO-001","age":52,"sex":"Female","symptoms":"Fever, cough and shortness of breath for 3 days.","history":"No known chronic lung disease.","medications":"Paracetamol as needed.","allergies":"None reported.","labs":"WBC: 14.2; Hemoglobin: 12.8; CRP: 42","imaging_report":"Chest X-ray: right lower-lobe airspace consolidation; no pneumothorax."}

@app.get("/api/audit")
def audit(limit:int=50):
    limit=max(1,min(limit,200))
    with db() as conn:
        rows=conn.execute("SELECT id,patient_id,created_at,result FROM audit_logs ORDER BY created_at DESC LIMIT ?",(limit,)).fetchall()
    return [{"audit_id":r["id"],"patient_id":r["patient_id"],"created_at":r["created_at"],"result":json.loads(r["result"])} for r in rows]

@app.post("/api/analyze")
async def analyze(patient_id:str=Form("DEMO-001"),age:str=Form(""),sex:str=Form(""),symptoms:str=Form(""),history:str=Form(""),medications:str=Form(""),allergies:str=Form(""),labs:str=Form(""),imaging_report:str=Form(""),scan:Optional[UploadFile]=File(None)):
    if not any([symptoms.strip(),history.strip(),labs.strip(),imaging_report.strip(),scan is not None]): raise HTTPException(400,"Please provide at least one patient input.")
    age_value=None
    if age.strip():
        try: age_value=int(age); assert 0<=age_value<=120
        except (ValueError,AssertionError): raise HTTPException(400,"Age must be between 0 and 120.")
    scan_present=False
    if scan:
        scan_present=True
        if not scan.content_type or not scan.content_type.startswith("image/"): raise HTTPException(400,"Scan must be an image.")
        data=await scan.read()
        if len(data)>MAX_SCAN_BYTES: raise HTTPException(413,"Maximum scan size is 8 MB.")
    return run_pipeline(patient_id[:100] or "DEMO-001",age_value,sex[:30],symptoms[:10000],history[:10000],medications[:10000],allergies[:10000],labs[:10000],imaging_report[:10000],scan_present)

@app.get("/", include_in_schema=False)
def frontend(): return FileResponse(BASE_DIR/"static"/"index.html")
