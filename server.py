import os
import sys
import json
import base64
import re
import requests
from fastapi import FastAPI, File, UploadFile, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pathlib import Path
from datetime import datetime

from config import (
    MODEL_NAME, PROMPTS_DIR, GUIDES_DIR, REFERENCES_DIR, DESCRIPTION_MAX_TOKENS, ROAST_MAX_TOKENS,
    HYPOTHESIS_MAX_TOKENS, API_TIMEOUT, MAX_STEPS, PATH_TYPE_TO_GUIDE, REQUIRED_FILES,
    EXTRA_BODY, MOBBIN_API_KEY, MOBBIN_API_URL
)
from openai import OpenAI
from database import (
    init_db, save_report, load_reports, get_products, get_guide_names,
    get_banks, find_best_for_criterion, get_knowledge_base
)
from dashboard import (
    get_reports_for_dashboard, compute_heatmap_data, compute_trend_data, compute_criticality_distribution
)

from dotenv import load_dotenv
load_dotenv()

FIGMA_ACCESS_TOKEN = os.getenv("FIGMA_ACCESS_TOKEN", "")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

client = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio", timeout=API_TIMEOUT)
init_db()

# ---------- Вспомогательные функции ----------
def load_text(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Файл не найден: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

system_desc = load_text(os.path.join(PROMPTS_DIR, "system_description.txt"))
system_roast = load_text(os.path.join(PROMPTS_DIR, "system_roast.txt"))
user_roast_template = load_text(os.path.join(PROMPTS_DIR, "user_roast.txt"))
system_hypothesis = load_text(os.path.join(PROMPTS_DIR, "system_hypothesis.txt"))
user_hypothesis_template = load_text(os.path.join(PROMPTS_DIR, "user_hypothesis.txt"))

def parse_llm_json(text):
    """Умный парсер JSON, исправляющий типичные ошибки модели."""
    if not text:
        return None

    # Убираем markdown-блоки
    text = re.sub(r'```(?:json)?\s*', '', text, flags=re.IGNORECASE)

    # Попытка 1: найти и загрузить JSON напрямую
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    # Попытка 2: исправить пропущенные запятые между объектами
    fixed = re.sub(r'\}\s*\{', '}, {', text)
    start = fixed.find('[')
    end = fixed.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(fixed[start:end+1])
        except json.JSONDecodeError:
            pass

    # Попытка 3: если ответ обрезан — добавляем закрывающую скобку
    if start != -1 and end == -1:
        fixed = text[start:] + ']'
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass

    # Попытка 4: найти и восстановить объекты с повреждёнными ключами
    # (например, когда вместо "критерий" стоит " আক্রম" или "term")
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        raw = text[start:end+1]
        # Убираем лишние символы, которые ломают JSON
        raw = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', raw)
        try:
            items = json.loads(raw)
            if isinstance(items, list):
                # Проверяем каждый объект: если нет ключа "критерий", заменяем первый неизвестный ключ
                for item in items:
                    if isinstance(item, dict) and "критерий" not in item:
                        for key in list(item.keys()):
                            if key not in ["оценка", "статус", "проблема", "рекомендация", "критичность"]:
                                item["критерий"] = item.pop(key)
                                break
                return items
        except json.JSONDecodeError:
            pass

    # Попытка 5: найти объект, а не массив
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass

    return None

def analyze_image(image_data):
    img_b64 = base64.b64encode(image_data).decode("utf-8")
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_desc},
            {
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img_b64}"}},
                    {"type": "text", "text": "Опиши все экраны на этом изображении."}
                ]
            }
        ],
        temperature=0.0,
        max_tokens=DESCRIPTION_MAX_TOKENS,
        extra_body=EXTRA_BODY
    )
    return response.choices[0].message.content.strip()

def analyze_maket(description, guide_text, product, goal, criteria):
    user_prompt = user_roast_template.format(
        guide_text=guide_text, goal=goal, criteria=criteria, product=product, description=description
    )
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_roast},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.0,
        max_tokens=ROAST_MAX_TOKENS,
        stop=None,
        extra_body=EXTRA_BODY
    )
    return response.choices[0].message.content.strip()

def generate_hypotheses(report_items, product, goal, criteria):
    if not report_items:
        return []
    problems = [item for item in report_items if isinstance(item, dict) and item.get("статус") == "проблема"]
    if not problems:
        return []
    user_prompt = user_hypothesis_template.format(
        report_json=json.dumps(problems, ensure_ascii=False, indent=2),
        product=product, goal=goal, criteria=criteria
    )
    response = client.chat.completions.create(
        model=MODEL_NAME,
        messages=[
            {"role": "system", "content": system_hypothesis},
            {"role": "user", "content": user_prompt}
        ],
        temperature=0.0,
        max_tokens=HYPOTHESIS_MAX_TOKENS,
        extra_body=EXTRA_BODY
    )
    raw = response.choices[0].message.content.strip()
    hypotheses = parse_llm_json(raw)
    if hypotheses is not None:
        return hypotheses if isinstance(hypotheses, list) else []
    try:
        hypotheses = json.loads(raw)
        return hypotheses if isinstance(hypotheses, list) else []
    except:
        return []

def slugify(text):
    translit = {
        'а': 'a', 'б': 'b', 'в': 'v', 'г': 'g', 'д': 'd', 'е': 'e', 'ё': 'yo',
        'ж': 'zh', 'з': 'z', 'и': 'i', 'й': 'y', 'к': 'k', 'л': 'l', 'м': 'm',
        'н': 'n', 'о': 'o', 'п': 'p', 'р': 'r', 'с': 's', 'т': 't', 'у': 'u',
        'ф': 'f', 'х': 'h', 'ц': 'ts', 'ч': 'ch', 'ш': 'sh', 'щ': 'sch',
        'ъ': '', 'ы': 'y', 'ь': '', 'э': 'e', 'ю': 'yu', 'я': 'ya',
        ' ': '_', '-': '_'
    }
    text = text.lower()
    result = ''.join(translit.get(c, c) for c in text)
    result = re.sub(r'[^a-z0-9_]', '', result)
    result = re.sub(r'_+', '_', result).strip('_')
    return result or "unnamed"

def save_version_files(bank, product, scenario, files, version_id):
    bank_slug = slugify(bank)
    product_slug = slugify(product)
    scenario_slug = slugify(scenario)
    version_dir = os.path.join(REFERENCES_DIR, "history", bank_slug, product_slug, scenario_slug, version_id)
    os.makedirs(version_dir, exist_ok=True)

    step_files = []
    for idx, file in enumerate(files):
        ext = os.path.splitext(file.filename)[1] or ".png"
        step_name = f"step_{idx+1}{ext}"
        step_path = os.path.join(version_dir, step_name)
        with open(step_path, "wb") as f:
            f.write(file.file.read())
        step_files.append(step_name)

    latest_link = os.path.join(os.path.dirname(version_dir), "latest")
    if os.path.islink(latest_link) or os.path.exists(latest_link):
        os.unlink(latest_link)
    os.symlink(version_id, latest_link)

    return version_dir, step_files, bank_slug, product_slug, scenario_slug

# ---------- API-эндпоинты ----------
@app.get("/")
async def serve_frontend():
    frontend_path = Path(__file__).parent / "index.html"
    if frontend_path.exists():
        return HTMLResponse(content=frontend_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>index.html не найден</h1>")

@app.get("/guides")
async def get_guides():
    if not os.path.isdir(GUIDES_DIR):
        return []
    return [f for f in os.listdir(GUIDES_DIR) if f.endswith(".txt")]

@app.get("/banks")
async def get_banks_list():
    banks = get_banks()
    banks_json_path = os.path.join(REFERENCES_DIR, "banks.json")
    if os.path.exists(banks_json_path):
        with open(banks_json_path, "r", encoding="utf-8") as f:
            preset = json.load(f)
        for v in preset.values():
            if v not in banks:
                banks.append(v)
    return sorted(banks)

@app.get("/products")
async def get_products_list():
    products = get_products()
    products_json_path = os.path.join(REFERENCES_DIR, "products.json")
    if os.path.exists(products_json_path):
        with open(products_json_path, "r", encoding="utf-8") as f:
            preset = json.load(f)
        for v in preset.values():
            if v not in products:
                products.append(v)
    return sorted(products)

@app.post("/analyze")
async def analyze(
    product: str = Form(...),
    goal: str = Form(...),
    criteria: str = Form(...),
    guide: str = Form(...),
    bank: str = Form(""),
    files: list[UploadFile] = File(...)
):
    guide_text = load_text(os.path.join(GUIDES_DIR, guide))
    descriptions = []
    for idx, file in enumerate(files):
        img_data = await file.read()
        desc = analyze_image(img_data)
        descriptions.append(f"=== Шаг {idx+1} ===\n{desc}")
    full_description = "\n\n".join(descriptions)
    report_raw = analyze_maket(full_description, guide_text, product, goal, criteria)
    items = parse_llm_json(report_raw)
    hypotheses = generate_hypotheses(items, product, goal, criteria) if items else []

    version_id = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    version_path, step_files, bank_slug, product_slug, scenario_slug = save_version_files(
        bank, product, goal, files, version_id
    )

    metadata = {
        "version_id": version_id,
        "bank": bank,
        "product": product,
        "goal": goal,
        "criteria": criteria,
        "guide": guide,
        "path_type": {v: k for k, v in PATH_TYPE_TO_GUIDE.items()}.get(guide, ""),
        "timestamp": datetime.now().isoformat(),
        "steps": [{"step": i+1, "file": sf} for i, sf in enumerate(step_files)],
        "descriptions": descriptions,
        "report": items,
        "hypotheses": hypotheses
    }
    with open(os.path.join(version_path, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    try:
        save_report(
            image_name=", ".join(step_files),
            description=full_description,
            report_json=items if items else report_raw,
            guide=guide_text,
            product_name=product,
            journey_goal=goal,
            success_criteria=criteria,
            guide_name=guide,
            bank=bank,
            scenario_slug=scenario_slug,
            version_path=version_path
        )
    except Exception as e:
        print(f"Ошибка сохранения в БД: {e}")

    return {
        "description": full_description,
        "report_items": items,
        "report_raw": report_raw,
        "hypotheses": hypotheses,
        "version_path": version_path,
        "scenario_slug": scenario_slug
    }

@app.post("/save_report")
async def save_report_endpoint(
    description: str = Form(...),
    report_json: str = Form(...),
    product: str = Form(...),
    goal: str = Form(...),
    criteria: str = Form(...),
    guide_name: str = Form(...),
    image_names: str = Form(...),
    bank: str = Form(""),
    scenario_slug: str = Form(""),
    version_path: str = Form("")
):
    try:
        guide_text = load_text(os.path.join(GUIDES_DIR, guide_name))
        report_items = json.loads(report_json)
        report_id = save_report(
            image_name=image_names,
            description=description,
            report_json=report_items,
            guide=guide_text,
            product_name=product,
            journey_goal=goal,
            success_criteria=criteria,
            guide_name=guide_name,
            bank=bank,
            scenario_slug=scenario_slug,
            version_path=version_path
        )
        return {"success": True, "report_id": report_id}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.get("/history")
async def get_history(limit: int = 50, product: str = "", guide: str = "", bank: str = "", scenario: str = ""):
    reports = load_reports(limit=limit, product_name=product, guide_name=guide, bank=bank, scenario_slug=scenario)
    result = []
    for r in reports:
        d = dict(r)
        try:
            d["report_items"] = json.loads(d["report_json"])
        except:
            d["report_items"] = []
        result.append(d)
    return result

@app.get("/dashboard")
async def get_dashboard_data(product: str = "", guide: str = "", days: int = 90):
    reports = get_reports_for_dashboard(product_name=product, guide_name=guide, days=days)
    heatmap = compute_heatmap_data(reports)
    trend = compute_trend_data(reports)
    crit_dist = compute_criticality_distribution(reports)
    return {"heatmap": heatmap, "trend": trend, "crit_dist": crit_dist}

@app.get("/references")
async def get_references(query: str = "", limit: int = 3):
    if not query:
        return []
    own = find_best_for_criterion(query, limit=limit)
    results = []
    for item in own:
        if item.get("version_path"):
            vp = item["version_path"]
            if os.path.isdir(vp):
                imgs = [f for f in os.listdir(vp) if f.endswith((".png", ".jpg", ".jpeg"))]
                if imgs:
                    results.append({
                        "app_name": f"{item.get('product', '')} (оценка 2)",
                        "image_path": os.path.join(vp, imgs[0]),
                        "source": "history"
                    })
    external = search_all_references(query, limit - len(results))
    results.extend(external)
    return results[:limit]

@app.get("/knowledge_base")
async def knowledge_base():
    return get_knowledge_base()

def search_local_references(query: str, limit: int = 3):
    results = []
    ref_dir = os.path.join(REFERENCES_DIR, "local")
    if not os.path.isdir(ref_dir):
        return results
    query_words = query.lower().split()
    for filename in os.listdir(ref_dir):
        if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        image_path = os.path.join(ref_dir, filename)
        name_without_ext = os.path.splitext(filename)[0]
        txt_path = os.path.join(ref_dir, name_without_ext + ".txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                description = f.read().lower()
        else:
            description = name_without_ext.lower()
        score = sum(1 for word in query_words if word in description)
        if score > 0:
            results.append({"image_path": image_path, "app_name": name_without_ext, "score": score, "source": "local"})
    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]

def search_mobbin_references(query: str, limit: int = 3):
    if not MOBBIN_API_KEY:
        return []
    headers = {"Authorization": f"Bearer {MOBBIN_API_KEY}", "Content-Type": "application/json"}
    params = {"query": query, "platform": "ios", "per_page": limit}
    try:
        response = requests.get(MOBBIN_API_URL, headers=headers, params=params, timeout=10)
        response.raise_for_status()
        data = response.json()
        screens = []
        for item in data.get("screens", []):
            screens.append({
                "app_name": item.get("app_name", "Неизвестное приложение"),
                "image_url": item.get("image_url", ""),
                "mobbin_url": item.get("url", ""),
                "source": "mobbin"
            })
        return screens
    except Exception:
        return []

def search_all_references(query: str, limit: int = 3):
    local_results = search_local_references(query, limit)
    remaining = limit - len(local_results)
    mobbin_results = search_mobbin_references(query, remaining) if remaining > 0 else []
    return (local_results + mobbin_results)[:limit]

# ---------- ИНТЕГРАЦИЯ С FIGMA ----------
@app.get("/figma/pages")
async def get_figma_pages(file_key: str):
    if not FIGMA_ACCESS_TOKEN:
        return {"error": "FIGMA_ACCESS_TOKEN не задан в .env"}
    
    headers = {"X-Figma-Token": FIGMA_ACCESS_TOKEN}
    file_url = f"https://api.figma.com/v1/files/{file_key}?depth=1"
    try:
        resp = requests.get(file_url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        document = data.get("document")
        pages = []
        for child in document.get("children", []):
            if child.get("type") == "CANVAS":
                frame_count = len([c for c in child.get("children", []) if c.get("type") == "FRAME"])
                pages.append({
                    "name": child.get("name"),
                    "frame_count": frame_count
                })
        return {"pages": pages}
    except requests.exceptions.Timeout:
        return {"error": "Таймаут при обращении к Figma API (проверьте интернет/VPN)"}
    except Exception as e:
        return {"error": str(e)}

@app.post("/figma/analyze")
async def analyze_from_figma(
    file_key: str = Form(...),
    page_name: str = Form(""),
    page_index: str = Form(""),
    product: str = Form(...),
    goal: str = Form(...),
    criteria: str = Form(...),
    guide: str = Form(...),
    bank: str = Form("")
):
    if not FIGMA_ACCESS_TOKEN:
        return {"error": "FIGMA_ACCESS_TOKEN не задан в .env"}

    page_index_int = None
    if page_index.strip():
        try:
            page_index_int = int(page_index)
        except ValueError:
            return {"error": f"Неверный номер страницы: {page_index}"}

    headers = {"X-Figma-Token": FIGMA_ACCESS_TOKEN}
    
    file_url = f"https://api.figma.com/v1/files/{file_key}?depth=1"
    try:
        file_resp = requests.get(file_url, headers=headers, timeout=30)
        file_resp.raise_for_status()
    except requests.exceptions.Timeout:
        return {"error": "Таймаут при получении файла Figma (проверьте интернет/VPN)"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Ошибка доступа к файлу: {e}"}

    file_data = file_resp.json()
    document = file_data.get("document")
    
    all_canvases = [c for c in document.get("children", []) if c.get("type") == "CANVAS"]

    target_canvas = None
    if page_index_int is not None and page_index_int > 0:
        idx = page_index_int - 1
        if idx < len(all_canvases):
            target_canvas = all_canvases[idx]
        else:
            return {"error": f"Страница с номером {page_index_int} не найдена. Всего страниц: {len(all_canvases)}"}
    elif page_name.strip():
        for canvas in all_canvases:
            if canvas.get("name", "").strip().lower() == page_name.strip().lower():
                target_canvas = canvas
                break
        if target_canvas is None:
            names = [c.get("name", "") for c in all_canvases]
            return {"error": f"Страница с именем '{page_name}' не найдена. Доступные: {', '.join(names)}"}
    else:
        if all_canvases:
            target_canvas = all_canvases[0]
        else:
            return {"error": "В файле не найдено страниц (CANVAS)"}

    canvas_id = target_canvas["id"]
    image_url = f"https://api.figma.com/v1/images/{file_key}?ids={canvas_id}&format=png&scale=2"
    try:
        image_resp = requests.get(image_url, headers=headers, timeout=30)
        image_resp.raise_for_status()
    except requests.exceptions.Timeout:
        return {"error": "Таймаут при получении изображения страницы (проверьте интернет/VPN)"}
    except requests.exceptions.RequestException as e:
        return {"error": f"Ошибка получения изображения страницы: {e}"}

    images_data = image_resp.json().get("images", {})
    if not images_data or canvas_id not in images_data:
        return {"error": "Не удалось получить изображение страницы"}
    
    img_url = images_data[canvas_id]
    try:
        img_resp = requests.get(img_url, timeout=30)
        img_resp.raise_for_status()
    except:
        return {"error": "Не удалось скачать изображение страницы (включите VPN?)"}
    
    filename = f"figma_page_{canvas_id}.png"
    filepath = os.path.join(REFERENCES_DIR, "figma_export", filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "wb") as f:
        f.write(img_resp.content)

    print(f"Скриншот страницы '{target_canvas.get('name', '')}' сохранён: {filepath}")

    class FakeUploadFile:
        def __init__(self, path):
            self.filename = os.path.basename(path)
            self.file = open(path, "rb")
        async def read(self):
            return self.file.read()

    fake_files = [FakeUploadFile(filepath)]
    try:
        result = await analyze(
            product=product,
            goal=goal,
            criteria=criteria,
            guide=guide,
            bank=bank,
            files=fake_files
        )
        return result
    finally:
        for f in fake_files:
            f.file.close()