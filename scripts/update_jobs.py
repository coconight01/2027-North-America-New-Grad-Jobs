#!/usr/bin/env python3
from __future__ import annotations

import argparse, csv, html, json, logging, re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests, yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
TODAY = date.today().isoformat()
TIMEOUT = 25
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; 2027-New-Grad-Jobs/0.3; +https://github.com/coconight01/2027-North-America-New-Grad-Jobs)"}
LOG = logging.getLogger("jobs")

FIELDS = ["company","role","category","location","country","graduation","start_date","degree","sponsorship","visa_evidence","citizenship_required","salary","skills","url","source","date_added","last_verified","status","match"]
TRACKING = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content","ref","source","src","gh_src","lever-source","jr_id"}
EXCLUDE = re.compile(r"\b(intern(ship)?|co-?op|apprentice|part[- ]?time|contract(?:or)?)\b", re.I)
NEW_GRAD = re.compile(r"\b(new grad(uate)?|new college grad|university grad(uate)?|college grad|entry[- ]level|early career|graduate (software|quantitative|machine learning|data|hardware|engineer|trader|researcher)|software engineer i\b|engineer i\b)\b", re.I)
YEAR_2027 = re.compile(r"\b(2027|class of 2027|dec(?:ember)? 2026|spring 2027|summer 2027|fall 2026|mid-2027|2026[-–/]2027)\b", re.I)
OLD_YEAR = re.compile(r"\b(2024|2025)\b", re.I)
NORTH_AMERICA = re.compile(r"\b(united states|u\.?s\.?|usa|canada|north america|remote(?:,? (?:us|usa|canada|north america))?|alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|wisconsin|wyoming|ontario|quebec|british columbia|alberta|manitoba|saskatchewan|nova scotia|new brunswick|newfoundland|prince edward island|yukon|northwest territories|chicago|austin|san francisco|seattle|boston|toronto|vancouver|montreal|waterloo|palo alto|mountain view|sunnyvale|san jose|los angeles|miami|atlanta|denver|dallas|houston|bellevue|redmond|pittsburgh|cupertino|fremont|san diego)\b", re.I)
NO_SPONSOR = re.compile(r"\b(?:will not|do not|does not|cannot|can't|unable to) (?:provide|offer)?\s*(?:employment |work |visa )?sponsorship|without (?:current or future )?sponsorship|not eligible for (?:visa )?sponsorship\b", re.I)
YES_SPONSOR = re.compile(r"\b(?:visa|immigration|employment) sponsorship (?:is )?(?:available|provided|offered)|(?:we|company) (?:will|can) sponsor|support (?:for )?work authorization\b", re.I)
CITIZEN = re.compile(r"\b(?:u\.?s\.?|united states) citizenship (?:is )?(?:required|mandatory)|must be (?:a )?(?:u\.?s\.?|united states) citizen|only (?:u\.?s\.?|united states) citizens|active (?:top secret|secret|ts/?sci|security) clearance|(?:ability|eligible|eligibility) to obtain (?:and maintain )?(?:a |an )?(?:top secret|secret|ts/?sci|security) clearance|(?:position appropriate|current) security clearance (?:is )?required\b", re.I)
CLOSED = re.compile(r"\b(job (?:is )?no longer available|position has been filled|posting has expired|applications? (?:are )?closed|this job is closed)\b", re.I)
SALARY = re.compile(r"(?:\$|USD\s*)\s*(\d{2,3}(?:,\d{3})?(?:\.\d+)?)\s*(?:-|–|—|to)\s*(?:\$|USD\s*)?\s*(\d{2,3}(?:,\d{3})?(?:\.\d+)?)(?:\s*(per year|annually|/year|per hour|hourly|/hr))?", re.I)
ROLE_NOISE = re.compile(r"\b(?:potential\s+telework|telework(?:\s+eligible)?|remote(?:\s+eligible)?|hybrid|on[- ]?site)\b", re.I)

SKILLS = [(n,re.compile(p,re.I)) for n,p in [
 ("Python",r"\bpython\b"),("C++",r"(?<!\w)c\+\+(?!\w)"),("Java",r"\bjava\b"),("Go",r"\b(?:golang|go language)\b"),("Rust",r"\brust\b"),("JavaScript",r"\bjavascript\b"),("TypeScript",r"\btypescript\b"),("React",r"\breact(?:\.js)?\b"),("SQL",r"\bsql\b"),("Linux",r"\blinux\b"),("AWS",r"\baws\b|amazon web services"),("GCP",r"\bgcp\b|google cloud"),("Azure",r"\bazure\b"),("Docker",r"\bdocker\b"),("Kubernetes",r"\bkubernetes\b|\bk8s\b"),("PyTorch",r"\bpytorch\b"),("TensorFlow",r"\btensorflow\b"),("JAX",r"\bjax\b"),("CUDA",r"\bcuda\b"),("Machine Learning",r"\bmachine learning\b"),("Deep Learning",r"\bdeep learning\b"),("Distributed Systems",r"\bdistributed systems?\b"),("Data Structures",r"\bdata structures?\b"),("Algorithms",r"\balgorithms?\b"),("Networking",r"\bnetworking\b|network protocols?"),("Databases",r"\bdatabases?\b"),("Compilers",r"\bcompilers?\b"),("FPGA",r"\bfpga\b"),("Verilog",r"\b(?:system)?verilog\b")]]
CATEGORIES = [("Quantitative Finance",r"\b(quant|trader|trading|investment|equities)\b"),("AI / Machine Learning",r"\b(machine learning|artificial intelligence|ai engineer|data scientist|nlp|llm)\b"),("Data Engineering",r"\b(data engineer|analytics engineer|business intelligence)\b"),("Infrastructure / Systems",r"\b(infrastructure|systems|platform|sre|site reliability|distributed|compiler|database|network)\b"),("Hardware Engineering",r"\b(hardware|firmware|fpga|asic|silicon|embedded|verification)\b"),("Cybersecurity",r"\b(cyber|security engineer|information security|threat|soc analyst)\b"),("Product Management",r"\b(product manager|apm|product management)\b"),("Software Engineering",r"\b(software|developer|full[- ]?stack|backend|frontend|forward deployed)\b")]

def clean(v): return BeautifulSoup(html.unescape(v or ""),"html.parser").get_text(" ",strip=True)
def canon(u):
    if not u: return ""
    p=urlsplit(u.strip()); q=urlencode([(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True) if k.casefold() not in TRACKING])
    return urlunsplit((p.scheme.lower(),p.netloc.lower(),p.path.rstrip("/"),q,""))
def job(company,role,location,url,source,description=""):
    return {"company":company.strip(),"role":role.strip(),"category":"Other","location":location.strip() or "Unknown","country":"Unknown","graduation":"Unknown","start_date":"Unknown","degree":"Unknown","sponsorship":"Unknown","visa_evidence":"","citizenship_required":"Unknown","salary":"Not listed","skills":"","url":canon(url),"source":source,"date_added":TODAY,"last_verified":TODAY,"status":"Open","match":"General new grad","description":description}
def get_json(url):
    r=requests.get(url,headers=HEADERS,timeout=TIMEOUT); r.raise_for_status(); return r.json()
def classify(text):
    for name,p in CATEGORIES:
        if re.search(p,text,re.I): return name
    return "Other"
def citizen_required(j):
    if str(j.get("citizenship_required","")).casefold()=="yes": return True
    return bool(CITIZEN.search(" ".join([j.get("role",""),j.get("description",""),j.get("visa_evidence","")])))
def eligible(j,include_general):
    text=" ".join([j.get("role",""),j.get("description",""),j.get("graduation",""),j.get("start_date","")])
    if citizen_required(j) or EXCLUDE.search(j.get("role","")): return False
    if OLD_YEAR.search(text) and not YEAR_2027.search(text): return False
    if not NORTH_AMERICA.search(j.get("location","")+" "+j.get("country","")): return False
    if YEAR_2027.search(text): j["match"]="Explicit 2027"; return True
    if include_general and NEW_GRAD.search(text): j["match"]="General new grad"; return True
    return False

def fetch_greenhouse(c):
    data=get_json(f"https://boards-api.greenhouse.io/v1/boards/{c['board']}/jobs?content=true")
    return [job(c["company"],x.get("title",""),(x.get("location") or {}).get("name","Unknown"),x.get("absolute_url",""),f"Greenhouse:{c['board']}",clean(x.get("content"))) for x in data.get("jobs",[])]
def fetch_lever(c):
    out=[]
    for x in get_json(f"https://api.lever.co/v0/postings/{c['site']}?mode=json"):
        cats=x.get("categories") or {}; loc=cats.get("location") or ", ".join(cats.get("allLocations") or []) or "Unknown"
        desc=" ".join(clean(x.get(k)) for k in ["descriptionPlain","description","additionalPlain","additional","requirementsPlain"])
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
        loc=x.get("location") or {}; place=", ".join(y for y in [loc.get("city"),loc.get("region"),loc.get("country")] if y) or "Unknown"; ref=x.get("ref") or x.get("id") or ""
        out.append(job(c["company"],x.get("name",""),place,ref if str(ref).startswith("http") else f"https://jobs.smartrecruiters.com/{c['slug']}/{ref}",f"SmartRecruiters:{c['slug']}"))
    return out

def apply_source_icons(j,text):
    if "🇺🇸" in text: j["citizenship_required"]="Yes"
    if "🛂" in text: j["sponsorship"]="No"
    if "🔒" in text: j["status"]="Closed"
    return j
def github_readme(c):
    ref=c.get("ref","main"); path=c.get("path","README.md").lstrip("/")
    r=requests.get(f"https://raw.githubusercontent.com/{c['repo']}/{ref}/{path}",headers=HEADERS,timeout=TIMEOUT); r.raise_for_status(); text=r.text; out=[]
    soup=BeautifulSoup(text,"html.parser")
    for row in soup.find_all("tr"):
        cells=row.find_all("td")
        if len(cells)<4: continue
        company=cells[0].get_text(" ",strip=True).replace("↳","").strip(); role=cells[1].get_text(" ",strip=True); loc=cells[2].get_text(" ",strip=True)
        links=[a.get("href","") for a in cells[-1].find_all("a",href=True)]+[a.get("href","") for a in cells[3].find_all("a",href=True)]
        links=[u for u in links if u.startswith("http") and "github.com/" not in u and "simplify.jobs/p/" not in u]
        if company and role and links: out.append(apply_source_icons(job(company,role,loc,links[0],f"GitHub:{c['repo']}:{path}"),row.get_text(" ",strip=True)))
    md=re.compile(r"\[([^\]]+)\]\((https?://[^)]+)\)")
    for line in text.splitlines():
        if line.count("|")<4: continue
        cells=[x.strip() for x in line.strip().strip("|").split("|")]
        if len(cells)<4 or set(cells[0])<={"-",":"}: continue
        links=md.findall(line)
        if not links: continue
        company=re.sub(r"[*_`]","",cells[0]); role=re.sub(r"[*_`]","",cells[1]); loc=cells[2]
        url=next((u for _,u in reversed(links) if "github.com/" not in u and "simplify.jobs/p/" not in u),"")
        if company and role and url: out.append(apply_source_icons(job(company,role,loc,url,f"GitHub:{c['repo']}:{path}"),line))
    return out

FETCH={"greenhouse":fetch_greenhouse,"lever":fetch_lever,"ashby":fetch_ashby,"smartrecruiters":fetch_smart,"github_discovery":github_readme}
def discover(cfg):
    out=[]
    for typ,fn in FETCH.items():
        for source in cfg.get(typ,[]) or []:
            if not source.get("enabled",True): continue
            try:
                found=fn(source); LOG.info("%s %s: %d",typ,source,len(found)); out.extend(found)
            except Exception as e: LOG.warning("%s %s failed: %s",typ,source,e)
    return out

def enrich(j):
    if not j.get("url"): return j
    try: r=requests.get(j["url"],headers=HEADERS,timeout=22,allow_redirects=True)
    except requests.RequestException: return j
    if r.status_code in (404,410): j["status"]="Closed"; j["last_verified"]=TODAY; return j
    if r.status_code>=400: return j
    text=BeautifulSoup(r.text,"html.parser").get_text(" ",strip=True)
    if not text: return j
    j["status"]="Closed" if CLOSED.search(text[:25000]) else "Open"
    m=NO_SPONSOR.search(text)
    if m: j["sponsorship"]="No"; j["visa_evidence"]=m.group(0)[:180]
    else:
        m=YES_SPONSOR.search(text)
        if m: j["sponsorship"]="Yes"; j["visa_evidence"]=m.group(0)[:180]
    m=CITIZEN.search(text)
    if m:
        j["citizenship_required"]="Yes"
        if not j.get("visa_evidence"): j["visa_evidence"]=m.group(0)[:180]
    ranges=list(SALARY.finditer(text))
    if ranges:
        m=next((x for x in ranges if int(x.group(1).replace(",","").split(".")[0])>=20000),ranges[0]); unit=(m.group(3) or "").lower(); suffix="/hr" if "hour" in unit or "/hr" in unit else ("/year" if unit else ""); j["salary"]=f"${m.group(1)}–${m.group(2)}{suffix}"
    j["skills"]=", ".join([name for name,p in SKILLS if p.search(text)][:10]); j["description"]=text[:80000]; j["last_verified"]=TODAY
    return j

def norm(v):
    v=html.unescape(v or "").casefold().replace("&"," and "); v=re.sub(r"\b(?:incorporated|inc|llc|ltd|corp|corporation)\b"," ",v); v=re.sub(r"[^a-z0-9+#]+"," ",v)
    return " ".join(v.split())
def natural_key(j):
    role=ROLE_NOISE.sub(" ",j.get("role","")); role=re.sub(r"\([^)]*(?:remote|hybrid|telework)[^)]*\)"," ",role,flags=re.I)
    loc=re.sub(r"\b(?:united states of america|united states|usa|u\.?s\.?)\b"," ",j.get("location",""),flags=re.I)
    return "|".join([norm(j.get("company","")),norm(role),norm(loc)])
def quality(j):
    url=j.get("url","").casefold(); source=j.get("source","").casefold(); score=0
    if any(d in url for d in ["jobs.lever.co","greenhouse.io","ashbyhq.com","smartrecruiters.com","myworkdayjobs.com","workdayjobs.com"]): score+=30
    if any(s in source for s in ["greenhouse:","lever:","ashby:","smartrecruiters:"]): score+=20
    if any(d in url for d in ["linkedin.com","jobrapido.com","ziprecruiter.com"]): score-=15
    if j.get("sponsorship") in {"Yes","No"}: score+=4
    if j.get("salary") not in {"","Unknown","Not listed"}: score+=4
    if j.get("skills"): score+=2
    return score
def merge(a,b):
    primary,secondary=(dict(b),a) if quality(b)>quality(a) else (dict(a),b)
    for k,v in secondary.items():
        if k=="description" and len(v or "")>len(primary.get(k,"") or ""): primary[k]=v
        elif (not primary.get(k) or primary.get(k) in {"Unknown","Not listed","Other"}) and v not in {"","Unknown","Not listed","Other"}: primary[k]=v
    dates=[x for x in [a.get("date_added",""),b.get("date_added","")] if x]
    if dates: primary["date_added"]=min(dates)
    primary["last_verified"]=max(a.get("last_verified",""),b.get("last_verified",""))
    if a.get("status")=="Open" or b.get("status")=="Open": primary["status"]="Open"
    return primary
def dedupe(rows):
    by_url={}
    for j in rows:
        j["url"]=canon(j.get("url","")); j["category"]=j.get("category") if j.get("category") not in {"","Other"} else classify(j.get("role","")+" "+j.get("description",""))
        key=j["url"] or f"no-url:{len(by_url)}"; by_url[key]=merge(by_url[key],j) if key in by_url else j
    out={}
    for j in by_url.values():
        key=natural_key(j); out[key]=merge(out[key],j) if key in out else j
    return sorted(out.values(),key=lambda x:(x.get("status")!="Open",x.get("date_added",""),x.get("company","").casefold()))
def load_csv(path):
    if not path.exists(): return []
    with path.open(encoding="utf-8",newline="") as f: return [dict(x) for x in csv.DictReader(f)]
def load_manual(path):
    if not path.exists(): return []
    raw=yaml.safe_load(path.read_text(encoding="utf-8")) or {}; return [dict(x) for x in raw.get("jobs",[])]

def write(rows):
    rows=dedupe([j for j in rows if not citizen_required(j)])
    data=ROOT/"data"; data.mkdir(parents=True,exist_ok=True)
    for j in rows:
        for f in FIELDS: j.setdefault(f,"" if f!="salary" else "Not listed")
    with (data/"jobs.csv").open("w",encoding="utf-8",newline="") as f:
        w=csv.DictWriter(f,fieldnames=FIELDS,extrasaction="ignore"); w.writeheader(); w.writerows(rows)
    (data/"jobs.json").write_text(json.dumps([{k:j.get(k,"") for k in FIELDS} for j in rows],ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    open_rows=[j for j in rows if j.get("status")=="Open"]; groups={}
    for j in open_rows: groups.setdefault(j.get("category","Other"),[]).append(j)
    parts=["# 2027 North America New Grad Full-Time Jobs","",f"> Last automated update: **{TODAY}** · Open roles: **{len(open_rows)}**","","Automatically discovers roles from public ATS feeds and GitHub job repositories, then visits eligible job pages to extract visa language, salary ranges, and common skills.","","> Roles that explicitly require U.S. citizenship or a security clearance are excluded.","","## Legend","","- ✅ explicit sponsorship support; ❌ explicit no-sponsorship language; ❔ not clearly stated.","- Skill and compensation fields are best-effort extractions; always verify on the employer page.",""]
    for cat in ["Software Engineering","AI / Machine Learning","Data Engineering","Infrastructure / Systems","Quantitative Finance","Hardware Engineering","Cybersecurity","Product Management","Finance / Research","Other"]:
        if cat not in groups: continue
        parts += [f"## {cat}","","| Company | Role | Location | Visa | Salary | Skills | Added |","|---|---|---|---|---|---|---|"]
        for j in sorted(groups[cat],key=lambda x:(x.get("date_added",""),x.get("company","")),reverse=True):
            visa="✅" if j.get("sponsorship")=="Yes" else ("❌" if j.get("sponsorship")=="No" else "❔"); name=j.get("role","").replace("|","/"); role=f"[{name}]({j['url']})" if j.get("url") else name; skills=(j.get("skills") or "—").replace("|","/")
            parts.append(f"| **{j.get('company','').replace('|','/')}** | {role} | {j.get('location','').replace('|','/')} | {visa} | {(j.get('salary') or 'Not listed').replace('|','/')} | {skills} | {j.get('date_added','')} |")
        parts.append("")
    parts += ["## Automatic updates","","GitHub Actions runs every six hours and can also be started manually from the Actions tab.","","## Data","","- `data/jobs.csv`","- `data/jobs.json`","- `config/sources.yml`","","Listings can close or change without notice. Verify all details before applying."]
    (ROOT/"README.md").write_text("\n".join(parts)+"\n",encoding="utf-8")

def main():
    p=argparse.ArgumentParser(); p.add_argument("--include-general",action="store_true"); p.add_argument("--skip-enrichment",action="store_true"); p.add_argument("--workers",type=int,default=4); p.add_argument("--max-detail-pages",type=int,default=300); p.add_argument("--dry-run",action="store_true"); a=p.parse_args(); logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    cfg=yaml.safe_load((ROOT/"config/sources.yml").read_text(encoding="utf-8")) or {}
    rows=load_manual(ROOT/"config/manual_jobs.yml")+discover(cfg)+load_csv(ROOT/"data/jobs.csv"); rows=dedupe([j for j in rows if eligible(j,a.include_general)])
    if not a.skip_enrichment:
        targets=rows[:a.max_detail_pages]
        with ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex:
            futures={ex.submit(enrich,j):i for i,j in enumerate(targets)}
            for f in as_completed(futures):
                try: rows[futures[f]]=f.result()
                except Exception as e: LOG.warning("enrichment failed: %s",e)
        rows=dedupe([j for j in rows if not citizen_required(j)])
    if a.dry_run:
        print(f"{len(rows)} eligible jobs")
        for x in rows[:30]: print(x.get("company",""),"-",x.get("role",""),"-",x.get("location",""))
        return
    write(rows); print(f"Wrote {len(rows)} jobs")
if __name__=="__main__": main()
