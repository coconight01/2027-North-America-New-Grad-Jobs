#!/usr/bin/env python3
"""Path-aware GitHub and optional Google Jobs discovery."""
from __future__ import annotations

import argparse, html, logging, os, re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests, yaml
from bs4 import BeautifulSoup

from update_jobs import (
    HEADERS, LOG, ROOT, TIMEOUT, apply_source_icons, citizen_required,
    dedupe, eligible, enrich, job, load_csv, write,
)

MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)]+)\)")
HTML_LINK = re.compile(r'href=["\'](https?://[^"\']+)["\']', re.I)
BLOCKED_LINKS = ("github.com/", "simplify.jobs/p/")

def application_link(line):
    links=[url for _,url in MARKDOWN_LINK.findall(line)]+HTML_LINK.findall(line)
    return next((url for url in reversed(links) if not any(x in url for x in BLOCKED_LINKS)),"")

def text_cell(value):
    value=re.sub(r"<[^>]+>"," ",value)
    value=re.sub(r"\[([^\]]+)\]\([^)]+\)",r"\1",value)
    value=value.replace("**","").replace("`","").replace("↳","").strip()
    return BeautifulSoup(html.unescape(value),"html.parser").get_text(" ",strip=True)

def github_table(source):
    repo=source["repo"]; ref=source.get("ref","main"); path=source.get("path","README.md").lstrip("/")
    max_rows=max(1,int(source.get("max_rows",1000))); assume_2027=bool(source.get("assume_2027",False))
    r=requests.get(f"https://raw.githubusercontent.com/{repo}/{ref}/{path}",headers=HEADERS,timeout=TIMEOUT); r.raise_for_status()
    rows=[]; previous_company=""
    for line in r.text.splitlines():
        if len(rows)>=max_rows: break
        if line.count("|")<4: continue
        cells=[cell.strip() for cell in line.strip().strip("|").split("|")]
        if len(cells)<4 or set(cells[0])<={"-",":"}: continue
        company=text_cell(cells[0]); role=text_cell(cells[1]); location=text_cell(cells[2])
        if not role or role.casefold() in {"role","position","job title"}: continue
        if company: previous_company=company
        else: company=previous_company
        apply_url=application_link(line)
        if not company or not apply_url: continue
        item=apply_source_icons(job(company,role,location or "Unknown",apply_url,f"GitHub:{repo}:{path}"),line)
        if assume_2027:
            item["graduation"]="2027"; item["match"]="Trusted 2027 source"
        if len(cells)>=5:
            salary=text_cell(cells[3])
            if "$" in salary or re.search(r"\b\d{2,3}k/(?:yr|year)\b",salary,re.I): item["salary"]=salary
        if not citizen_required(item): rows.append(item)
    return rows

def google_jobs(source):
    api_key=os.getenv(source.get("api_key_env","SERPAPI_KEY"),"").strip()
    if not api_key:
        LOG.info("Google Jobs skipped: SERPAPI_KEY repository secret is not configured"); return []
    rows=[]; max_pages=max(1,min(int(source.get("max_pages",1)),3))
    for query in source.get("queries",[]):
        next_token=None
        for _ in range(max_pages):
            params={"engine":"google_jobs","q":query["q"],"api_key":api_key,"hl":query.get("hl","en"),"gl":query.get("gl","us")}
            if query.get("location"): params["location"]=query["location"]
            if next_token: params["next_page_token"]=next_token
            r=requests.get("https://serpapi.com/search.json",params=params,headers=HEADERS,timeout=TIMEOUT); r.raise_for_status(); data=r.json()
            for result in data.get("jobs_results",[]):
                options=result.get("apply_options") or []
                apply_url=next((x.get("link","") for x in options if x.get("link")),"")
                if not apply_url: continue
                description=" ".join([result.get("description","")," ".join(result.get("extensions") or [])])
                item=job(result.get("company_name","Unknown"),result.get("title",""),result.get("location","Unknown"),apply_url,"Google Jobs via SerpAPI",description)
                if not citizen_required(item): rows.append(item)
            next_token=(data.get("serpapi_pagination") or {}).get("next_page_token")
            if not next_token: break
    return rows

def main():
    p=argparse.ArgumentParser(); p.add_argument("--workers",type=int,default=4); p.add_argument("--max-detail-pages",type=int,default=200); p.add_argument("--dry-run",action="store_true"); p.add_argument("--skip-github",action="store_true"); p.add_argument("--skip-google",action="store_true"); a=p.parse_args()
    logging.basicConfig(level=logging.INFO,format="%(levelname)s %(message)s")
    cfg=yaml.safe_load((ROOT/"config/sources.yml").read_text(encoding="utf-8")) or {}; discovered=[]
    for source in ([] if a.skip_github else (cfg.get("extra_github_discovery",[]) or [])):
        if not source.get("enabled",True): continue
        try:
            found=github_table(source); LOG.info("extra GitHub %s/%s: %d",source["repo"],source.get("path","README.md"),len(found)); discovered.extend(found)
        except Exception as e: LOG.warning("extra GitHub %s failed: %s",source,e)
    search=cfg.get("serpapi_google_jobs") or {}
    if not a.skip_google and search.get("enabled",False):
        try:
            found=google_jobs(search); LOG.info("Google Jobs via SerpAPI: %d",len(found)); discovered.extend(found)
        except Exception as e: LOG.warning("Google Jobs discovery failed: %s",e)
    candidates=dedupe([x for x in discovered if eligible(x,False)])
    if candidates:
        targets=candidates[:a.max_detail_pages]
        with ThreadPoolExecutor(max_workers=max(1,a.workers)) as ex:
            futures={ex.submit(enrich,x):i for i,x in enumerate(targets)}
            for f in as_completed(futures):
                try: candidates[futures[f]]=f.result()
                except Exception as e: LOG.warning("extra enrichment failed: %s",e)
        candidates=dedupe([x for x in candidates if not citizen_required(x)])
    merged=dedupe(load_csv(ROOT/"data/jobs.csv")+candidates)
    if a.dry_run:
        print(f"Discovered {len(candidates)} eligible extra jobs; merged total {len(merged)}")
        for x in candidates[:30]: print(x["company"],"-",x["role"],"-",x["location"])
        return 0
    write(merged); print(f"Merged {len(candidates)} extra jobs; total {len(merged)}"); return 0

if __name__=="__main__": raise SystemExit(main())
