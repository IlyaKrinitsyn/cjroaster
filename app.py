import streamlit as st
from openai import OpenAI
import os
import sys
import json
import base64
import re
import requests
import pandas as pd
from datetime import datetime
from config import (
    MODEL_NAME, PROMPTS_DIR, GUIDES_DIR, REFERENCES_DIR, DESCRIPTION_MAX_TOKENS, ROAST_MAX_TOKENS,
    HYPOTHESIS_MAX_TOKENS, API_TIMEOUT, MAX_STEPS, PATH_TYPE_TO_GUIDE, REQUIRED_FILES,
    EXTRA_BODY, MOBBIN_API_KEY, MOBBIN_API_URL
)
from database import init_db, save_report, load_reports, get_products, get_guide_names
from dashboard import (
    get_reports_for_dashboard, compute_heatmap_data, compute_trend_data, compute_criticality_distribution
)

# ---------------------------------------------
# Проверка окружения
# ---------------------------------------------
def check_required_files():
    missing = [f for f in REQUIRED_FILES if not os.path.exists(f)]
    if missing:
        print("❌ Отсутствуют обязательные файлы:")
        for f in missing:
            print(f"   - {f}")
        print("Пожалуйста, создайте недостающие файлы и перезапустите приложение.")
        sys.exit(1)
    if not os.path.isdir(GUIDES_DIR) or not [f for f in os.listdir(GUIDES_DIR) if f.endswith(".txt")]:
        print(f"❌ В папке {GUIDES_DIR} нет .txt файлов с гайдами.")
        sys.exit(1)
    os.makedirs(REFERENCES_DIR, exist_ok=True)

# ---------------------------------------------
# Универсальная загрузка текстовых файлов
# ---------------------------------------------
def load_text(path):
    if not os.path.exists(path):
        raise FileNotFoundError(f"Файл не найден: {path}")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def load_prompt(name):
    return load_text(os.path.join(PROMPTS_DIR, name))

def load_guide(filename):
    return load_text(os.path.join(GUIDES_DIR, filename))

# ---------------------------------------------
# Инициализация клиента и БД
# ---------------------------------------------
check_required_files()

client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lm-studio",
    timeout=API_TIMEOUT
)

init_db()

# ---------------------------------------------
# Загрузка промптов
# ---------------------------------------------
system_desc = load_prompt("system_description.txt")
system_roast = load_prompt("system_roast.txt")
user_roast_template = load_prompt("user_roast.txt")
system_hypothesis = load_prompt("system_hypothesis.txt")
user_hypothesis_template = load_prompt("user_hypothesis.txt")

# ---------------------------------------------
# Работа с session_state
# ---------------------------------------------
def init_session_state():
    defaults = {
        "reset_trigger": False,
        "num_steps": 1,
        "current_description": None,
        "current_report_items": None,
        "current_report_raw": None,
        "current_hypotheses": None,
        "current_image_name": None,
        "current_product_name": None,
        "current_journey_goal": None,
        "current_success_criteria": None,
        "current_guide_name": None,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value

init_session_state()

# ---------------------------------------------
# Умный парсинг JSON
# ---------------------------------------------
def parse_llm_json(text):
    text = re.sub(r'```(?:json)?\s*', '', text, flags=re.IGNORECASE)
    start = text.find('[')
    end = text.rfind(']')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass
    start = text.find('{')
    end = text.rfind('}')
    if start != -1 and end != -1 and end > start:
        try:
            return json.loads(text[start:end+1])
        except json.JSONDecodeError:
            pass
    return None

# ---------------------------------------------
# Функции анализа
# ---------------------------------------------
def analyze_image_locally(image_data):
    img_b64 = base64.b64encode(image_data).decode("utf-8")
    try:
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
        content = response.choices[0].message.content.strip()
        if not content.endswith((".", "!", "?")):
            content += " [описание могло быть неполным]"
        return content
    except Exception as e:
        st.error(f"❌ Ошибка при получении описания: {e}")
        return None

def analyze_maket(description, guide_text, product, goal, criteria):
    user_prompt = user_roast_template.format(
        guide_text=guide_text,
        goal=goal,
        criteria=criteria,
        product=product,
        description=description
    )
    try:
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
    except Exception as e:
        st.error(f"❌ Ошибка при прожарке: {e}")
        return None

def generate_hypotheses(report_items, product, goal, criteria):
    if not report_items:
        return []

    problems = [item for item in report_items if isinstance(item, dict) and item.get("статус") == "проблема"]
    if not problems:
        return []

    user_prompt = user_hypothesis_template.format(
        report_json=json.dumps(problems, ensure_ascii=False, indent=2),
        product=product,
        goal=goal,
        criteria=criteria
    )
    try:
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
        hypotheses = json.loads(raw)
        return hypotheses if isinstance(hypotheses, list) else []
    except Exception as e:
        st.error(f"❌ Ошибка при генерации гипотез: {e}")
        return []

# ---------------------------------------------
# Поиск референсов: локальная библиотека + Mobbin API
# ---------------------------------------------
def search_local_references(query: str, limit: int = 3):
    results = []
    if not os.path.isdir(REFERENCES_DIR):
        return results

    query_words = query.lower().split()
    for filename in os.listdir(REFERENCES_DIR):
        if not filename.lower().endswith((".png", ".jpg", ".jpeg")):
            continue
        image_path = os.path.join(REFERENCES_DIR, filename)
        name_without_ext = os.path.splitext(filename)[0]
        txt_path = os.path.join(REFERENCES_DIR, name_without_ext + ".txt")
        if os.path.exists(txt_path):
            with open(txt_path, "r", encoding="utf-8") as f:
                description = f.read().lower()
        else:
            description = name_without_ext.lower()

        score = sum(1 for word in query_words if word in description)
        if score > 0:
            results.append({
                "image_path": image_path,
                "app_name": name_without_ext,
                "score": score,
                "source": "local"
            })

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]


def search_mobbin_references(query: str, limit: int = 3):
    if not MOBBIN_API_KEY:
        return []

    headers = {
        "Authorization": f"Bearer {MOBBIN_API_KEY}",
        "Content-Type": "application/json"
    }
    params = {
        "query": query,
        "platform": "ios",
        "per_page": limit
    }
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
    except Exception as e:
        print(f"Mobbin API error: {e}")
        return []


def search_all_references(query: str, limit: int = 3):
    local_results = search_local_references(query, limit)
    remaining = limit - len(local_results)
    mobbin_results = search_mobbin_references(query, remaining) if remaining > 0 else []
    combined = local_results + mobbin_results
    return combined[:limit]

# ---------------------------------------------
# Генерация HTML-отчёта
# ---------------------------------------------
def generate_html_report(description, report_items, hypotheses, product, goal, criteria, guide_name):
    now = datetime.now().strftime('%d.%m.%Y %H:%M')
    
    styles = """
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Arial, sans-serif; margin: 40px; color: #1a1a1a; line-height: 1.5; }
        h1 { font-size: 28px; border-bottom: 2px solid #2563eb; padding-bottom: 10px; }
        h2 { font-size: 20px; margin-top: 30px; color: #2563eb; }
        h3 { font-size: 16px; margin-top: 25px; }
        .meta { color: #666; margin-bottom: 20px; }
        table { width: 100%; border-collapse: collapse; margin: 20px 0; }
        th, td { padding: 10px; text-align: left; border-bottom: 1px solid #ddd; vertical-align: top; }
        th { background-color: #f8fafc; font-weight: 600; }
        .ok { color: #16a34a; }
        .problem { color: #dc2626; }
        .score { font-weight: 600; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 12px; font-weight: 600; }
        .tag-blocker { background: #fee2e2; color: #991b1b; }
        .tag-high { background: #fed7aa; color: #9a3412; }
        .tag-medium { background: #fef08a; color: #854d0e; }
        .tag-low { background: #e0e7ff; color: #3730a3; }
        .hypothesis { background: #f0fdf4; border-left: 4px solid #16a34a; padding: 15px; margin: 10px 0; border-radius: 4px; }
        @media print { body { margin: 20px; } button { display: none; } }
        button { background: #2563eb; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 14px; margin-bottom: 20px; }
        button:hover { background: #1d4ed8; }
    </style>
    """
    
    html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<title>Прожарка CJM – {product}</title>
{styles}
</head>
<body>
<button onclick="window.print()">🖨️ Распечатать отчёт</button>
<h1>Отчёт прожарки клиентского пути</h1>
<div class="meta">
    <p><strong>Продукт:</strong> {product}</p>
    <p><strong>Цель:</strong> {goal}</p>
    <p><strong>Критерий успеха:</strong> {criteria}</p>
    <p><strong>Гайд:</strong> {guide_name}</p>
    <p><strong>Дата:</strong> {now}</p>
</div>

<h2>📝 Описание макета</h2>
<pre style="white-space: pre-wrap; background: #f8fafc; padding: 15px; border-radius: 6px; font-size: 13px;">{description[:5000]}</pre>

<h2>📊 Результаты проверки</h2>
<table>
<tr><th>Критерий</th><th>Оценка</th><th>Статус</th><th>Проблема / Рекомендация</th></tr>
"""
    
    if report_items:
        for item in report_items:
            if isinstance(item, dict) and "критерий" in item:
                status = item.get("статус", "?")
                is_ok = status == "выполнено"
                icon = "✅" if is_ok else "❌"
                score = item.get("оценка", "-")
                problem_text = item.get("проблема", "")
                recommendation = item.get("рекомендация", "")
                criticality = item.get("критичность", "")

                crit_class = ""
                if criticality == "блокер": crit_class = "tag-blocker"
                elif criticality == "высокая": crit_class = "tag-high"
                elif criticality == "средняя": crit_class = "tag-medium"
                elif criticality == "низкая": crit_class = "tag-low"

                detail = ""
                if not is_ok:
                    if problem_text:
                        detail += f"<strong>Проблема:</strong> {problem_text}<br>"
                    if recommendation:
                        detail += f"<strong>Рекомендация:</strong> {recommendation}<br>"
                    if criticality:
                        detail += f"<span class='tag {crit_class}'>{criticality}</span>"
                else:
                    detail = "Критерий выполнен успешно"
                
                html += f"<tr><td>{item['критерий']}</td><td class='score'>{score}/2</td><td class='{'ok' if is_ok else 'problem'}'>{icon}</td><td>{detail}</td></tr>"
            else:
                html += f"<tr><td colspan='4'>- {item.get('проблема', '?')}</td></tr>"
    else:
        html += "<tr><td colspan='4'>Нет данных для отображения</td></tr>"
    
    html += "</table>"
    
    if hypotheses:
        html += "<h2>🧪 A/B-гипотезы</h2>"
        for h in hypotheses:
            html += f"""
<div class="hypothesis">
    <strong>Критерий:</strong> {h.get('критерий', '?')}<br>
    <strong>Гипотеза:</strong> {h.get('гипотеза', '?')}<br>
    <strong>Действие:</strong> {h.get('действие', '?')}<br>
    <strong>Метрика:</strong> {h.get('метрика', '?')}<br>
    <strong>Ожидаемый эффект:</strong> {h.get('ожидаемый_эффект', '?')}
</div>"""
    
    html += "\n</body>\n</html>"
    return html

# ---------------------------------------------
# Вспомогательные функции интерфейса
# ---------------------------------------------
def clear_current_results():
    keys = [
        "current_description", "current_report_items", "current_report_raw",
        "current_hypotheses", "current_image_name", "current_product_name",
        "current_journey_goal", "current_success_criteria", "current_guide_name"
    ]
    for key in keys:
        if key in st.session_state:
            del st.session_state[key]

def display_report_items(items, hypotheses=None):
    for item in items:
        if isinstance(item, dict) and "критерий" in item:
            status = item.get("статус", "?")
            icon = "✅" if status == "выполнено" else "❌"
            with st.expander(f"{icon} {item['критерий'][:120]}…"):
                if status == "проблема":
                    st.markdown(f"**Проблема:** {item.get('проблема', '-')}")
                    st.markdown(f"**Рекомендация:** {item.get('рекомендация', '-')}")
                    st.markdown(f"**Критичность:** `{item.get('критичность', '-')}`")

                    # 🔍 ПОИСК РЕФЕРЕНСОВ (локально + Mobbin)
                    with st.spinner("🔍 Ищем примеры..."):
                        refs = search_all_references(item.get("проблема", ""), limit=3)
                        if refs:
                            st.markdown("#### 📋 Примеры из базы референсов:")
                            ref_cols = st.columns(len(refs))
                            for i, ref in enumerate(refs):
                                with ref_cols[i]:
                                    if ref["source"] == "local":
                                        st.image(ref["image_path"], caption=ref["app_name"])
                                    else:
                                        st.image(ref["image_url"], caption=ref["app_name"])
                                        if "mobbin_url" in ref:
                                            st.markdown(f"[Открыть в Mobbin]({ref['mobbin_url']})")

                    if hypotheses:
                        for h in hypotheses:
                            if h.get("критерий") == item.get("критерий"):
                                with st.expander("🧪 A/B-гипотеза"):
                                    st.markdown(f"**Гипотеза:** {h.get('гипотеза', '-')}")
                                    st.markdown(f"**Действие:** {h.get('действие', '-')}")
                                    st.markdown(f"**Метрика:** {h.get('метрика', '-')}")
                                    st.markdown(f"**Ожидаемый эффект:** {h.get('ожидаемый_эффект', '-')}")
                                break
                else:
                    st.markdown("Критерий выполнен успешно.")
        else:
            st.markdown(f"- **{item.get('проблема', 'Проблема')}**")
            st.markdown(f"  Рекомендация: {item.get('рекомендация', '-')}")
            st.markdown(f"  Критичность: `{item.get('критичность', '-')}`")

def show_save_and_export_buttons():
    if st.session_state.current_description is None:
        return

    st.divider()
    st.subheader("💾 Сохранение и экспорт")
    col_save, col_report = st.columns(2)

    with col_save:
        if st.button("💾 Сохранить отчёт в базу", type="primary"):
            try:
                guide = load_guide(st.session_state.current_guide_name)
                report_json = st.session_state.current_report_items or st.session_state.current_report_raw
                report_id = save_report(
                    image_name=st.session_state.current_image_name,
                    description=st.session_state.current_description,
                    report_json=report_json,
                    guide=guide,
                    product_name=st.session_state.current_product_name,
                    journey_goal=st.session_state.current_journey_goal,
                    success_criteria=st.session_state.current_success_criteria,
                    guide_name=st.session_state.current_guide_name
                )
                st.success(f"Отчёт сохранён! ID записи: {report_id}")
                clear_current_results()
                st.rerun()
            except Exception as e:
                st.error(f"❌ Ошибка сохранения в базу: {e}")

    with col_report:
        if st.button("📄 Скачать HTML-отчёт"):
            try:
                html = generate_html_report(
                    description=st.session_state.current_description,
                    report_items=st.session_state.current_report_items,
                    hypotheses=st.session_state.current_hypotheses,
                    product=st.session_state.current_product_name,
                    goal=st.session_state.current_journey_goal,
                    criteria=st.session_state.current_success_criteria,
                    guide_name=st.session_state.current_guide_name
                )
                st.download_button(
                    label="📥 Скачать HTML",
                    data=html,
                    file_name=f"prozharka_{datetime.now().strftime('%Y%m%d_%H%M')}.html",
                    mime="text/html"
                )
                with st.expander("📋 Предпросмотр отчёта", expanded=True):
                    st.markdown(html, unsafe_allow_html=True)
            except Exception as e:
                st.error(f"❌ Ошибка при создании отчёта: {e}")

    if st.button("🔄 Повторить анализ"):
        st.session_state.reset_trigger = True
        st.rerun()

# ---------------------------------------------
# Интерфейс Streamlit
# ---------------------------------------------
st.set_page_config(page_title="CJM Прожарка", layout="wide")
st.title("🔥 Прожарка клиентского пути (CJM)")

with st.sidebar:
    st.header("📋 Гайд")
    if st.session_state.current_guide_name:
        guide_info = load_guide(st.session_state.current_guide_name)
        st.text_area("Активный гайд", guide_info, height=200, disabled=True)
        st.caption(f"Файл: {st.session_state.current_guide_name}")
    else:
        st.info("Выберите тип пути в форме анализа.")
    st.divider()
    st.header("⚙️ Действия")
    mode = st.radio("Режим", ["🔍 Прожарка", "📚 История", "📊 Дашборд"])
    if st.button("🛑 Остановить / Сбросить"):
        st.session_state.reset_trigger = True
        st.rerun()

if st.session_state.reset_trigger:
    st.session_state.reset_trigger = False
    num_steps = st.session_state.get("num_steps", 1)
    for key in list(st.session_state.keys()):
        if key not in ["num_steps"]:
            del st.session_state[key]
    st.session_state.num_steps = num_steps
    init_session_state()
    st.rerun()

# ========== РЕЖИМ: ДАШБОРД ==========
if mode == "📊 Дашборд":
    st.subheader("📊 Дашборд «Здоровье CJM»")
    products = get_products()
    all_guides = get_guide_names()
    col_prod, col_guide, col_days = st.columns(3)
    with col_prod:
        product_filter = st.selectbox("Продукт", ["Все"] + products, key="dash_product")
    with col_guide:
        guide_filter = st.selectbox("Гайд", ["Все"] + all_guides, key="dash_guide")
    with col_days:
        days = st.slider("Дней назад", 7, 365, 90, key="dash_days")
    reports = get_reports_for_dashboard(
        product_name="" if product_filter == "Все" else product_filter,
        guide_name="" if guide_filter == "Все" else guide_filter,
        days=days
    )
    if not reports:
        st.info("Нет отчётов за выбранный период.")
        st.stop()

    st.subheader("🗺️ Тепловая карта по критериям")
    heatmap = compute_heatmap_data(reports)
    if heatmap["products"] and heatmap["criteria"]:
        heatmap_rows = []
        for criterion in heatmap["criteria"]:
            row = {"Критерий": criterion[:60]}
            for product in heatmap["products"]:
                score = heatmap["scores"][product].get(criterion)
                row[product] = score
            heatmap_rows.append(row)
        df_heatmap = pd.DataFrame(heatmap_rows).set_index("Критерий")
        def color_cells(val):
            if val is None or pd.isna(val):
                return "background-color: #f0f0f0"
            if val >= 1.8:
                return "background-color: #c8e6c9"
            elif val >= 1.0:
                return "background-color: #fff9c4"
            else:
                return "background-color: #ffcdd2"
        st.dataframe(
            df_heatmap.style.format("{:.1f}", na_rep="-").map(color_cells),
            use_container_width=True
        )
        st.caption("🟢 ≥ 1.8  |  🟡 1.0–1.7  |  🔴 < 1.0  |  Серый: нет данных")
    else:
        st.info("Недостаточно данных для тепловой карты (нужны отчёты с оценками 0/1/2).")

    st.subheader("📈 Было / Стало")
    trend = compute_trend_data(reports)
    if trend:
        df_trend = pd.DataFrame(trend)
        for product in df_trend["product"].unique():
            product_data = df_trend[df_trend["product"] == product]
            for image in product_data["image"].unique():
                subset = product_data[product_data["image"] == image]
                if len(subset) >= 2:
                    st.line_chart(
                        subset.set_index("timestamp")["avg_score"],
                        use_container_width=True
                    )
                    st.caption(f"{product} — {image}")
    else:
        st.info("Нет повторных прожарок одного макета для отображения тренда.")

    st.subheader("⚠️ Распределение критичности")
    crit_dist = compute_criticality_distribution(reports)
    if crit_dist:
        df_crit = pd.DataFrame({
            "Критичность": list(crit_dist.keys()),
            "Количество": list(crit_dist.values())
        }).sort_values("Количество", ascending=False)
        st.bar_chart(df_crit.set_index("Критичность"), use_container_width=True)
    else:
        st.info("Нет данных о критичности (нужны отчёты с полем 'критичность').")
    st.stop()

# ========== РЕЖИМ: ИСТОРИЯ ==========
if mode == "📚 История":
    st.subheader("📚 История прожарок")
    products = get_products()
    all_guides = get_guide_names()
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        product_filter = st.selectbox("Фильтр по продукту", ["Все"] + products)
    with col_f2:
        guide_filter = st.selectbox("Фильтр по гайду", ["Все"] + all_guides)
    reports = load_reports(
        limit=50,
        product_name="" if product_filter == "Все" else product_filter,
        guide_name="" if guide_filter == "Все" else guide_filter
    )
    if not reports:
        st.info("Нет сохранённых отчётов.")
    else:
        for rep in reports:
            short_desc = rep['description'][:150] if rep['description'] else "—"
            guide_label = rep['guide_name'] or "без гайда"
            with st.expander(
                f"{rep['timestamp'][:19]} | {guide_label} | {rep['product_name'] or '?'} | {short_desc}…"
            ):
                st.markdown(f"**Продукт:** {rep['product_name']}")
                st.markdown(f"**Гайд:** {guide_label}")
                st.markdown(f"**Цель:** {rep['journey_goal']}")
                st.markdown(f"**Критерий успеха:** {rep['success_criteria']}")
                st.markdown(f"**Файлы:** {rep['image_name']}")
                try:
                    items = json.loads(rep['report_json'])
                    display_report_items(items)
                except json.JSONDecodeError:
                    st.text(rep['report_json'])
    st.stop()

# ========== РЕЖИМ: ПРОЖАРКА ==========
with st.form("analysis_form"):
    st.subheader("Параметры клиентского пути")
    col_a, col_b, col_type = st.columns(3)
    with col_a:
        product_name = st.text_input("Название продукта *", placeholder="Например, Премиум-банкинг")
    with col_b:
        journey_goal = st.text_input("Цель клиентского пути *", placeholder="Например, Оформление кредитной карты")
    with col_type:
        path_type = st.selectbox("Тип пути *", list(PATH_TYPE_TO_GUIDE.keys()))
    
    col_c, _ = st.columns([1, 1])
    with col_c:
        success_criteria = st.text_input("Критерий успешного завершения *",
                                         placeholder="Например, Заявка отправлена, клиент получил решение")

    st.subheader("🖼️ Экраны клиентского пути")
    st.caption("Загрузите скриншоты шагов. Пустые шаги будут пропущены.")
    
    step_files = {}
    for i in range(1, st.session_state.num_steps + 1):
        step_files[i] = st.file_uploader(f"Шаг {i}", type=["png", "jpg", "jpeg"], key=f"step_{i}")

    if st.session_state.num_steps < MAX_STEPS:
        if st.form_submit_button("➕ Добавить шаг"):
            st.session_state.num_steps += 1
            st.rerun()

    submitted = st.form_submit_button("🚀 Запустить прожарку")

if submitted:
    if not all([product_name, journey_goal, success_criteria]):
        st.error("❌ Заполните все обязательные поля.")
    elif not any(step_files[i] is not None for i in step_files):
        st.error("❌ Загрузите хотя бы один скриншот.")
    else:
        guide_filename = PATH_TYPE_TO_GUIDE[path_type]
        guide = load_guide(guide_filename)
        st.session_state.current_guide_name = guide_filename

        uploaded_steps = [(i, step_files[i]) for i in range(1, st.session_state.num_steps + 1) if step_files[i] is not None]

        st.subheader("📸 Загруженные экраны")
        cols = st.columns(min(len(uploaded_steps), 5))
        for idx, (step_num, file) in enumerate(uploaded_steps):
            with cols[idx % 5]:
                st.image(file, caption=f"Шаг {step_num}", width=150)

        descriptions = []
        image_names = []
        progress_bar = st.progress(0, text="👁️ Анализирую шаги...")
        for idx, (step_num, file) in enumerate(uploaded_steps):
            progress_bar.progress((idx) / len(uploaded_steps), text=f"👁️ Шаг {step_num}: описание...")
            img_data = file.read()
            desc = analyze_image_locally(img_data)
            if desc is None:
                st.error(f"❌ Не удалось получить описание для шага {step_num}.")
                st.stop()
            descriptions.append(f"=== Шаг {step_num} ===\n{desc}")
            image_names.append(file.name)
        progress_bar.progress(1.0, text="✅ Все шаги описаны.")

        full_description = "\n\n".join(descriptions)

        with st.expander("📝 Полное описание клиентского пути"):
            st.write(full_description)

        with st.spinner("🔥 Провожу прожарку всего пути..."):
            report_raw = analyze_maket(full_description, guide, product_name, journey_goal, success_criteria)
            if report_raw is None:
                st.stop()
            st.subheader("📊 Результат прожарки")
            
            st.caption("Сырой ответ модели:")
            st.code(report_raw, language="json")
            
            items = parse_llm_json(report_raw)
            if items is None:
                st.warning("Не удалось извлечь JSON из ответа модели. Показан сырой текст выше.")

            hypotheses = []
            if items:
                with st.spinner("🧪 Генерирую A/B-гипотезы..."):
                    hypotheses = generate_hypotheses(items, product_name, journey_goal, success_criteria)

            st.session_state.current_description = full_description
            st.session_state.current_report_items = items
            st.session_state.current_report_raw = report_raw
            st.session_state.current_hypotheses = hypotheses
            st.session_state.current_image_name = ", ".join(image_names)
            st.session_state.current_product_name = product_name
            st.session_state.current_journey_goal = journey_goal
            st.session_state.current_success_criteria = success_criteria
            st.session_state.current_guide_name = guide_filename

            if items:
                display_report_items(items, hypotheses)

# Показываем кнопки сохранения и HTML-отчёта
show_save_and_export_buttons()