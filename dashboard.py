import json
from database import get_connection
from collections import defaultdict
from datetime import datetime, timedelta


def get_reports_for_dashboard(product_name="", guide_name="", days=90):
    """Загружает отчёты для дашборда с фильтрацией."""
    with get_connection() as conn:
        conditions = []
        params = []
        
        if product_name:
            conditions.append("product_name = ?")
            params.append(product_name)
        if guide_name:
            conditions.append("guide_name = ?")
            params.append(guide_name)
        if days:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            conditions.append("timestamp >= ?")
            params.append(cutoff)
        
        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)
        
        query = f"SELECT * FROM reports {where} ORDER BY timestamp DESC"
        rows = conn.execute(query, params).fetchall()
        return [dict(row) for row in rows]


def compute_heatmap_data(reports: list[dict]) -> dict:
    """
    Строит тепловую карту: по каждому продукту и критерию — средняя оценка.
    Возвращает:
    {
      "products": ["Продукт A", "Продукт B"],
      "criteria": ["Ясность CTA", ...],
      "scores": {
        "Продукт A": {"Ясность CTA": 1.5, ...},
        "Продукт B": {"Ясность CTA": 2.0, ...}
      }
    }
    """
    # Собираем оценки по продуктам и критериям
    product_criteria_scores = defaultdict(lambda: defaultdict(list))
    
    for rep in reports:
        product = rep["product_name"] or "Без продукта"
        try:
            items = json.loads(rep["report_json"])
        except json.JSONDecodeError:
            continue
        
        if not isinstance(items, list):
            continue
        
        for item in items:
            if not isinstance(item, dict):
                continue
            # Новый формат: "критерий" + "оценка"
            if "критерий" in item and "оценка" in item:
                criterion = item["критерий"]
                try:
                    score = int(item["оценка"])
                    product_criteria_scores[product][criterion].append(score)
                except (ValueError, TypeError):
                    pass
    
    # Собираем все уникальные критерии
    all_criteria = set()
    for product_scores in product_criteria_scores.values():
        all_criteria.update(product_scores.keys())
    criteria_list = sorted(all_criteria)
    
    # Считаем средние оценки
    products = sorted(product_criteria_scores.keys())
    scores = {}
    for product in products:
        scores[product] = {}
        for criterion in criteria_list:
            vals = product_criteria_scores[product].get(criterion, [])
            scores[product][criterion] = sum(vals) / len(vals) if vals else None
    
    return {
        "products": products,
        "criteria": criteria_list,
        "scores": scores
    }


def compute_trend_data(reports: list[dict]) -> list[dict]:
    """
    Строит тренд «было/стало»: группирует отчёты по product_name + image_name
    и возвращает список точек с датой и средним баллом.
    """
    # Группируем по (product, image_name)
    grouped = defaultdict(list)
    for rep in reports:
        key = (rep["product_name"] or "?", rep["image_name"] or "?")
        try:
            items = json.loads(rep["report_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        
        scores = []
        for item in items:
            if isinstance(item, dict) and "оценка" in item:
                try:
                    scores.append(int(item["оценка"]))
                except (ValueError, TypeError):
                    pass
        
        if scores:
            grouped[key].append({
                "timestamp": rep["timestamp"],
                "avg_score": sum(scores) / len(scores)
            })
    
    # Оставляем только группы с 2+ точками и возвращаем плоским списком
    trend = []
    for key, points in grouped.items():
        if len(points) >= 2:
            product, image = key
            for p in sorted(points, key=lambda x: x["timestamp"]):
                trend.append({
                    "product": product,
                    "image": image,
                    "timestamp": p["timestamp"][:19],
                    "avg_score": round(p["avg_score"], 2)
                })
    return trend


def compute_criticality_distribution(reports: list[dict]) -> dict:
    """Считает распределение критичности по всем отчётам."""
    counts = defaultdict(int)
    for rep in reports:
        try:
            items = json.loads(rep["report_json"])
        except json.JSONDecodeError:
            continue
        if not isinstance(items, list):
            continue
        
        for item in items:
            if isinstance(item, dict) and "критичность" in item:
                crit = item["критичность"]
                counts[crit] += 1
    
    return dict(counts)