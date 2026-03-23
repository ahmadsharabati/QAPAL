import asyncio
import os
import sys
import json
import time
from pathlib import Path
from typing import List, Dict, Any

# Ensure project root is in path
sys.path.append(str(Path(__file__).parent.parent))

from engine.quick_scan import run_quick_scan
# from cli import quick_scan as cli_quick_scan # Remove to avoid typer dependency issues in script env
from backend.worker import run_deep_scan
from backend.database import SessionLocal, engine
from backend.models import Base, Job, User

# Diverse set of real-world sites
GAUNTLET_SITES = [
    {"name": "TodoMVC (React)", "url": "https://demo.playwright.dev/todomvc/#/", "framework": "React"},
    {"name": "The Internet (Edge Cases)", "url": "https://the-internet.herokuapp.com/", "framework": "Legacy"},
]

async def run_site_scan(site: Dict[str, str], mode: str = "quick"):
    """Run a single scan for a site and return results."""
    url = site["url"]
    start_time = time.monotonic()
    
    print(f"[{mode.upper()}] Scanning {site['name']} ({url})...")
    
    try:
        if mode == "quick":
            result = await run_quick_scan(url, headless=True)
            duration = int((time.monotonic() - start_time) * 1000)
            return {
                "site": site["name"],
                "framework": site["framework"],
                "score": result.get("score", "N/A"),
                "issues": len(result.get("issues", [])),
                "categories": "N/A",  # Quick scan doesn't use the new taxonomy yet
                "healed": 0,
                "duration": duration,
                "status": "PASS"
            }
        else:
            # Deep Scan logic (requires DB)
            from uuid import uuid4
            job_id = str(uuid4())
            user_id = "gauntlet-user"
            
            # Setup DB record
            db = SessionLocal()
            try:
                job = Job(id=job_id, user_id=user_id, url=url, options={"max_pages": 1})
                db.add(job)
                db.commit()
            finally:
                db.close()
                
            await run_deep_scan(job_id)
            
            # Fetch final report
            db = SessionLocal()
            try:
                job = db.query(Job).filter(Job.id == job_id).first()
                report = job.report if job else {}
                duration = int((time.monotonic() - start_time) * 1000)
                
                # ── Phase 4 Metrics ───────────────────────────────────
                issues = report.get("issues", [])
                categories = list(set(i.get("rule", "UNKNOWN") for i in issues))
                healed_count = sum(1 for p in (report.get("exec_results") or []) 
                                  for s in p.get("steps", []) if s.get("healed"))
                
                return {
                    "site": site["name"],
                    "framework": site["framework"],
                    "score": report.get("score", 0),
                    "issues": len(issues),
                    "categories": ", ".join(categories) if categories else "None",
                    "healed": healed_count,
                    "duration": duration,
                    "status": job.state.upper() if job else "FAILED"
                }
            finally:
                db.close()
                
    except Exception as e:
        print(f"!! Error scanning {site['name']}: {e}")
        return {
            "site": site["name"],
            "framework": site["framework"],
            "score": 0,
            "issues": 0,
            "categories": "N/A",
            "healed": 0,
            "duration": 0,
            "status": f"ERROR: {str(e)[:50]}"
        }

async def run_gauntlet(mode: str = "quick"):
    """Run scans for all sites in the gauntlet."""
    results = []
    # Initialize DB for deep scan if needed
    if mode == "deep":
        Base.metadata.create_all(bind=engine)
        db = SessionLocal()
        from backend.models import User
        if not db.query(User).filter(User.id == "gauntlet-user").first():
            db.add(User(id="gauntlet-user", email="gauntlet@qapal.local", tier="pro"))
            db.commit()
        db.close()

    for site in GAUNTLET_SITES:
        res = await run_site_scan(site, mode=mode)
        results.append(res)
        # Small delay between scans
        await asyncio.sleep(2)
        
    # Generate Markdown Report
    report_path = Path("gauntlet_results.md")
    with open(report_path, "w") as f:
        f.write(f"# QAPAL Gauntlet Results ({mode.upper()})\n\n")
        f.write(f"Generated at: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        f.write("| Site | Framework | Score | Issues | Categories | Heals | Status | Duration |\n")
        f.write("| --- | --- | --- | --- | --- | --- | --- | --- |\n")
        for r in results:
            f.write(f"| {r['site']} | {r['framework']} | {r['score']} | {r['issues']} | {r['categories']} | {r['healed']} | {r['status']} | {r['duration']}ms |\n")
        
    print(f"\n✅ Gauntlet complete! Results saved to {report_path}")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["quick", "deep"], default="quick")
    args = parser.parse_args()
    
    asyncio.run(run_gauntlet(mode=args.mode))
