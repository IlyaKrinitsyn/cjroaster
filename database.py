import sqlite3
import json
from datetime import datetime
from pathlib import Path
from config import BASE_DIR

DB_PATH = Path(BASE_DIR) / "reports.db"

def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                image_name TEXT,
                description TEXT,
                report_json TEXT,
                guide_snapshot TEXT
            )
        """)
        for col, col_type in [
            ("product_name", "TEXT"),
            ("journey_goal", "TEXT"),
            ("success_criteria", "TEXT"),
            ("guide_name", "TEXT"),
            ("bank", "TEXT"),
            ("scenario_slug", "TEXT")
        ]:
            try:
                conn.execute(f"ALTER TABLE reports ADD COLUMN {col} {col_type}")
            except sqlite3.OperationalError:
                pass

        conn.execute("""
            CREATE TABLE IF NOT EXISTS criteria_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                report_id INTEGER REFERENCES reports(id),
                bank TEXT,
                product TEXT,
                scenario_slug TEXT,
                version_path TEXT,
                criterion TEXT,
                score INTEGER,
                status TEXT,
                problem TEXT,
                recommendation TEXT,
                timestamp TEXT
            )
        """)

        # Таблица для API-ключей
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                key TEXT UNIQUE NOT NULL,
                owner_name TEXT,
                created_at TEXT NOT NULL,
                active INTEGER DEFAULT 1
            )
        """)
        conn.commit()

def save_report(image_name: str, description: str, report_json: list | str,
                guide: str, product_name: str, journey_goal: str,
                success_criteria: str, guide_name: str = "",
                bank: str = "", scenario_slug: str = "",
                version_path: str = "") -> int:
    if isinstance(report_json, list):
        report_str = json.dumps(report_json, ensure_ascii=False, indent=2)
    else:
        report_str = report_json

    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO reports 
               (timestamp, image_name, description, report_json, guide_snapshot,
                product_name, journey_goal, success_criteria, guide_name, bank, scenario_slug)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (datetime.now().isoformat(), image_name, description, report_str,
             guide, product_name, journey_goal, success_criteria, guide_name, bank, scenario_slug)
        )
        report_id = cursor.lastrowid

        # Заполняем criteria_history
        if isinstance(report_json, list):
            for item in report_json:
                if isinstance(item, dict) and "критерий" in item:
                    conn.execute(
                        """INSERT INTO criteria_history 
                           (report_id, bank, product, scenario_slug, version_path,
                            criterion, score, status, problem, recommendation, timestamp)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (report_id, bank, product_name, scenario_slug, version_path,
                         item.get("критерий", ""), item.get("оценка", -1),
                         item.get("статус", ""), item.get("проблема", ""),
                         item.get("рекомендация", ""), datetime.now().isoformat())
                    )
        conn.commit()
        return report_id

def load_reports(limit: int = 50, product_name: str = "", guide_name: str = "",
                 bank: str = "", scenario_slug: str = "") -> list[dict]:
    with get_connection() as conn:
        conditions = []
        params = []
        if product_name:
            conditions.append("product_name = ?")
            params.append(product_name)
        if guide_name:
            conditions.append("guide_name = ?")
            params.append(guide_name)
        if bank:
            conditions.append("bank = ?")
            params.append(bank)
        if scenario_slug:
            conditions.append("scenario_slug = ?")
            params.append(scenario_slug)

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        query = f"SELECT * FROM reports {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def get_report_by_id(report_id: int) -> dict | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone()
        return dict(row) if row else None

def get_products() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT product_name FROM reports WHERE product_name IS NOT NULL ORDER BY product_name"
        ).fetchall()
        return [row[0] for row in rows]

def get_guide_names() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT guide_name FROM reports WHERE guide_name IS NOT NULL AND guide_name != '' ORDER BY guide_name"
        ).fetchall()
        return [row[0] for row in rows]

def get_banks() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT DISTINCT bank FROM reports WHERE bank IS NOT NULL AND bank != '' ORDER BY bank"
        ).fetchall()
        return [row[0] for row in rows]

def find_best_for_criterion(criterion: str, bank: str = "", product: str = "", limit: int = 3):
    with get_connection() as conn:
        conditions = ["criterion = ?", "score = 2"]
        params = [criterion]
        if bank:
            conditions.append("bank = ?")
            params.append(bank)
        if product:
            conditions.append("product = ?")
            params.append(product)
        where = "WHERE " + " AND ".join(conditions)
        query = f"SELECT * FROM criteria_history {where} ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]

def get_knowledge_base():
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT bank, product_name, scenario_slug, guide_name, timestamp, id
            FROM reports
            WHERE bank IS NOT NULL AND bank != ''
            ORDER BY bank, product_name, scenario_slug, timestamp DESC
        """).fetchall()
    tree = {}
    for r in rows:
        bank = r["bank"]
        product = r["product_name"] or "без продукта"
        scenario = r["scenario_slug"] or "без сценария"
        if bank not in tree:
            tree[bank] = {}
        if product not in tree[bank]:
            tree[bank][product] = {}
        if scenario not in tree[bank][product]:
            tree[bank][product][scenario] = []
        tree[bank][product][scenario].append({
            "id": r["id"],
            "guide": r["guide_name"],
            "timestamp": r["timestamp"]
        })
    return tree

def validate_api_key(key: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM api_keys WHERE key = ? AND active = 1",
            (key,)
        ).fetchone()
        return dict(row) if row else None

def add_api_key(key: str, owner_name: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO api_keys (key, owner_name, created_at) VALUES (?, ?, ?)",
            (key, owner_name, datetime.now().isoformat())
        )
        conn.commit()
        return cursor.lastrowid

def revoke_api_key(key: str):
    with get_connection() as conn:
        conn.execute("UPDATE api_keys SET active = 0 WHERE key = ?", (key,))
        conn.commit()

def list_api_keys():
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT id, key, owner_name, created_at, active FROM api_keys ORDER BY created_at DESC"
        ).fetchall()
        return [dict(row) for row in rows]