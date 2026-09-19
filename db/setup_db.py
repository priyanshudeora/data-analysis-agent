"""Create the bundled SQLite database from the portable CSV dataset."""
from __future__ import annotations

import csv
import sqlite3
from contextlib import closing
from pathlib import Path

HERE = Path(__file__).resolve().parent
DB_PATH = HERE / "layoffs.db"
CSV_PATH = HERE / "sample_data.csv"


def setup_database(db_path: Path = DB_PATH, csv_path: Path = CSV_PATH) -> Path:
    if not csv_path.exists():
        raise FileNotFoundError(f"Dataset not found: {csv_path}")
    with closing(sqlite3.connect(db_path)) as conn, conn, csv_path.open(newline="", encoding="utf-8") as handle:
        conn.execute("DROP TABLE IF EXISTS layoffs")
        conn.execute("""CREATE TABLE layoffs (
            company TEXT NOT NULL, industry TEXT NOT NULL, region TEXT NOT NULL,
            date TEXT NOT NULL, employees_laid_off INTEGER NOT NULL CHECK(employees_laid_off >= 0),
            funds_raised REAL NOT NULL CHECK(funds_raised >= 0))""")
        rows = list(csv.DictReader(handle))
        conn.executemany(
            "INSERT INTO layoffs VALUES (:company, :industry, :region, :date, :employees_laid_off, :funds_raised)", rows
        )
        conn.execute("CREATE INDEX idx_layoffs_date ON layoffs(date)")
        conn.execute("CREATE INDEX idx_layoffs_industry ON layoffs(industry)")
    return db_path


if __name__ == "__main__":
    print(f"Created {setup_database()}")
