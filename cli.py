import asyncio
import os
import sys
import uuid
import typer
from pathlib import Path
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.live import Live

# Ensure project root is in path
sys.path.append(str(Path(__file__).parent))

from engine.quick_scan import run_quick_scan
from backend.worker import run_deep_scan
from backend.database import SessionLocal, engine
from backend.models import Base

app = typer.Typer(help="QAPAL CLI — Test any website from your terminal.")
console = Console()

@app.command()
def quick_scan(
    url: str = typer.Argument(..., help="URL to scan"),
    headless: bool = typer.Option(True, help="Run browser in headless mode"),
):
    """
    Run browser-native Quick Scan rules (Free Tier).
    Injects the extension's JS rules directly into the page.
    """
    console.print(f"\n[bold blue]🚀 Running Quick Scan on {url}...[/bold blue]")
    
    with console.status("[bold green]Injecting scanner bundle and analyzing DOM..."):
        try:
            result = asyncio.run(run_quick_scan(url, headless=headless))
        except Exception as e:
            console.print(f"[bold red]Error:[/] {e}")
            raise typer.Exit(code=1)

    # Render Results
    score = result.get("score", 0)
    # Estimate score if not present (Quick Scan JS usually returns just issues)
    # For now, let's just show issues.
    
    issues = result.get("issues", [])
    console.print(Panel(
        f"Found [bold]{len(issues)}[/bold] potential issues.\n"
        f"Duration: [dim]{result.get('duration_ms', 0)}ms[/dim]",
        title="[bold green]Quick Scan Results[/bold green]",
        expand=False
    ))

    if not issues:
        console.print("[bold green]✨ No issues found![/bold green]")
        return

    table = Table(title="Issues Found", show_header=True, header_style="bold magenta")
    table.add_column("Severity", style="dim", width=12)
    table.add_column("Module", width=12)
    table.add_column("Issue", style="cyan")
    table.add_column("Message")

    for issue in issues:
        sev = issue.get("severity", "info").lower()
        color = "red" if sev == "critical" else "yellow" if sev == "high" else "blue"
        table.add_row(
            f"[{color}]{sev.upper()}[/{color}]",
            issue.get("module", "general"),
            issue.get("rule", "N/A"),
            issue.get("message", "")
        )

    console.print(table)


@app.command()
def deep_scan(
    url: str = typer.Argument(..., help="URL to deep scan"),
    max_pages: int = typer.Option(3, help="Maximum pages to crawl"),
):
    """
    Run AI-powered Deep Scan pipeline (Premium Tier).
    Crawl -> Plan -> Execute -> Report.
    """
    console.print(f"\n[bold purple]🧠 Starting Deep Scan Pipeline for {url}...[/bold purple]")
    
    # Setup a dummy user and job in the local DB for the CLI run
    job_id = str(uuid.uuid4())
    user_id = "cli-user"
    
    # Initialize DB tables if they don't exist
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # Create a dummy user if not exists
        from backend.models import User, Job
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            user = User(id=user_id, email="cli@qapal.local", tier="pro")
            db.add(user)
            db.commit()
            
        # Create the job
        job = Job(id=job_id, user_id=user_id, url=url, options={"max_pages": max_pages})
        db.add(job)
        db.commit()
    finally:
        db.close()

    # Run the worker pipeline
    # We'll use a Live display to show progress updates from the DB
    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        transient=True,
    ) as progress:
        task = progress.add_task("[cyan]Initializing pipeline...", total=100)
        
        async def monitor_and_run():
            # Start the scan in the background
            worker_task = asyncio.create_task(run_deep_scan(job_id))
            
            # Poll the DB for progress updates
            final_job = None
            while not worker_task.done():
                await asyncio.sleep(0.5)
                db = SessionLocal()
                try:
                    job = db.query(Job).filter(Job.id == job_id).first()
                    if job:
                        progress.update(task, completed=job.progress, description=f"[cyan]{job.message}")
                        final_job = job
                finally:
                    db.close()
            
            await worker_task
            
            # Fetch the definitive final state
            db = SessionLocal()
            try:
                final_job = db.query(Job).filter(Job.id == job_id).first()
                # Return the report dict
                return final_job.report if final_job else {}
            finally:
                db.close()

        try:
            report_data = asyncio.run(monitor_and_run())
            if not report_data:
                console.print("[bold red]No report data found for this job.[/]")
                return

            console.print(Panel(
                f"Status: [bold green]COMPLETE[/bold green]\n"
                f"Score: [bold]{report_data.get('score', 'N/A')}[/bold]\n"
                f"Summary: [dim]{report_data.get('summary', 'N/A')}[/dim]\n"
                f"Actions: {report_data.get('actions_taken', 0)} performed",
                title="[bold purple]Deep Scan Results[/bold purple]",
                expand=False
            ))
            
            # Summary of issues
            issues = report_data.get("issues", [])
            if issues:
                table = Table(title="Top Issues", show_header=True)
                table.add_column("Type")
                table.add_column("Severity")
                table.add_column("Description")
                for issue in issues[:5]: # Show top 5
                    table.add_row(
                        str(issue.get("type", "N/A")),
                        str(issue.get("severity", "N/A")),
                        str(issue.get("description", ""))
                    )
                console.print(table)
            else:
                console.print("[bold green]✓ No major issues found by AI![/bold green]")
                
        except Exception as e:
            console.print(f"\n[bold red]Pipeline Failed:[/] {e}")
            raise typer.Exit(code=1)

if __name__ == "__main__":
    app()
