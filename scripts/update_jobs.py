#!/usr/bin/env python3
from __future__ import annotations
import argparse,csv,html,json,logging,re
from concurrent.futures import ThreadPoolExecutor,as_completed
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl,urlencode,urlsplit,urlunsplit
import requests,yaml
from bs4 import BeautifulSoup

ROOT=Path(__file__).resolve().parents[1]; TODAY=date.today().isoformat(); TIMEOUT=25
HEADERS={"User-Agent":"Mozilla/5.0 (compatible; 2027-New-Grad-Jobs/0.4; +https://github.com/coconight01/2027-North-America-New-Grad-Jobs)"}
LOG=logging.getLogger("jobs")
FIELDS=["company","role","category","location","country","graduation","start_date","degree","sponsorship","visa_evidence","citizenship_required","salary","salary_min_annual","salary_max_annual","phd_required","personalized_score","priority","personalized_reason","skills","url","source","date_added","last_verified","status","match"]
TRACKING={"utm_source","utm_medium","utm_campaign","utm_term","utm_content","ref","source","src","gh_src","lever-source","jr_id"}
EXCLUDE=re.compile(r"\b(intern(ship)?|co-?op|apprentice|part[- ]?time|contract(?:or)?)\b",re.I)
NEW_GRAD=re.compile(r"\b(new grad(uate)?|new college grad|university grad(uate)?|college grad|entry[- ]level|early career|graduate (software|quantitative|machine learning|data|hardware|engineer|trader|researcher)|software engineer i\b|engineer i\b)\b",re.I)
YEAR_2027=re.compile(r"\b(2027|class of 2027|dec(?:ember)? 2026|spring 2027|summer 2027|fall 2026|mid-2027|2026[-–/]2027)\b",re.I)
OLD_YEAR=re.compile(r"\b(2024|2025)\b",re.I)
NORTH_AMERICA=re.compile(r"\b(united states|u\.?s\.?|usa|canada|north america|remote(?:,? (?:us|usa|canada|north america))?|alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|ontario|quebec|british columbia|alberta|manitoba|saskatchewan|nova scotia|new brunswick|newfoundland|prince edward island|yukon|northwest territories|chicago|austin|san francisco|seattle|boston|toronto|vancouver|montreal|waterloo|palo alto|mountain view|sunnyvale|san jose|los angeles|miami|atlanta|denver|dallas|houston|bellevue|redmond|pittsburgh|cupertino|fremont|san diego)\b",re.I)
NO_SPONSOR=re.compile(r"\b(?:will not|do not|does not|cannot|can't|unable to) (?:provide|offer)?\s*(?:employment |work |visa )?sponsorship|without (?:current or future )?sponsorship|not eligible for (?:visa )?sponsorship|(?:no|not available for) (?:employment |visa )?sponsorship\b",re.I)
YES_SPONSOR=re.compile(r"\b(?:visa|immigration|employment) sponsorship (?:is )?(?:available|provided|offered)|(?:we|company) (?:will|can) sponsor|support (?:for )?work authorization\b",re.I)
CITIZEN=re.compile(r"\b(?:u\.?s\.?|united states) citizenship (?:is )?(?:required|mandatory)|must be (?:a )?(?:u\.?s\.?|united states) citizen|only (?:u\.?s\.?|united states) citizens|active (?:top secret|secret|ts/?sci|security) clearance|(?:ability|eligible|eligibility) to obtain (?:and maintain )?(?:a |an )?(?:top secret|secret|ts/?sci|security) clearance|(?:position appropriate|current) security clearance (?:is )?required\b",re.I)
CLOSED=re.compile(r"\b(job (?:is )?no longer available|position has been filled|posting has expired|applications? (?:are )?closed|this job is closed)\b",re.I)
SALARY=re.compile(r"(?:\$|USD\s*)\s*(\d{2,3}(?:,\d{3})?(?:\.\d+)?)\s*(?:-|–|—|to)\s*(?:\$|USD\s*)?\s*(\d{2,3}(?:,\d{3})?(?:\.\d+)?)(?:\s*(per year|annually|/year|per hour|hourly|/hr))?",re.I)
ROLE_NOISE=re.compile(r"\b(?:potential\s+telework|telework(?:\s+eligible)?|remote(?:\s+eligible)?|hybrid|on[- ]?site)\b",re.I)
PURE_HARDWARE=re.compile(r"\b(electrical|mechanical|manufacturing|hardware|asic|rtl|fpga|physical design|silicon design|circuit|analog|mixed[- ]signal|pcb|board design|semiconductor|verification engineer|validation engineer|embedded hardware|firmware engineer|test hardware)\b",re.I)
SOFTWARE_RESCUE=re.compile(r"\b(software|machine learning|ml systems?|ai infrastructure|distributed systems?|compiler|runtime|cuda|gpu software|kernel|systems programming|inference|training platform|cloud|database|network)\b",re.I)
PHD_REQUIRED=re.compile(r"(?:\bph\.?d\.?\b|\bdoctoral degree\b).{0,45}(?:required|must|required qualification|minimum qualification)|(?:required|must have|minimum qualification).{0,60}(?:\bph\.?d\.?\b|\bdoctoral degree\b)|\bphd only\b",re.I)
SKILLS=[(n,re.compile(p,re.I)) for n,p in [("Python",r"\bpython\b"),("C++",r"(?<!\w)c\+\+(?!\w)"),("Java",r"\bjava\b"),("Go",r"\b(?:golang|go language)\b"),("Rust",r"\brust\b"),("JavaScript",r"\bjavascript\b"),("TypeScript",r"\btypescript\b"),("React",r"\breact(?:\.js)?\b"),("SQL",r"\bsql\b"),("Linux",r"\blinux\b"),("AWS",r"\baws\b|amazon web services"),("GCP",r"\bgcp\b|google cloud"),("Azure",r"\bazure\b"),("Docker",r"\bdocker\b"),("Kubernetes",r"\bkubernetes\b|\bk8s\b"),("PyTorch",r"\bpytorch\b"),("TensorFlow",r"\btensorflow\b"),("JAX",r"\bjax\b"),("CUDA",r"\bcuda\b"),("Machine Learning",r"\bmachine learning\b"),("Deep Learning",r"\bdeep learning\b"),("Distributed Systems",r"\bdistributed systems?\b"),("Data Structures",r"\bdata structures?\b"),("Algorithms",r"\balgorithms?\b"),("Networking",r"\bnetworking\b|network protocols?"),("Databases",r"\bdatabases?\b"),("Compilers",r"\bcompilers?\b"),("FPGA",r"\bfpga\b"),("Verilog",r"\b(?:system)?verilog\b")]]
CATEGORIES=[("Quantitative Finance",r"\b(quant|trader|trading|investment|equities)\b"),("AI / Machine Learning",r"\b(machine learning|artificial intelligence|ai engineer|data scientist|nlp|llm)\b"),("Data Engineering",r"\b(data engineer|analytics engineer|business intelligence)\b"),("Infrastructure / Systems",r"\b(infrastructure|systems|platform|sre|site reliability|distributed|compiler|database|network)\b"),("Hardware Engineering",r"\b(hardware|firmware|fpga|asic|silicon|embedded|verification|electrical|mechanical|manufacturing)\b"),("Cybersecurity",r"\b(cyber|security engineer|information security|threat|soc analyst)\b"),("Product Management",r"\b(product manager|apm|product management)\b"),("Software Engineering",r"\b(software|developer|full[- ]?stack|backend|frontend|forward deployed)\b")]
DEFAULT_PREFERENCES={"hard_filters":{"exclude_no_sponsorship":True,"min_annual_salary":100000,"exclude_pure_hardware":True},"ranking":{"base_score":40,"target_annual_salary":200000,"sponsorship_yes_bonus":18,"sponsorship_unknown_penalty":8,"salary_unknown_penalty":5,"phd_penalty":42,"phd_strong_fit_penalty":12,"phd_rescue_fit_score":34,"explicit_2027_bonus":5,"category_weights":{"Infrastructure / Systems":24,"AI / Machine Learning":20,"Quantitative Finance":18,"Software Engineering":13,"Data Engineering":8,"Cybersecurity":4,"Product Management":0,"Hardware Engineering":-30,"Other":0},"keyword_weights":{"ml systems":12,"machine learning systems":12,"llm systems":12,"ai infrastructure":12,"ml infrastructure":12,"machine learning infrastructure":12,"distributed training":11,"distributed systems":9,"llm inference":12,"llm training":11,"inference":6,"training platform":8,"vllm":10,"nccl":10,"cuda":8,"gpu":5,"pytorch":5,"jax":5,"compiler":8,"runtime":7,"parallelism":8,"performance":5,"quantitative":5,"quant":4}}}

def clean(v): return BeautifulSoup(html.unescape(v or ""),"html.parser").get_text(" ",strip=True)
def canon(u):
    if not u:return ""
    p=urlsplit(u.strip()); q=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.casefold() not in TRACKING])
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/"),q,""))
def load_preferences():
    result=json.loads(json.dumps(DEFAULT_PREFERENCES)); path=ROOT/"config/preferences.yml"
    loaded=yaml.safe_load(path.read_text(encoding="utf-8")) or {} if path.exists() else {}
    for section,values in loaded.items():
        if isinstance(values,dict) and isinstance(result.get(section),dict): result[section].update(values)
        else: result[section]=values
    return result
def job(company,role,location,url,source,description=""):
    return {"company":company.strip(),"role":role.strip(),"category":"Other","location":location.strip() or "Unknown","country":"Unknown","graduation":"Unknown","start_date":"Unknown","degree":"Unknown","sponsorship":"Unknown","visa_evidence":"","citizenship_required":"Unknown","salary":"Not listed","salary_min_annual":"","salary_max_annual":"","phd_required":"Unknown","personalized_score":0,"priority":"","personalized_reason":"","skills":"","url":canon(url),"source":source,"date_added":TODAY,"last_verified":TODAY,"status":"Open","match":"General new grad","description":description}
def get_json(url):
    r=requests.get(url,headers=HEADERS,timeout=TIMEOUT); r.raise_for_status(); return r.json()
def classify(text):
    for name,p in CATEGORIES:
        if re.search(p,text,re.I):return name
    return "Other"
def citizen_required(j):
    if str(j.get("citizenship_required","")).casefold()=="yes":return True
    return bool(CITIZEN.search(" ".join([j.get("role",""),j.get("description",""),j.get("visa_evidence","")])))
def pure_hardware(j):
    role=j.get("role","")
    if SOFTWARE_RESCUE.search(role):return False
    return bool(PURE_HARDWARE.search(role) or (j.get("category")=="Hardware Engineering" and not SOFTWARE_RESCUE.search(role)))
def phd_is_required(j):
    if str(j.get("phd_required","")).casefold()=="yes":return True
    role=j.get("role","")
    return bool(re.search(r"(?:[-–—|(/]\s*)ph\.?d\.?\b|\bphd\s*$",role,re.I) or PHD_REQUIRED.search(role+" "+j.get("description","")))
def salary_bounds(value):
    text=str(value or "")
    if not text or text.casefold() in {"unknown","not listed","none"} or ("$" not in text and "usd" not in text.casefold()):return None
    values=[]
    for raw,suffix in re.findall(r"(?:\$|USD\s*)?\s*(\d{1,3}(?:,\d{3})*(?:\.\d+)?)(\s*[kK])?",text,re.I):
        n=float(raw.replace(",","")); n*=1000 if suffix.strip().casefold()=="k" else 1; values.append(n)
    if not values:return None
    values=values[:2]
    if re.search(r"/hr|per hour|hourly",text,re.I):values=[x*2080 for x in values]
    return min(values),max(values)
def strong_fit_score(j,prefs):
    text=" ".join([j.get("role",""),j.get("category",""),j.get("skills",""),j.get("description","")]).casefold(); score=0; matched=[]
    for keyword,weight in prefs["ranking"].get("keyword_weights",{}).items():
        if keyword.casefold() in text:score+=int(weight);matched.append(keyword)
    return min(score,45),matched
def personalized_rejection_reason(j,prefs):
    hard=prefs["hard_filters"]
    if citizen_required(j):return "Citizenship or security-clearance requirement"
    if hard.get("exclude_pure_hardware",True) and pure_hardware(j):return "Pure hardware role"
    if hard.get("exclude_no_sponsorship",True) and j.get("sponsorship")=="No":return "No sponsorship"
    bounds=salary_bounds(j.get("salary")); threshold=int(hard.get("min_annual_salary",100000))
    if bounds and bounds[1]<threshold:return f"Maximum stated annual salary below ${threshold:,}"
    return ""
def personalize(j,prefs):
    j["phd_required"]="Yes" if phd_is_required(j) else ("No" if j.get("phd_required") in {"","Unknown",None} else j.get("phd_required"))
    bounds=salary_bounds(j.get("salary")); j["salary_min_annual"]=int(round(bounds[0])) if bounds else ""; j["salary_max_annual"]=int(round(bounds[1])) if bounds else ""
    r=prefs["ranking"];score=int(r.get("base_score",40));reasons=[]
    category_weight=int(r.get("category_weights",{}).get(j.get("category","Other"),0));score+=category_weight
    if category_weight>0:reasons.append(j.get("category","Target category"))
    fit,matched=strong_fit_score(j,prefs);score+=fit
    if matched:reasons.append("fit: "+", ".join(matched[:4]))
    if j.get("sponsorship")=="Yes":score+=int(r.get("sponsorship_yes_bonus",18));reasons.append("sponsorship stated")
    elif j.get("sponsorship") not in {"Yes","No"}:score-=int(r.get("sponsorship_unknown_penalty",8));reasons.append("sponsorship unknown")
    if bounds:
        maximum,minimum=bounds[1],bounds[0];target=int(r.get("target_annual_salary",200000))
        if minimum>=target or maximum>=target:score+=35;reasons.append("$200k+ range")
        elif maximum>=150000:score+=22;reasons.append("$150k+ range")
        elif maximum>=100000:score+=9;reasons.append("$100k+ range")
    else:score-=int(r.get("salary_unknown_penalty",5));reasons.append("salary unknown")
    if j["phd_required"]=="Yes":
        if fit>=int(r.get("phd_rescue_fit_score",34)):score-=int(r.get("phd_strong_fit_penalty",12));reasons.append("PhD required, retained for exceptional fit")
        else:score-=int(r.get("phd_penalty",42));reasons.append("PhD required")
    if "2027" in str(j.get("match","")) or j.get("graduation")=="2027":score+=int(r.get("explicit_2027_bonus",5))
    score=max(0,min(100,score));j["personalized_score"]=score;j["priority"]="Top" if score>=85 else ("Strong" if score>=70 else ("Consider" if score>=50 else "Stretch"));j["personalized_reason"]="; ".join(dict.fromkeys(reasons));return j
def eligible(j,include_general):
    text=" ".join([j.get("role",""),j.get("description",""),j.get("graduation",""),j.get("start_date","")])
    if citizen_required(j) or EXCLUDE.search(j.get("role","")) or pure_hardware(j) or j.get("sponsorship")=="No":return False
    if OLD_YEAR.search(text) and not YEAR_2027.search(text):return False
    if not NORTH_AMERICA.search(j.get("location","")+" "+j.get("country","")):return False
    if YEAR_2027.search(text):j["match"]="Explicit 2027";return True
    if include_general and NEW_GRAD.search(text):j["match"]="General new grad";return True
    return False

def fetch_greenhouse(c):
    data=get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['board']}/jobs?content=true")
    return [job(c["company"],x.get("title",""),(x.get("location") or {}).get("name","Unknown"),x.get("absolute_url",""),f"Greenhouse:{c['board']}",clean(x.get("content"))) for x in data.get("jobs",[])]
def fetch_lever(c):
    out=[]
    for x in get_json(f"https://api.lever.co/v0/postings/{c['site']}?mode=json"):
        cats=x.get("categories") or {};loc=cats.get("location") or ", ".join(cats.get("allLocations") or []) or "Unknown"
        desc=" ".join(clean(x.get(k)) for k in ["descriptionPlain","description","additionalPlain","additional","requirementsPlain"])
        for group in x.get("lists") or []:desc+=" "+clean(group.get("content") or group.get("text"))
        out.append(job(c["company"],x.get("text",""),loc,x.get("hostedUrl") or x.get("applyUrl") or "",f"Lever:{c['site']}",desc))
    return out
def fetch_ashby(c):
    out=[]
    for x in get_json(f"https://api.ashbyhq.com/posting-api/job-board/{c['board']}").get("jobs",[]):
        locs=[x.get("location","")]+[(z.get("location") or z.get("name") or "") for z in (x.get("secondaryLocations") or [])]
        out.append(job(c["company"],x.get("title",""),"; ".join(y for y in locs if y) or "Unknown",x.get("jobUrl") or x.get("applyUrl") or "",f"Ashby:{c['board']}",x.get("descriptionPlain") or clean(x.get("descriptionHtml"))))
    return out
def fetch_smart(c):
    out=[]
    for x in get_json(f"https://api.smartrecruiters.com/v1/companies/{c['slug']}/postings?limit=100").get("content",[]):
        loc=x.get("location") or {};place=", ".join(y for y in [loc.get("city"),loc.get("region"),loc.get("country")] if y) or "Unknown";ref=x.get("ref") or x.get("id") or ""
        out.append(job(c["company"],x.get("name",""),place,ref if str(ref).startswith("http") else f"https://jobs.smartrecruiters.com/{c['slug']}/{ref}",f"SmartRecruiters:{c['slug']}"))
    return out
def apply_source_icons(j,text):
    if "🇺🇸" in text:j["citizenship_required"]="Yes"
    if "🛂" in text:j["sponsorship"]="No"
    if "🔒" in text:j["status"]="Closed"
    return j
def github_readme(c):
    ref=c.get("ref","main");path=c.get("path","README.md").lstrip("/");r=requests.get(f"https://raw.githubusercontent.com/{c['repo']}/{ref}/{path}",headers=HEADERS,timeout=TIMEOUT);r.raise_for_status();text=r.text;out=[];previous_company=""
    md=re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
    for line in text.splitlines():
        if line.count("|")<4:continue
        cells=[x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells)<4 or set(cells[0])<={"-",":"}:continue
        links=md.findall(line)
        if not links:continue
        company=re.sub(r"[*_`]","",cells[0]).replace("↳","").strip();previous_company=company or previous_company;company=company or previous_company
        role=re.sub(r"[*_`]","",cells[1]).strip();loc=clean(cells[2]);url=next((u for _,u in reversed(links) if "github.com/" not in u and "simplify.jobs/p/" not in u),"")
        if company and role and url:out.append(apply_source_icons(job(company,role,loc,url,f"GitHub:{c['repo']}:{path}"),line))
    return out
FETCH={"greenhouse":fetch_greenhouse,"lever":fetch_lever,"ashby":fetch_ashby,"smartrecruiters":fetch_smart,"github_discovery":github_readme}
def discover(cfg):
    out=[]
    for typ,fn in FETCH.items():
        for source in cfg.get(typ,[]) or []:
            if not source.get("enabled",True):continue
            try:found=fn(source);LOG.info("%s %s: %d",typ,source,len(found));out.extend(found)
            except Exception as e:LOG.warning("%s %s failed: %s",typ,source,e)
    return out
def lever_detail_text(url):
    p=urlsplit(url)
    if p.netloc not in {"jobs.lever.co","jobs.eu.lever.co"}:return ""
    parts=[x for x in p.path.split("/") if x]
    if len(parts)<2:return ""
    host="api.eu.lever.co" if p.netloc=="jobs.eu.lever.co" else "api.lever.co"
    try:x=get_json(f"https://{host}/v0/postings/{parts[0]}/{parts[1]}?mode=json")
    except Exception:return ""
    text=" ".join(clean(x.get(k)) for k in ["descriptionPlain","description","additionalPlain","additional","requirementsPlain"])
    for group in x.get("lists") or []:text+=" "+clean(group.get("content") or group.get("text"))
    return text
def enrich_from_text(j,text):
    if not text:return j
    j["status"]="Closed" if CLOSED.search(text[:25000]) else "Open";m=NO_SPONSOR.search(text)
    if m:j["sponsorship"]="No";j["visa_evidence"]=m.group(0)[:180]
    elif (m:=YES_SPONSOR.search(text)):j["sponsorship"]="Yes";j["visa_evidence"]=m.group(0)[:180]
    if (m:=CITIZEN.search(text)):j["citizenship_required"]="Yes";j["visa_evidence"]=j.get("visa_evidence") or m.group(0)[:180]
    ranges=list(SALARY.finditer(text))
    if ranges:
        m=next((x for x in ranges if int(x.group(1).replace(",","").split(".")[0])>=20000),ranges[0]);unit=(m.group(3) or "").lower();suffix="/hr" if "hour" in unit or "/hr" in unit else ("/year" if unit else "");j["salary"]=f"${m.group(1)}–${m.group(2)}{suffix}"
    j["skills"]=", ".join([name for name,p in SKILLS if p.search(text)][:10]);j["description"]=text[:80000];j["phd_required"]="Yes" if phd_is_required(j) else "No";j["last_verified"]=TODAY;return j
def enrich(j):
    if not j.get("url"):return j
    if text:=lever_detail_text(j["url"]):return enrich_from_text(j,text)
    try:r=requests.get(j["url"],headers=HEADERS,timeout=22,allow_redirects=True)
    except requests.RequestException:return j
    if r.status_code in (404,410):j["status"]="Closed";j["last_verified"]=TODAY;return j
    if r.status_code>=400:return j
    return enrich_from_text(j,BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True))

def norm(v):
    v=html.unescape(v or "").casefold().replace("&"," and ");v=re.sub(r"\b(?:incorporated|inc|llc|ltd|corp|corporation)\b"," ",v);v=re.sub(r"[^a-z0-9+#]+"," ",v);return " ".join(v.split())
def natural_key(j):
    role=ROLE_NOISE.sub(" ",j.get("role",""));role=re.sub(r"\([^)]*(?:remote|hybrid|telework)[^)]*\)"," ",role,flags=re.I);loc=re.sub(r"\b(?:united states of america|united states|usa|u\.?s\.?)\b"," ",j.get("location",""),flags=re.I);return "|".join([norm(j.get("company","")),norm(role),norm(loc)])
def quality(j):
    url=j.get("url","").casefold();source=j.get("source","").casefold();score=0
    if any(d in url for d in ["jobs.lever.co","greenhouse.io","ashbyhq.com","smartrecruiters.com","myworkdayjobs.com","workdayjobs.com"]):score+=30
    if any(s in source for s in ["greenhouse:","lever:","ashby:","smartrecruiters:"]):score+=20
    if any(d in url for d in ["linkedin.com","jobrapido.com","ziprecruiter.com"]):score-=15
    if j.get("sponsorship") in {"Yes","No"}:score+=4
    if j.get("salary") not in {"","Unknown","Not listed"}:score+=4
    if j.get("skills"):score+=2
    return score
def merge(a,b):
    primary,secondary=(dict(b),a) if quality(b)>quality(a) else (dict(a),b)
    for k,v in secondary.items():
        if k=="description" and len(v or "")>len(primary.get(k,"") or ""):primary[k]=v
        elif (not primary.get(k) or primary.get(k) in {"Unknown","Not listed","Other"}) and v not in {"","Unknown","Not listed","Other"}:primary[k]=v
    dates=[x for x in [a.get("date_added",""),b.get("date_added","")] if x]
    if dates:primary["date_added"]=min(dates)
    primary["last_verified"]=max(a.get("last_verified",""),b.get("last_verified",""))
    if a.get("status")=="Open" or b.get("status")=="Open":primary["status"]="Open"
    return primary
def dedupe(rows):
    by_url={}
    for j in rows:
        j["url"]=canon(j.get("url",""));j["category"]=j.get("category") if j.get("category") not in {"","Other",None} else classify(j.get("role","")+" "+j.get("description",""));key=j["url"] or f"no-url:{len(by_url)}";by_url[key]=merge(by_url[key],j) if key in by_url else j
    out={}
    for j in by_url.values():key=natural_key(j);out[key]=merge(out[key],j) if key in out else j
    return list(out.values())
def load_csv(path):
    if not path.exists():return []
    with path.open(encoding="utf-8",newline="") as f:return [dict(x) for x in csv.DictReader(f)]
def load_manual(path):
    if not path.exists():return []
    raw=yaml.safe_load(path.read_text(encoding="utf-8")) or {};return [dict(x) for x in raw.get("jobs",[])]
def sort_key(j):
    try:score=int(j.get("personalized_score") or 0)
    except (TypeError,ValueError):score=0
    try:salary=int(j.get("salary_max_annual") or 0)
    except (TypeError,ValueError):salary=0
    return (j.get("status")=="Open",score,salary,j.get("date_added",""),j.get("company","").casefold())
def write(rows):
    prefs=load_preferences();personalized=[];rejected={}
    for j in dedupe(rows):
        j=personalize(j,prefs);reason=personalized_rejection_reason(j,prefs)
        if reason:rejected[reason]=rejected.get(reason,0)+1;continue
        personalized.append(j)
    personalized.sort(key=sort_key,reverse=True);data=ROOT/"data";data.mkdir(parents=True,exist_ok=True)
    for j in personalized:
        for f in FIELDS:j.setdefault(f,"" if f!="salary" else "Not listed")
    with (data/"jobs.csv").open("w",encoding="utf-8",newline="") as f:w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore");w.writeheader();w.writerows(personalized)
    (data/"jobs.json").write_text(json.dumps([{k:j.get(k,"") for k in FIELDS} for j in personalized],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    open_rows=[j for j in personalized if j.get("status")=="Open"];groups={}
    for j in open_rows:groups.setdefault(j.get("category","Other"),[]).append(j)
    parts=["# 2027 North America New Grad Full-Time Jobs","",f"> Last automated update: **{TODAY}** · Open roles: **{len(open_rows)}**","","Personalized for an F-1/OPT candidate targeting high-compensation ML systems, AI infrastructure, distributed systems, software, and quant roles.","","> Hard filters: explicit no-sponsorship, U.S.-citizenship/security-clearance, pure hardware, and stated salary ranges entirely below $100k are excluded.","","> PhD-required roles are strongly down-ranked unless the rest of the role is an exceptional match. Unknown sponsorship or salary is retained but penalized.","","## Legend","","- **Top / Strong / Consider / Stretch** are personalized ranking tiers.","- ✅ sponsorship stated; ❔ sponsorship not clearly stated.","- Salary and visa extraction are best-effort; verify on the employer page.",""]
    for cat in ["Infrastructure / Systems","AI / Machine Learning","Quantitative Finance","Software Engineering","Data Engineering","Cybersecurity","Product Management","Finance / Research","Other"]:
        if cat not in groups:continue
        parts += [f"## {cat}","","| Score | Company | Role | Location | Visa | Salary | Why | Added |","|---:|---|---|---|---|---|---|---|"]
        for j in sorted(groups[cat],key=sort_key,reverse=True):
            visa="✅" if j.get("sponsorship")=="Yes" else "❔";name=j.get("role","").replace("|","/");role=f"[{name}]({j['url']})" if j.get("url") else name;reason=(j.get("personalized_reason") or "—").replace("|","/")
            parts.append(f"| **{j.get('personalized_score',0)} · {j.get('priority','')}** | **{j.get('company','').replace('|','/')}** | {role} | {j.get('location','').replace('|','/')} | {visa} | {(j.get('salary') or 'Not listed').replace('|','/')} | {reason} | {j.get('date_added','')} |")
        parts.append("")
    rejected_text=", ".join(f"{reason}: {count}" for reason,count in sorted(rejected.items())) or "none"
    parts += ["## Automatic updates","","GitHub Actions runs every six hours and can also be started manually from the Actions tab.","",f"Latest hard-filter counts during generation: {rejected_text}.","","## Data","","- `data/jobs.csv`","- `data/jobs.json`","- `config/sources.yml`","- `config/preferences.yml`","","Listings can close or change without notice. Verify all details before applying."]
    (ROOT/"README.md").write_text("\n".join(parts)+"\n",encoding="utf-8")
def main():
    p=argparse.ArgumentParser();p.add_argument("--include-general",action="store_true");p.add_argument("--skip-enrichment",action="store_true");p.add_argument("--workers",type=int,default=4);p.add_argument("--max-detail-pages",type=int,default=300);p.add_argument("--dry-run",action="store_true");a=p.parse_args();logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    cfg=yaml.safe_load((ROOT/"config/sources.yml").read_text(encoding="utf-8")) or {};rows=load_manual(ROOT/"config/manual_jobs.yml")+discover(cfg)+load_csv(ROOT/"data/jobs.csv");rows=dedupe([j for j in rows if eligible(j,a.include_general)])
    if not a.skip_enrichment:
        with ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex:
            futures={ex.submit(enrich,j):i for i,j in enumerate(rows[:a.max_detail_pages])}
            for f in as_completed(futures):
                try:rows[futures[f]]=f.result()
                except Exception as e:LOG.warning("enrichment failed: %s",e)
    if a.dry_run:
        prefs=load_preferences();kept=[]
        for j in rows:
            j=personalize(j,prefs)
            if not personalized_rejection_reason(j,prefs):kept.append(j)
        kept.sort(key=sort_key,reverse=True);print(f"{len(kept)} eligible personalized jobs")
        for j in kept[:30]:print(j.get("personalized_score"),j.get("company",""),"-",j.get("role",""),"-",j.get("salary",""))
        return
    write(rows);print(f"Wrote personalized job list from {len(rows)} candidates")
if __name__=="__main__":main()
