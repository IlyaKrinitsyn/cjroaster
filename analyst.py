from openai import OpenAI
import os
import sys
import json
import base64

# Клиент к локальному серверу LM Studio (тот же, что и раньше)
client = OpenAI(
    base_url="http://localhost:1234/v1",
    api_key="lm-studio"
)

MODEL_NAME = "qwen3.6-27b"  # Имя модели в LM Studio

def analyze_image_locally(image_path: str) -> str:
    """
    Отправляет скриншот в мультимодальную модель и получает
    его подробное текстовое описание для дальнейшего аудита.
    """
    if not os.path.exists(image_path):
        raise FileNotFoundError(f"❌ Файл изображения не найден: {image_path}")

    print(f"👁️ Отправляю скриншот в модель: {os.path.basename(image_path)}")

    # Кодируем изображение в base64
    with open(image_path, "rb") as f:
        img_bytes = f.read()
    img_b64 = base64.b64encode(img_bytes).decode("utf-8")

    # Системный запрос для получения детального описания макета
    system_prompt = (
        "Ты — ассистент для описания интерфейсов. "
        "Опиши макет, который ты видишь, максимально подробно, как если бы "
        "ты был ИИ-аудитором. Включи в описание: все видимые тексты (заголовки, "
        "кнопки, подписи к полям), цвета и контрастность кнопок и фона, "
        "наличие или отсутствие туториалов, индикаторов прогресса, гарантийных "
        "текстов, ссылок, эмоциональную окраску (спокойствие, уверенность, "
        "раздражение), наличие кнопок «Поделиться», «Оценить опыт», «Позвать человека»."
    )

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/png;base64,{img_b64}"
                            }
                        },
                        {
                            "type": "text",
                            "text": "Дай полное текстовое описание этого интерфейса."
                        }
                    ]
                }
            ],
            temperature=0.0,
            max_tokens=10000,
            extra_body={
                "reasoning": {"enabled": False}  # отключаем размышления
            }
        )
    except Exception as e:
        print(f"❌ Ошибка при обращении к модели: {e}")
        print("   Проверьте, что сервер LM Studio запущен и модель загружена.")
        sys.exit(1)

    description = response.choices[0].message.content
    if not description:
        print("⚠️ Модель вернула пустое описание.")
        return ""
    print(f"✅ Описание макета получено ({len(description)} символов)")
    return description.strip()

def analyze_maket(description: str) -> str:
    """
    Отправляет описание макета в модель и получает структурированный
    отчёт о несоответствиях гайду (та же функция, что и раньше).
    """
    guide_path = os.path.join(os.path.dirname(__file__), "guide.txt")
    if not os.path.exists(guide_path):
        raise FileNotFoundError(f"❌ Файл гайда не найден: {guide_path}")

    with open(guide_path, "r", encoding="utf-8") as f:
        guide = f.read()

    if not guide.strip():
        raise ValueError("❌ guide.txt пуст!")

    print(f"✅ Гайд загружен ({len(guide)} символов)")

    system_prompt = (
        "Ты — эксперт по аудиту клиентских путей. "
        "Отвечай сразу, без рассуждений. Только JSON."
    )

    user_prompt = f"""Гайд:
{guide}

Описание макета:
{description}

Найди ровно 3 главных несоответствия гайду. Ответ строго в формате JSON-массива."""

    print("⏳ Отправляю запрос в LM Studio...")

    try:
        response = client.chat.completions.create(
            model=MODEL_NAME,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0,
            max_tokens=10000,
            stop=None,
            extra_body={
                "reasoning": {"enabled": False}
            }
        )
    except Exception as e:
        print(f"❌ Ошибка при вызове API: {e}")
        sys.exit(1)

    content = response.choices[0].message.content
    if not content:
        print("⚠️ Модель вернула пустой ответ.")
        return ""

    print(f"✅ Модель вернула {len(content)} символов")
    return content.strip()

if __name__ == "__main__":
    # Определяем источник для анализа
    if len(sys.argv) > 1:
        image_path = sys.argv[1]
        print(f"🔍 Анализирую изображение: {image_path}")
        description = analyze_image_locally(image_path)
        if not description:
            sys.exit(1)
        print("\n--- ПОЛУЧЕННОЕ ОПИСАНИЕ МАКЕТА ---")
        print(description)
        print("--- КОНЕЦ ОПИСАНИЯ ---\n")
    elif os.path.exists("test_ui.png"):
        print("🔍 Нашёлся тестовый скриншот test_ui.png, анализирую его...")
        description = analyze_image_locally("test_ui.png")
        if not description:
            sys.exit(1)
        print("\n--- ПОЛУЧЕННОЕ ОПИСАНИЕ МАКЕТА ---")
        print(description)
        print("--- КОНЕЦ ОПИСАНИЯ ---\n")
    else:
        print("🔍 Изображение не указано, использую тестовое текстовое описание макета.")
        description = """
        Экран онбординга премиум-банка: заголовок "Добро пожаловать в мир привилегий". 
        Кнопка "Продолжить" бледно-серая на белом фоне. Форма только с одним полем "Email". 
        Никаких подсказок о дальнейших шагах. Нет ссылок на политику конфиденциальности.
        """

    # Запускаем прожарку
    report = analyze_maket(description)
    if report:
        print("\n--- РЕЗУЛЬТАТ ПРОЖАРКИ ---")
        try:
            parsed = json.loads(report)
            print(json.dumps(parsed, ensure_ascii=False, indent=2))
        except json.JSONDecodeError:
            print(report)
    else:
        print("❌ Не удалось получить рекомендации.")