#!/usr/bin/env python3
"""
Daily Adora Report.
Runs daily to summarize ad analysis performance.
"""
import subprocess
import datetime
import json
import os

def run_psql(sql):
    cmd = ['sudo', '-u', 'postgres', 'psql', '-d', 'firecrawl', '-t', '-P', 'format=unaligned', '-c', sql]
    return subprocess.run(cmd, capture_output=True, text=True)

def get_stats():
    today = datetime.date.today()
    yesterday = today - datetime.timedelta(days=1)
    
    # Total Analyzed Yesterday
    sql_total = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{yesterday} 00:00:00' AND analyzed_at < '{today} 00:00:00';"
    total = int(run_psql(sql_total).stdout.strip() or 0)
    
    # Risky Ads (Score >= 0.5)
    sql_risky = f"SELECT COUNT(*) FROM ads_with_urls WHERE analyzed_at >= '{yesterday} 00:00:00' AND analyzed_at < '{today} 00:00:00' AND analysis_score >= 0.5;"
    risky = int(run_psql(sql_risky).stdout.strip() or 0)
    
    # Safe Ads
    safe = total - risky
    
    # Risk DB Upserts (Approximation: Risky ads are always upserted)
    upserted = risky 

    # Remaining Backlog
    sql_pending = "SELECT COUNT(*) FROM ads_with_urls WHERE analysis_score IS NULL;"
    pending = int(run_psql(sql_pending).stdout.strip() or 0)
    
    return {
        "date": str(yesterday),
        "total": total,
        "risky": risky,
        "safe": safe,
        "upserted": upserted,
        "pending": pending
    }

def main():
    LOG_FILE = "/home/ubuntu/adora_ops/daily_reports.log"
    stats = get_stats()
    
    report = f"""
=========================================
DAILY REPORT: {stats['date']}
=========================================
ads_tested:       {stats['total']}
risky_found:      {stats['risky']} ({stats['risky']/stats['total']*100 if stats['total'] else 0:.1f}%)
safe_cleared:     {stats['safe']}
copied_to_riskdb: {stats['upserted']}
remaining_backlog:{stats['pending']}
=========================================
"""
    print(report)
    
    # Append to log file
    with open(LOG_FILE, "a") as f:
        f.write(report + "\n")

if __name__ == "__main__":
    main()
